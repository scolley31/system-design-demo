"""Crawl worker：消費 `crawl` queue → 抓 Amazon → 寫 price 表 → 結案待驗證的回報。

只有 crawler 的結果（或非可疑的 extension 回報）會進 price 表；price 表的每一筆 append
再經 CDC 變成 price_changed 事件 → 通知。可疑回報「不入庫、先重爬」的完整性驗證就在這裡結案：
  |crawler 價 - 回報價| / crawler 價 ≤ REPORT_TOLERANCE_PCT → confirmed，否則 rejected。
"""
import asyncio
import time
from datetime import datetime

from sqlalchemy import select

from .catalog import record_crawl, record_seen
from .config import MOCK_AMAZON, REPORT_TOLERANCE_PCT
from .crawler.fetcher import BlockedError, FetchError, NotFoundError, fetcher
from .database import SessionLocal
from .errors import logger
from .models import CrawlLog, PriceReport
from .price_store import price_store
from .queue import queue


def _resolve_reports(asin: str, verify_price: float | None) -> None:
    db = SessionLocal()
    try:
        pending = db.execute(
            select(PriceReport).where(PriceReport.product_id == asin, PriceReport.status == "pending_verify")
        ).scalars().all()
        now = datetime.utcnow()
        for r in pending:
            r.verify_price = verify_price
            r.resolved_at = now
            if verify_price is None:
                r.status = "rejected"
            else:
                r.status = "confirmed" if abs(verify_price - r.reported_price) / verify_price <= REPORT_TOLERANCE_PCT else "rejected"
        db.commit()
    finally:
        db.close()


def _log(asin: str, reason: str, fetcher_name: str | None, status: str, price: float | None,
         duration_ms: int, error: str | None = None) -> None:
    db = SessionLocal()
    try:
        db.add(CrawlLog(product_id=asin, reason=reason, fetcher=fetcher_name, status=status,
                        price=price, duration_ms=duration_ms, error=(error or "")[:512] or None))
        db.commit()
    finally:
        db.close()


async def crawl_one(asin: str, reason: str = "manual") -> dict:
    t0 = time.monotonic()
    fetcher_name, status, price, err = None, "http_error", None, None
    try:
        snap = await fetcher.fetch_product(asin)
        fetcher_name = snap.fetcher
        parsed = snap.parsed
        if parsed.price is not None:
            status, price = "ok", parsed.price
            source = "mock" if MOCK_AMAZON else "crawler"
            seq = price_store.append(asin, price, parsed.currency, source=source, fetcher=fetcher_name)
            record_seen(asin, price, source, parsed.currency, parsed.title)
            record_crawl(asin, "ok", fetcher_name, title=parsed.title, currency=parsed.currency)
            logger.info("crawl %s [%s] via %s → %s %.2f (seq=%s)", asin, reason, fetcher_name, parsed.currency, price, seq)
        elif parsed.region_locked:
            status, err = "region_locked", "此 IP 看不到 buybox（無法配送到此地區）；crawler 需美國出口 IP"
            record_crawl(asin, "region_locked", fetcher_name, error=err, title=parsed.title)
        elif not parsed.available:
            status = "unavailable"
            record_crawl(asin, "unavailable", fetcher_name, title=parsed.title)
        else:
            status, err = "parse_error", "有標題但找不到價格"
            record_crawl(asin, "parse_error", fetcher_name, error=err, title=parsed.title)
    except NotFoundError:
        status, err = "not_found", "404"
        record_crawl(asin, "not_found", fetcher_name, error=err)
    except BlockedError as e:
        status, err = "captcha", str(e)
        record_crawl(asin, "captcha", fetcher_name, error=err)
        logger.warning("crawl %s blocked: %s", asin, e)
    except FetchError as e:
        status, err = "http_error", str(e)
        record_crawl(asin, "http_error", fetcher_name, error=err)
        logger.warning("crawl %s fetch error: %s", asin, e)

    _resolve_reports(asin, price)
    dur = int((time.monotonic() - t0) * 1000)
    _log(asin, reason, fetcher_name, status, price, dur, err)
    return {"product_id": asin, "status": status, "price": price, "fetcher": fetcher_name, "duration_ms": dur, "error": err}


async def run_crawl_worker() -> None:
    logger.info("crawl worker started")
    while True:
        try:
            job = await queue.dequeue("crawl")
            await crawl_one(job["product_id"], job.get("reason", "scheduled"))
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001 — 單一 job 失敗不可拖垮 worker
            logger.exception("crawl worker error: %s", e)
