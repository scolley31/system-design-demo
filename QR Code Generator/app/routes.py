"""API 路由（DESIGN.md 第 2 題：管理 /api/v1/qr/*、掃描 /r/{token}）。"""
import io
import os
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import storage
from .cache import cache
from .database import SessionLocal, get_db
from .models import ScanEvent, UrlMapping
from .schemas import CreateRequest, CreateResponse, QRInfoResponse, UpdateRequest
from .token_gen import MAX_RETRIES, make_token
from .url_validator import UrlValidationError, validate_url

router = APIRouter()

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


# ---------- helpers ----------

def _validate(url: str) -> str:
    try:
        return validate_url(url)
    except UrlValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """時間一律存 naive UTC（第 13 題）；tz-aware 的輸入轉成 UTC 再去 tzinfo。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _get_active_or_404(token: str, db: Session) -> UrlMapping:
    """管理 API 用：找不到或已軟刪除都當 404（第 11 題：管理面把已刪當『找不到』）。"""
    mapping = db.query(UrlMapping).filter(UrlMapping.token == token).first()
    if mapping is None or mapping.is_deleted:
        raise HTTPException(status_code=404, detail="Not Found")
    return mapping


def _record_scan(token: str, user_agent: str | None, ip: str | None) -> None:
    """第 8 題：非同步寫入 —— 由 BackgroundTasks 在回應送出後執行，
    不擋 redirect 關鍵路徑。背景任務自開 session（請求的 session 已關閉）。
    正式版改用訊息佇列 + worker。
    """
    db = SessionLocal()
    try:
        db.add(ScanEvent(token=token, user_agent=user_agent, ip_address=ip))
        db.commit()
    finally:
        db.close()


# ---------- 管理 API ----------

@router.post("/api/v1/qr/create", response_model=CreateResponse)
def create_qr(req: CreateRequest, db: Session = Depends(get_db)):
    normalized = _validate(req.url)
    expires_at = _to_naive_utc(req.expires_at)

    # 第 5 題：直接插入 + UNIQUE 例外重試（無 race，比 repo 的「先查再插」穩健）。
    token = None
    for attempt in range(MAX_RETRIES):
        candidate = make_token(normalized, attempt)
        mapping = UrlMapping(
            token=candidate, original_url=normalized, expires_at=expires_at
        )
        db.add(mapping)
        try:
            db.commit()
            token = candidate
            break
        except IntegrityError:
            db.rollback()  # 撞到 UNIQUE → 換 nonce 重試
    if token is None:
        raise HTTPException(status_code=500, detail="Failed to generate unique token")

    cache.set(token, normalized, expires_at)  # 暖快取（也緩解 read replica 複寫延遲，第 16 題）

    short_url = f"{BASE_URL}/r/{token}"
    # 第 15 題：有設 S3 則預生成上傳，qr_code_url 指 CDN；否則回 /image 即時生圖端點。
    if storage.enabled():
        qr_code_url = storage.upload_qr(token, storage.render_qr_png(short_url))
    else:
        qr_code_url = f"{BASE_URL}/api/v1/qr/{token}/image"

    return CreateResponse(
        token=token,
        short_url=short_url,
        qr_code_url=qr_code_url,
        original_url=normalized,
    )


@router.get("/api/v1/qr")
def list_qr(db: Session = Depends(get_db)):
    """列出所有未刪除的 QR（含掃描總數），供前端管理頁使用。

    註：目前系統無使用者/登入概念，故清單為全域。正式版應加 auth 並以 user_id 過濾
    （對應 PDF FR「用戶可以管理自己創建的 QR Code」）。
    """
    mappings = (
        db.query(UrlMapping)
        .filter(UrlMapping.is_deleted.is_(False))
        .order_by(UrlMapping.created_at.desc())
        .all()
    )
    counts = dict(
        db.query(ScanEvent.token, func.count(ScanEvent.id))
        .group_by(ScanEvent.token)
        .all()
    )
    return [
        {
            "token": m.token,
            "original_url": m.original_url,
            "short_url": f"{BASE_URL}/r/{m.token}",
            "qr_code_url": f"{BASE_URL}/api/v1/qr/{m.token}/image",
            "created_at": m.created_at,
            "expires_at": m.expires_at,
            "total_scans": counts.get(m.token, 0),
        }
        for m in mappings
    ]


@router.get("/api/v1/qr/{token}", response_model=QRInfoResponse)
def get_qr_info(token: str, db: Session = Depends(get_db)):
    return _get_active_or_404(token, db)


@router.patch("/api/v1/qr/{token}", response_model=QRInfoResponse)
def update_qr(token: str, req: UpdateRequest, db: Session = Depends(get_db)):
    mapping = _get_active_or_404(token, db)
    changed = False
    if req.url is not None:
        mapping.original_url = _validate(req.url)
        changed = True
    if req.expires_at is not None:
        mapping.expires_at = _to_naive_utc(req.expires_at)
        changed = True
    db.commit()
    db.refresh(mapping)
    # 第 7 題：先 commit、再 invalidate，避免並發 redirect 在 commit 前回填舊值的競態。
    if changed:
        cache.delete(token)
    return mapping


@router.delete("/api/v1/qr/{token}")
def delete_qr(token: str, db: Session = Depends(get_db)):
    mapping = _get_active_or_404(token, db)
    mapping.is_deleted = True  # 第 12 題：軟刪除
    db.commit()
    cache.delete(token)
    return {"detail": "Deleted"}


@router.get("/api/v1/qr/{token}/image")
def get_qr_image(
    token: str,
    db: Session = Depends(get_db),
    dimension: int | None = None,
    color: str = "000000",
    border: int = 4,
):
    """第 15 題：原型即時生成 PNG；正式版預生成後存 object store + CDN。
    QR 編的是不變的短碼 /r/{token}，所以圖是靜態內容（目標 URL 改了圖不需重生）。
    樣式參數 dimension/color/border 對齊 PDF 的圖片 API。
    """
    _get_active_or_404(token, db)
    short_url = f"{BASE_URL}/r/{token}"
    png = storage.render_qr_png(short_url, color=color, border=border, dimension=dimension)
    return StreamingResponse(io.BytesIO(png), media_type="image/png")


@router.get("/api/v1/qr/{token}/analytics")
def get_analytics(token: str, db: Session = Depends(get_db)):
    _get_active_or_404(token, db)
    total = (
        db.query(func.count(ScanEvent.id)).filter(ScanEvent.token == token).scalar()
    )
    daily = (
        db.query(
            func.date(ScanEvent.scanned_at).label("date"),
            func.count(ScanEvent.id).label("count"),
        )
        .filter(ScanEvent.token == token)
        .group_by(func.date(ScanEvent.scanned_at))
        .all()
    )
    return {
        "token": token,
        "total_scans": total,
        "scans_by_day": [{"date": str(r.date), "count": r.count} for r in daily],
    }


# ---------- 掃描入口 ----------

@router.get("/r/{token}")
def redirect(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """第 2/6/8/11/13 題：cache → DB → 302 / 404 / 410，scan 非同步記錄。"""
    user_agent = request.headers.get("user-agent")
    ip = request.client.host if request.client else None

    # 1) 快取命中（第 7 題）。仍需重新檢查惰性過期（第 13 題），過期則當 miss 落到 DB 回 410。
    cached = cache.get(token)
    if cached is not None:
        url, exp = cached
        if exp is not None and exp < datetime.utcnow():
            cache.delete(token)
        else:
            background_tasks.add_task(_record_scan, token, user_agent, ip)
            return RedirectResponse(url=url, status_code=302)  # 第 6 題：302

    # 2) DB 查找（正式版走 read replica，第 16 題）
    mapping = db.query(UrlMapping).filter(UrlMapping.token == token).first()
    if mapping is None:
        raise HTTPException(status_code=404, detail="Not Found")  # 第 11 題：不存在 → 404
    if mapping.is_deleted:
        raise HTTPException(status_code=410, detail="Gone — link deleted")  # 已刪 → 410
    if mapping.expires_at and mapping.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Gone — link expired")  # 惰性過期 → 410

    cache.set(token, mapping.original_url, mapping.expires_at)  # 回填快取（含 TTL）
    background_tasks.add_task(_record_scan, token, user_agent, ip)
    return RedirectResponse(url=mapping.original_url, status_code=302)
