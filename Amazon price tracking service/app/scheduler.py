"""優先式爬取排程（深入探討 1：Prioritized Crawling）。

盲爬 5 億商品 5 天才掃一輪 → 改成「誰重要誰先爬、爬得勤」：
  score    = W_SUB * subscription_count + W_VIEW * log1p(view_count) + W_SUS * suspicious_flag + priority_boost
  interval = clamp(BASE / (1 + score), MIN, MAX)        # 分數越高、間隔越短
  due      = now - last_seen_at >= interval              # extension 回報也算「看過」（last_seen_at）
每 tick 取 due 的商品，依 score * (age / interval) 排序取前 N 筆入 crawl queue。

enqueue_urgent()：訂閱新商品 / 手動爬 / 可疑回報 → 直接入列（不等 tick），同時設 flag，
job 若遺失下個 tick 仍會被撿回（fail-soft）。

正式版升級（附錄 C）：Redis ZSET 當 priority queue、多 crawler 以 consumer group 分工、
per-IP 的 token bucket 集中在 Redis。
"""
import asyncio
import math
from datetime import datetime, timedelta

from sqlalchemy import select

from .config import (BASE_CRAWL_INTERVAL_S, CRAWL_INFLIGHT_TTL_S, MAX_CRAWL_INTERVAL_S,
                     MIN_CRAWL_INTERVAL_S, PRIO_W_SUB, PRIO_W_SUSPICIOUS, PRIO_W_VIEW,
                     SCHEDULER_BATCH, SCHEDULER_TICK_S)
from .database import SessionLocal
from .errors import logger
from .models import Product
from .queue import queue


def score(p: Product) -> float:
    return (PRIO_W_SUB * p.subscription_count
            + PRIO_W_VIEW * math.log1p(p.view_count)
            + PRIO_W_SUSPICIOUS * p.suspicious_flag
            + p.priority_boost)


def interval_s(p: Product) -> float:
    base = BASE_CRAWL_INTERVAL_S / (1.0 + score(p))
    if p.last_crawl_status == "captcha":
        base *= 4  # 被擋 → 退避
    if p.status in ("not_found",):
        base = MAX_CRAWL_INTERVAL_S
    return max(MIN_CRAWL_INTERVAL_S, min(MAX_CRAWL_INTERVAL_S, base))


def age_s(p: Product, now: datetime | None = None) -> float:
    now = now or datetime.utcnow()
    if not p.last_seen_at:
        return float(10 ** 9)  # 從沒看過 → 最老
    return (now - p.last_seen_at).total_seconds()


def due_in_s(p: Product, now: datetime | None = None) -> float:
    return max(0.0, interval_s(p) - age_s(p, now))


def _inflight(p: Product, now: datetime) -> bool:
    return bool(p.crawl_inflight_at) and (now - p.crawl_inflight_at) < timedelta(seconds=CRAWL_INFLIGHT_TTL_S)


async def enqueue_urgent(asin: str, reason: str, report_id: int | None = None, boost: float = 50.0) -> bool:
    """直接入列（不等 tick）。回傳是否真的入列（inflight 中則不重複）。"""
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if not p:
            return False
        now = datetime.utcnow()
        p.priority_boost = max(p.priority_boost, boost)
        if reason == "verify_report":
            p.suspicious_flag = 1
        if _inflight(p, now):
            db.commit()
            return False
        p.crawl_inflight_at = now
        db.commit()
    finally:
        db.close()
    await queue.enqueue("crawl", {"product_id": asin, "reason": reason, "report_id": report_id})
    return True


async def tick() -> int:
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        products = db.execute(select(Product)).scalars().all()
        due = []
        for p in products:
            if _inflight(p, now):
                continue
            a, i = age_s(p, now), interval_s(p)
            if a >= i:
                due.append((score(p) * (a / i), p))
        due.sort(key=lambda x: x[0], reverse=True)
        picked = [p for _, p in due[:SCHEDULER_BATCH]]
        for p in picked:
            p.crawl_inflight_at = now
        db.commit()
        asins = [p.asin for p in picked]
    finally:
        db.close()

    for asin in asins:
        await queue.enqueue("crawl", {"product_id": asin, "reason": "scheduled", "report_id": None})
    if asins:
        logger.info("scheduler tick: due=%d enqueued=%s", len(due), asins)
    return len(asins)


async def run_scheduler() -> None:
    logger.info("scheduler started (tick=%ss batch=%d)", SCHEDULER_TICK_S, SCHEDULER_BATCH)
    while True:
        try:
            await asyncio.sleep(SCHEDULER_TICK_S)
            await tick()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.exception("scheduler error: %s", e)
