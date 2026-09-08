"""預先彙總（深入探討 3：< 500ms 的價格歷史圖表）。

熱門商品每小時一筆 → 2 年 17,520 列；每次畫圖都 date_trunc + avg 撐不住。改成：
- 背景 job 把 raw 依 daily / weekly / monthly 彙總進 price_aggregations（PK=(product_id, granularity, bucket_start)）。
- API 依 period 選粒度：≤90d daily、≤1y weekly、>1y monthly → 30 天圖只讀 30 列、2 年圖只讀 24 列。
- 新商品還沒彙總 → raw fallback（現算），回應標 source="raw_fallback"，圖照樣能畫。

新鮮度：彙總落後最多一個 AGG_INTERVAL_S（原型 5 分鐘；正式版夜間 job 落後 ≤ 24h，PDF 認為可接受，
因為圖表看的是趨勢，即時價另外從 products.last_price 拿）。
附錄 F 討論 OLAP/TSDB（ClickHouse / InfluxDB）替代：OLTP → CDC → stream → OLAP。
"""
import asyncio
import re
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .config import AGG_INTERVAL_S
from .database import SessionLocal
from .errors import logger
from .models import PriceAggregation, Product
from .price_store import price_store

GRANULARITIES = ("daily", "weekly", "monthly")
_PERIOD_RE = re.compile(r"^(\d+)([dwmy])$")


def parse_period(period: str) -> int:
    """'30d' / '12w' / '6m' / '2y' → 天數。"""
    m = _PERIOD_RE.match((period or "30d").lower())
    if not m:
        raise ValueError("period 格式：<n>d|w|m|y，如 30d、2y")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"d": 1, "w": 7, "m": 30, "y": 365}[unit]


def pick_granularity(days: int) -> str:
    if days <= 90:
        return "daily"
    if days <= 366:
        return "weekly"
    return "monthly"


def bucket_start(ts: datetime, gran: str) -> datetime:
    d = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if gran == "daily":
        return d
    if gran == "weekly":
        return d - timedelta(days=d.weekday())  # 週一
    return d.replace(day=1)


def _bucketize(rows: list[tuple[datetime, float]], gran: str) -> dict[datetime, dict]:
    out: dict[datetime, dict] = {}
    for ts, price in rows:  # rows 已依 ts 排序 → 最後一筆即 close
        b = bucket_start(ts, gran)
        s = out.get(b)
        if s is None:
            out[b] = {"sum": price, "min": price, "max": price, "close": price, "n": 1}
        else:
            s["sum"] += price
            s["min"] = min(s["min"], price)
            s["max"] = max(s["max"], price)
            s["close"] = price
            s["n"] += 1
    return out


def aggregate_product(asin: str) -> int:
    """全量重算該商品三種粒度（原型；正式版 incremental 只重算被新資料碰到的 bucket）。回傳 upsert 數。"""
    rows = price_store.query_range(asin, datetime(1970, 1, 1))
    if not rows:
        return 0
    db = SessionLocal()
    n = 0
    try:
        for gran in GRANULARITIES:
            for b, s in _bucketize(rows, gran).items():
                db.merge(PriceAggregation(
                    product_id=asin, granularity=gran, bucket_start=b,
                    avg_price=round(s["sum"] / s["n"], 4), min_price=s["min"], max_price=s["max"],
                    close_price=s["close"], sample_count=s["n"], updated_at=datetime.utcnow()))
                n += 1
        db.commit()
    finally:
        db.close()
    return n


def aggregate_all(only_stale: bool = True) -> dict:
    t0 = time.monotonic()
    db = SessionLocal()
    try:
        products = db.execute(select(Product)).scalars().all()
        last_agg = dict(db.execute(
            select(PriceAggregation.product_id, func.max(PriceAggregation.updated_at)).group_by(PriceAggregation.product_id)
        ).all())
        targets = [p.asin for p in products
                   if p.last_price_at and (not only_stale or last_agg.get(p.asin) is None or p.last_price_at > last_agg[p.asin])]
    finally:
        db.close()
    total = sum(aggregate_product(a) for a in targets)
    return {"products": len(targets), "buckets_upserted": total, "duration_ms": int((time.monotonic() - t0) * 1000)}


async def run_aggregator() -> None:
    logger.info("aggregator started (every %ss)", AGG_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(AGG_INTERVAL_S)
            res = await asyncio.to_thread(aggregate_all, True)
            if res["products"]:
                logger.info("aggregation: %s", res)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.exception("aggregator error: %s", e)


def stats(asin: str) -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(PriceAggregation.granularity, func.count(), func.max(PriceAggregation.updated_at))
            .where(PriceAggregation.product_id == asin).group_by(PriceAggregation.granularity)
        ).all()
        agg = {g: 0 for g in GRANULARITIES}
        last = None
        for g, c, u in rows:
            agg[g] = c
            last = max(last, u) if last else u
        return {"product_id": asin, "raw_rows": price_store.count_all(asin), "agg_rows": agg,
                "last_aggregated_at": last.isoformat() if last else None}
    finally:
        db.close()


def query_history(asin: str, period: str = "30d", granularity: str | None = None) -> dict:
    days = parse_period(period)
    gran = granularity if granularity in GRANULARITIES else pick_granularity(days)
    since = datetime.utcnow() - timedelta(days=days)
    b_since = bucket_start(since, gran)

    t0 = time.perf_counter()
    db = SessionLocal()
    try:
        rows = db.execute(
            select(PriceAggregation).where(
                PriceAggregation.product_id == asin, PriceAggregation.granularity == gran,
                PriceAggregation.bucket_start >= b_since,
            ).order_by(PriceAggregation.bucket_start)
        ).scalars().all()
    finally:
        db.close()

    if rows:
        points = [{"t": r.bucket_start.isoformat(), "avg": r.avg_price, "min": r.min_price, "max": r.max_price,
                   "close": r.close_price, "n": r.sample_count} for r in rows]
        source, scanned = "aggregation", len(rows)
    else:
        raw = price_store.query_range(asin, since)
        points = [{"t": b.isoformat(), "avg": round(s["sum"] / s["n"], 4), "min": s["min"], "max": s["max"],
                   "close": s["close"], "n": s["n"]} for b, s in sorted(_bucketize(raw, gran).items())]
        source, scanned = "raw_fallback", len(raw)
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)

    # demo 用的對照數字（不計入 latency）：這段期間 raw 有幾列
    raw_rows = price_store.count_range(asin, since)
    return {"product_id": asin, "period": period, "days": days, "granularity": gran, "source": source,
            "points": points, "rows_scanned": scanned, "raw_rows_in_period": raw_rows, "latency_ms": latency_ms}
