"""商品目錄 + 優先訊號（深入探討 1：優先式爬取的資料來源）。

商品在「第一次訂閱 / 第一次 extension 回報 / 手動 track」時註冊。每個動作更新對應訊號：
- 訂閱 → subscription_count（最強訊號：有人在等這個價格）
- 查價格歷史 → view_count（有人在看）
- 可疑回報 → suspicious_flag（要最高優先重爬驗證）
scheduler.py 用這些欄位算優先分數。
"""
from datetime import datetime

from .database import SessionLocal
from .models import Product


def ensure_product(asin: str, title: str | None = None, db=None) -> Product:
    own = db is None
    db = db or SessionLocal()
    try:
        p = db.get(Product, asin)
        if not p:
            p = Product(asin=asin, title=title)
            db.add(p)
            db.commit()
            db.refresh(p)
        elif title and not p.title:
            p.title = title
            db.commit()
        return p
    finally:
        if own:
            db.close()


def bump_view(asin: str) -> None:
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if p:
            p.view_count += 1
            db.commit()
    finally:
        db.close()


def bump_subscriptions(asin: str, delta: int) -> None:
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if p:
            p.subscription_count = max(0, p.subscription_count + delta)
            db.commit()
    finally:
        db.close()


def record_seen(asin: str, price: float, source: str, currency: str | None = None, title: str | None = None) -> None:
    """寫入端：更新最近已知價格（crawler 或被接受的 extension 回報都會呼叫）。"""
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if not p:
            return
        now = datetime.utcnow()
        p.last_price = price
        p.last_price_at = now
        p.last_price_source = source
        p.last_seen_at = now
        if currency:
            p.currency = currency
        if title and not p.title:
            p.title = title
        db.commit()
    finally:
        db.close()


def record_crawl(asin: str, status: str, fetcher: str | None, error: str | None = None,
                 title: str | None = None, currency: str | None = None) -> None:
    """爬取結束：更新爬取狀態、清掉 inflight / suspicious / boost（這次爬取已消化這些訊號）。"""
    db = SessionLocal()
    try:
        p = db.get(Product, asin)
        if not p:
            return
        now = datetime.utcnow()
        p.last_crawled_at = now
        p.last_crawl_status = status
        p.last_fetcher = fetcher
        p.last_error = (error or "")[:512] or None
        p.crawl_inflight_at = None
        if status == "ok":
            p.last_seen_at = now
            p.suspicious_flag = 0
            p.priority_boost = 0.0
            p.status = "active"
            if title:
                p.title = title
            if currency:
                p.currency = currency
        elif status in ("unavailable", "region_locked"):
            p.last_seen_at = now
            p.suspicious_flag = 0
            p.priority_boost = 0.0
            p.status = status
            if title:
                p.title = title
        elif status == "not_found":
            p.last_seen_at = now
            p.status = "not_found"
        elif status == "captcha":
            p.status = "blocked"
        db.commit()
    finally:
        db.close()


def to_dict(p: Product) -> dict:
    return {
        "asin": p.asin, "title": p.title, "currency": p.currency, "status": p.status,
        "last_price": p.last_price,
        "last_price_at": p.last_price_at.isoformat() if p.last_price_at else None,
        "last_price_source": p.last_price_source,
        "last_crawled_at": p.last_crawled_at.isoformat() if p.last_crawled_at else None,
        "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
        "last_crawl_status": p.last_crawl_status, "last_fetcher": p.last_fetcher, "last_error": p.last_error,
        "crawl_inflight": p.crawl_inflight_at is not None,
        "subscription_count": p.subscription_count, "view_count": p.view_count,
        "suspicious_flag": p.suspicious_flag, "priority_boost": p.priority_boost,
        "last_event_price": p.last_event_price, "last_event_seq": p.last_event_seq,
    }
