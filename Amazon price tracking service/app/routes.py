"""API 路由（對齊 PDF API design，加 /api/v1）。"""
import asyncio
import json
import random
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import aggregation, catalog, reports, scheduler
from .cdc import tailer
from .config import ALLOW_SEED, MOCK_AMAZON, public_meta
from .crawler.fetcher import fetcher
from .database import SessionLocal
from .models import CrawlLog, NotificationOutbox, Product, Subscription
from .price_store import price_store
from .queue import CHANNELS, queue
from .realtime import hub
from .schemas import (MockPriceRequest, PriceReportRequest, SeedRequest, SubscriptionRequest,
                      TrackRequest, extract_asin)
from .worker_crawl import crawl_one

router = APIRouter(prefix="/api/v1")


def _err(status: int, msg: str):
    return JSONResponse(status_code=status, content={"detail": msg})


@router.get("/config/meta")
def config_meta():
    return public_meta()


# ---------- PDF API 1：價格歷史 ----------
@router.get("/price/{product_id}")
def price_history(product_id: str, period: str = "30d", granularity: str | None = None):
    try:
        asin = extract_asin(product_id)
        res = aggregation.query_history(asin, period, granularity)
    except ValueError as e:
        return _err(422, str(e))
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if not p:
            return _err(404, "product not tracked")
        res.update({"title": p.title, "currency": p.currency, "current_price": p.last_price,
                    "current_price_at": p.last_price_at.isoformat() if p.last_price_at else None})
    finally:
        db.close()
    catalog.bump_view(asin)  # 優先訊號：有人在看
    return res


# ---------- PDF API 2：訂閱 ----------
@router.post("/subscriptions")
async def create_subscription(req: SubscriptionRequest):
    if req.notification_type == "email" and not req.email:
        return _err(422, "notification_type=email 需提供 email")
    p = catalog.ensure_product(req.product_id)
    db = SessionLocal()
    created = False
    try:
        s = db.execute(select(Subscription).where(
            Subscription.product_id == req.product_id, Subscription.user_id == req.user_id)).scalar_one_or_none()
        if s:
            s.price_threshold = req.price_threshold
            s.notification_type = req.notification_type
            s.email = req.email
            s.status = "active"
            s.last_notified_price = None  # 改門檻 → 重新計算 cooldown
        else:
            s = Subscription(subscription_id=uuid.uuid4().hex, user_id=req.user_id, product_id=req.product_id,
                             price_threshold=req.price_threshold, notification_type=req.notification_type,
                             email=req.email)
            db.add(s)
            created = True
        db.commit()
        out = _sub_dict(s)
    finally:
        db.close()
    if created:
        catalog.bump_subscriptions(req.product_id, +1)  # 優先訊號：有人在等
    if p.last_price is None:
        await scheduler.enqueue_urgent(req.product_id, "track")
    return out


def _sub_dict(s: Subscription) -> dict:
    return {"subscription_id": s.subscription_id, "user_id": s.user_id, "product_id": s.product_id,
            "price_threshold": s.price_threshold, "notification_type": s.notification_type, "email": s.email,
            "status": s.status, "last_notified_price": s.last_notified_price,
            "last_notified_at": s.last_notified_at.isoformat() if s.last_notified_at else None}


@router.get("/subscriptions")
def list_subscriptions(user_id: str):
    db = SessionLocal()
    try:
        rows = db.execute(select(Subscription).where(Subscription.user_id == user_id)
                          .order_by(Subscription.created_at.desc())).scalars().all()
        return [_sub_dict(s) for s in rows]
    finally:
        db.close()


@router.delete("/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: str):
    db = SessionLocal()
    try:
        s = db.get(Subscription, subscription_id)
        if not s:
            return _err(404, "not found")
        asin = s.product_id
        db.delete(s)
        db.commit()
    finally:
        db.close()
    catalog.bump_subscriptions(asin, -1)
    return {"deleted": True}


# ---------- extension 眾包回報 ----------
@router.post("/price-reports")
async def price_report(req: PriceReportRequest):
    try:
        return await reports.ingest(req)
    except reports.RateLimited as e:
        return _err(429, str(e))


@router.get("/price-reports")
def list_price_reports(product_id: str | None = None, limit: int = Query(30, le=200)):
    asin = extract_asin(product_id) if product_id else None
    return reports.list_reports(asin, limit)


# ---------- catalog / crawler ----------
@router.post("/products/track")
async def track_product(req: TrackRequest):
    p = catalog.ensure_product(req.product_id)
    enq = await scheduler.enqueue_urgent(req.product_id, "track")
    return {**catalog.to_dict(p), "crawl_enqueued": enq}


@router.get("/products")
def list_products():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        rows = db.execute(select(Product).order_by(Product.created_at)).scalars().all()
        out = []
        for p in rows:
            d = catalog.to_dict(p)
            d.update({"score": round(scheduler.score(p), 2), "interval_s": int(scheduler.interval_s(p)),
                      "due_in_s": int(scheduler.due_in_s(p, now))})
            out.append(d)
        return out
    finally:
        db.close()


@router.get("/products/{product_id}")
def get_product(product_id: str):
    asin = extract_asin(product_id)
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if not p:
            return _err(404, "product not tracked")
        return catalog.to_dict(p)
    finally:
        db.close()


@router.post("/products/{product_id}/crawl")
async def crawl_now(product_id: str, sync: bool = False):
    asin = extract_asin(product_id)
    catalog.ensure_product(asin)
    if sync:  # 測試用：同步等結果
        return await crawl_one(asin, "manual")
    enq = await scheduler.enqueue_urgent(asin, "manual", boost=100.0)
    return {"product_id": asin, "enqueued": enq, "queue_depth": queue.depth("crawl")}


@router.get("/crawl/log")
def crawl_log(limit: int = Query(50, le=500), product_id: str | None = None):
    db = SessionLocal()
    try:
        q = select(CrawlLog)
        if product_id:
            q = q.where(CrawlLog.product_id == extract_asin(product_id))
        rows = db.execute(q.order_by(CrawlLog.id.desc()).limit(limit)).scalars().all()
        return [{"id": r.id, "product_id": r.product_id, "reason": r.reason, "fetcher": r.fetcher, "status": r.status,
                 "price": r.price, "duration_ms": r.duration_ms, "error": r.error, "created_at": r.created_at.isoformat()}
                for r in rows]
    finally:
        db.close()


# ---------- 彙總 ----------
@router.post("/aggregations/run")
async def run_aggregation(product_id: str | None = None):
    if product_id:
        asin = extract_asin(product_id)
        n = await asyncio.to_thread(aggregation.aggregate_product, asin)
        return {"products": 1, "buckets_upserted": n}
    return await asyncio.to_thread(aggregation.aggregate_all, False)


@router.get("/aggregations/stats")
def aggregation_stats(product_id: str):
    return aggregation.stats(extract_asin(product_id))


# ---------- 即時 / outbox / 維運 ----------
@router.get("/stream")
async def stream(user_id: str, request: Request):
    """SSE：使用者訂閱，收到符合門檻的降價即時推播。"""
    q = hub.subscribe(user_id)

    async def gen():
        try:
            yield {"event": "connected", "data": json.dumps({"user_id": user_id})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield {"event": "price_drop", "data": json.dumps(data)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            hub.unsubscribe(user_id, q)

    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(gen())


@router.get("/notifications")
def notifications(user_id: str, limit: int = Query(50, le=500)):
    db = SessionLocal()
    try:
        rows = db.execute(select(NotificationOutbox).where(NotificationOutbox.user_id == user_id)
                          .order_by(NotificationOutbox.created_at.desc()).limit(limit)).scalars().all()
        return [{"notification_id": r.notification_id, "product_id": r.product_id, "event_seq": r.event_seq,
                 "old_price": r.old_price, "new_price": r.new_price, "threshold": r.threshold,
                 "channel": r.channel, "status": r.status, "updated_at": r.updated_at.isoformat()} for r in rows]
    finally:
        db.close()


@router.get("/cdc/status")
def cdc_status():
    return {**tailer.status(), "queues": {c: queue.depth(c) for c in CHANNELS}}


# ---------- debug（MOCK_AMAZON / ALLOW_SEED 才開）----------
@router.post("/debug/mock-price")
def set_mock_price(req: MockPriceRequest):
    if not MOCK_AMAZON or not fetcher.mock:
        return _err(404, "MOCK_AMAZON 未開啟")
    fetcher.mock.set_price(req.product_id, req.price)
    return {"product_id": req.product_id, "mock_price": req.price}


@router.post("/debug/seed")
async def seed_history(req: SeedRequest):
    """回填假歷史（隨機漫步），並把 CDC checkpoint 推過回填列 → 回填不觸發通知。"""
    if not ALLOW_SEED:
        return _err(404, "需 MOCK_AMAZON=1 或 ALLOW_SEED=1")
    p = catalog.ensure_product(req.product_id)
    base = req.base_price or p.last_price or (fetcher.mock.current(req.product_id) if fetcher.mock else 100.0)

    def _gen():
        rows, price = [], base * random.uniform(0.9, 1.3)
        start = datetime.utcnow() - timedelta(days=req.days)
        step = timedelta(hours=24 / req.per_day)
        total = req.days * req.per_day
        for i in range(total):
            price = max(1.0, price * random.uniform(0.985, 1.015))
            if random.random() < 0.01:
                price *= random.uniform(0.7, 1.3)  # 偶爾跳價
            rows.append({"product_id": req.product_id, "price": round(price, 2), "currency": p.currency,
                         "source": "seed", "ts": start + step * i})
        return price_store.bulk_append(rows), total

    (mn, mx), total = await asyncio.to_thread(_gen)
    tailer.advance_to(mx)
    return {"product_id": req.product_id, "rows": total, "seq_range": [mn, mx], "cdc_checkpoint": mx,
            "note": "checkpoint 已推過回填列，不會觸發通知"}
