"""價格事件 → 通知（深入探討 2 的 consumer 端）。

price-change worker（消費 price_changed）：
1. prev = product.last_event_price（消費端自己記的上一個事件價，與寫入端分離）。
2. 查訂閱：`WHERE product_id = :pid AND status='active' AND price_threshold >= :new_price`
   （走 (product_id, price_threshold, user_id) 索引 = PDF 的 secondary key；本質是 event stream ⋈ DB）。
3. 過濾小波動：|Δ| < MIN_CHANGE_PCT 且沒有訂閱「這次才跨過門檻」→ 不通知（dual-write 論點裡的
   「過濾/合併」，在 CDC 架構下改放 consumer 端做）。
4. 每個訂閱：cooldown（上次已通知過且這次沒有更低 → SKIPPED_COOLDOWN）→ outbox 冪等 gate
   （notification_id = hash(subscription_id|event_seq)）→ 寫 ENQUEUED → 丟 notify:{type}。
5. 價格回升到門檻之上的訂閱 → 重設 last_notified_price（下次再跌破會再通知）。

sender（消費 notify:*）：抄 Earthquake worker：terminal 檢查 → ATTEMPTED → 送 → VENDOR_ACCEPTED/FAILED。
"""
import asyncio
import hashlib
from datetime import datetime

from sqlalchemy import select

from .channels import build_sender
from .config import MIN_CHANGE_PCT
from .database import SessionLocal
from .errors import logger
from .models import NotificationOutbox, Product, Subscription
from .queue import queue
from .worker_crawl import run_crawl_worker

TERMINAL = {"ATTEMPTED", "VENDOR_ACCEPTED", "FAILED"}


def notification_id(subscription_id: str, event_seq: int) -> str:
    return hashlib.sha256(f"{subscription_id}|{event_seq}".encode()).hexdigest()[:32]


async def handle_price_event(ev: dict) -> dict:
    asin, new, seq = ev["product_id"], float(ev["price"]), int(ev["seq"])
    db = SessionLocal()
    enqueued = skipped = 0
    try:
        p = db.get(Product, asin)
        if not p:
            return {"ignored": "unknown product"}
        if p.last_event_seq is not None and seq <= p.last_event_seq:
            return {"ignored": "replayed event"}  # at-least-once 的重播
        prev = p.last_event_price
        change_pct = abs(new - prev) / prev if prev else None

        subs = db.execute(
            select(Subscription).where(
                Subscription.product_id == asin,
                Subscription.status == "active",
                Subscription.price_threshold >= new,
            )
        ).scalars().all()
        crossing = any(prev is None or prev > s.price_threshold for s in subs)

        if change_pct is not None and change_pct < MIN_CHANGE_PCT and not crossing:
            p.last_event_price, p.last_event_seq = new, seq
            db.commit()
            logger.info("price event %s seq=%s %.2f→%.2f filtered (Δ=%.2f%% < %.0f%%, no crossing)",
                        asin, seq, prev, new, change_pct * 100, MIN_CHANGE_PCT * 100)
            return {"filtered": True, "change_pct": change_pct}

        jobs = []
        for s in subs:
            nid = notification_id(s.subscription_id, seq)
            if s.last_notified_price is not None and new >= s.last_notified_price:
                # cooldown：已通知過更低（或相同）的價格 → 不重複吵使用者
                if not db.get(NotificationOutbox, nid):
                    db.add(NotificationOutbox(
                        notification_id=nid, subscription_id=s.subscription_id, user_id=s.user_id,
                        product_id=asin, event_seq=seq, old_price=prev, new_price=new,
                        threshold=s.price_threshold, channel=s.notification_type, status="SKIPPED_COOLDOWN"))
                skipped += 1
                continue
            existing = db.get(NotificationOutbox, nid)
            if existing and existing.status != "FAILED":
                continue  # 冪等：已排/已送
            if existing:
                existing.status = "ENQUEUED"
            else:
                db.add(NotificationOutbox(
                    notification_id=nid, subscription_id=s.subscription_id, user_id=s.user_id,
                    product_id=asin, event_seq=seq, old_price=prev, new_price=new,
                    threshold=s.price_threshold, channel=s.notification_type, status="ENQUEUED"))
            jobs.append({
                "notification_id": nid, "subscription_id": s.subscription_id, "user_id": s.user_id,
                "email": s.email, "channel": f"notify:{s.notification_type}",
                "payload": {
                    "product_id": asin, "title": p.title, "currency": ev.get("currency", p.currency),
                    "old_price": prev, "new_price": new, "threshold": s.price_threshold,
                    "event_seq": seq, "source": ev.get("source"), "ts": ev.get("ts"),
                },
            })

        # 價格回到門檻之上的訂閱 → 重設 cooldown
        db.query(Subscription).filter(
            Subscription.product_id == asin, Subscription.price_threshold < new,
            Subscription.last_notified_price.isnot(None),
        ).update({"last_notified_price": None}, synchronize_session=False)

        p.last_event_price, p.last_event_seq = new, seq
        db.commit()
    finally:
        db.close()

    for j in jobs:
        await queue.enqueue(j["channel"], j)
        enqueued += 1
    logger.info("price event %s seq=%s %s→%.2f: subs=%d enqueued=%d cooldown=%d",
                asin, seq, f"{prev:.2f}" if prev else "—", new, len(subs), enqueued, skipped)
    return {"enqueued": enqueued, "skipped_cooldown": skipped, "change_pct": change_pct}


async def run_price_change_worker() -> None:
    logger.info("price-change worker started")
    while True:
        try:
            ev = await queue.dequeue("price_changed")
            await handle_price_event(ev)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.exception("price-change worker error: %s", e)


async def _deliver(channel: str, sender, job: dict) -> None:
    db = SessionLocal()
    try:
        nid = job["notification_id"]
        rec = db.get(NotificationOutbox, nid)
        if rec and rec.status in TERMINAL:
            return  # 冪等：已終態，不重送
        if rec:
            rec.status = "ATTEMPTED"
            db.commit()
        delivered = await sender.send(job)
        if rec:
            rec.status = "VENDOR_ACCEPTED" if delivered else "FAILED"
            db.commit()
        if delivered:
            s = db.get(Subscription, job["subscription_id"])
            if s:
                s.last_notified_price = job["payload"]["new_price"]
                s.last_notified_at = datetime.utcnow()
                db.commit()
        logger.info("%s %s → user=%s %s %.2f", "delivered" if delivered else "not-delivered",
                    channel, job["user_id"], job["payload"]["product_id"], job["payload"]["new_price"])
    finally:
        db.close()


async def run_sender(channel: str) -> None:
    sender = build_sender(channel)
    logger.info("sender started (channel=%s via %s)", channel, sender.name)
    while True:
        try:
            job = await queue.dequeue(channel)
            await _deliver(channel, sender, job)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.exception("sender error (%s): %s", channel, e)


def start_workers() -> list[asyncio.Task]:
    return [
        asyncio.create_task(run_price_change_worker()),
        asyncio.create_task(run_sender("notify:sse")),
        asyncio.create_task(run_sender("notify:email")),
        asyncio.create_task(run_crawl_worker()),
    ]
