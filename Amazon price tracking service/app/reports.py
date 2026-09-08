"""extension 回報（深入探討 1：browser extension 當分散式資料蒐集 + 完整性驗證）。

使用者裝了 extension 逛 Amazon → extension 把 (product_id, 看到的價格) 回報。這天然涵蓋
「有人在意」的熱門商品，crawler 只需補近期沒人看的。

匿名：只帶 reporter_token（每分頁隨機、只用來限流），不綁 user_id，不記瀏覽紀錄。

完整性驗證（不能直接信使用者上傳的資料）：
- baseline = 商品最近已知價；deviation = (baseline - reported) / baseline
- deviation > SUSPICIOUS_DROP_PCT（或根本沒 baseline）→ pending_verify：**不寫 price 表**（不會觸發通知），
  改把該商品以最高優先送進 crawl queue；crawler 結果才是真相（worker_crawl 結案 confirmed/rejected）。
- 否則 accepted：直接 append(source=extension)，走正常 CDC → 通知，並更新 last_seen_at（crawler 可以晚點再來）。

限流：每個 reporter_token 每分鐘 REPORT_RATE_PER_MIN 次（原型記憶體 deque；正式版 Redis INCR + EXPIRE）。
"""
import time
from collections import defaultdict, deque

from sqlalchemy import select

from .catalog import ensure_product, record_seen
from .config import REPORT_RATE_PER_MIN, SUSPICIOUS_DROP_PCT
from .database import SessionLocal
from .models import PriceReport
from .price_store import price_store
from .scheduler import enqueue_urgent
from .schemas import PriceReportRequest

_buckets: dict[str, deque] = defaultdict(deque)


class RateLimited(Exception):
    pass


def _check_rate(token: str) -> None:
    now = time.time()
    q = _buckets[token]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= REPORT_RATE_PER_MIN:
        raise RateLimited(f"reporter {token} 超過 {REPORT_RATE_PER_MIN}/min")
    q.append(now)


async def ingest(req: PriceReportRequest) -> dict:
    _check_rate(req.reporter_token)
    p = ensure_product(req.product_id, req.title)
    baseline = p.last_price
    deviation = (baseline - req.price) / baseline if baseline else None
    suspicious = baseline is None or (deviation is not None and deviation > SUSPICIOUS_DROP_PCT)

    db = SessionLocal()
    try:
        r = PriceReport(product_id=req.product_id, reported_price=req.price, currency=req.currency,
                        reporter_token=req.reporter_token, baseline_price=baseline, deviation_pct=deviation,
                        status="pending_verify" if suspicious else "accepted")
        db.add(r)
        db.commit()
        report_id = r.report_id
    finally:
        db.close()

    recrawl = False
    if suspicious:
        recrawl = await enqueue_urgent(req.product_id, "verify_report", report_id=report_id, boost=100.0)
    else:
        price_store.append(req.product_id, req.price, req.currency, source="extension")
        record_seen(req.product_id, req.price, "extension", req.currency, req.title)

    return {"report_id": report_id, "status": "pending_verify" if suspicious else "accepted",
            "baseline_price": baseline, "deviation_pct": deviation, "recrawl_enqueued": recrawl,
            "reason": ("no baseline yet" if baseline is None else
                       f"drop {deviation * 100:.1f}% > {SUSPICIOUS_DROP_PCT * 100:.0f}%") if suspicious else None}


def list_reports(product_id: str | None, limit: int = 30) -> list[dict]:
    db = SessionLocal()
    try:
        q = select(PriceReport)
        if product_id:
            q = q.where(PriceReport.product_id == product_id)
        rows = db.execute(q.order_by(PriceReport.report_id.desc()).limit(limit)).scalars().all()
        return [{
            "report_id": r.report_id, "product_id": r.product_id, "reported_price": r.reported_price,
            "baseline_price": r.baseline_price, "deviation_pct": r.deviation_pct, "status": r.status,
            "verify_price": r.verify_price, "created_at": r.created_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        } for r in rows]
    finally:
        db.close()
