"""API 路由（對齊 PDF API design，加 /api/v1）。"""
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .database import SessionLocal
from .gateway import handle_event
from .geo import RES
from .geo_index import geo_index
from .location_store import location_store
from .models import AlertConfig, NotificationOutbox
from .realtime import hub
from .schemas import ConfigRequest, EarthquakeRequest, LocationRequest

router = APIRouter(prefix="/api/v1")


@router.get("/config/meta")
def config_meta():
    """給前端的公開設定（H3 解析度必須前後端一致）。"""
    return {"h3_res": RES}


@router.post("/alerts/configuration")
def set_config(req: ConfigRequest):
    db = SessionLocal()
    try:
        cfg = db.get(AlertConfig, req.device_id)
        if cfg:
            cfg.magnitude_min = req.magnitude_min
            cfg.distance_km = req.distance_km
            cfg.status = "active"
        else:
            db.add(AlertConfig(
                device_id=req.device_id,
                magnitude_min=req.magnitude_min,
                distance_km=req.distance_km,
                status="active",
            ))
        db.commit()
        return {
            "device_id": req.device_id,
            "magnitude_min": req.magnitude_min,
            "distance_km": req.distance_km,
            "status": "active",
        }
    finally:
        db.close()


@router.get("/alerts/configuration")
def get_config(device_id: str):
    db = SessionLocal()
    try:
        cfg = db.get(AlertConfig, device_id)
        if not cfg:
            return JSONResponse(status_code=404, content={"detail": "not configured"})
        return {
            "device_id": cfg.device_id,
            "magnitude_min": cfg.magnitude_min,
            "distance_km": cfg.distance_km,
            "status": cfg.status,
        }
    finally:
        db.close()


@router.post("/alerts/user_location")
def report_location(req: LocationRequest):
    # 前端已算好 H3 cell 直送，server 不換算。原型 user_id = device_id。
    location_store.upsert(req.device_id, req.device_id, req.cell)
    geo_index.set_device_cell(req.device_id, req.cell)
    return {"device_id": req.device_id, "cell": req.cell}


@router.post("/earthquakes")
async def trigger_earthquake(req: EarthquakeRequest):
    alert_id = req.alert_id or ("eq-" + datetime.utcnow().strftime("%Y%m%d%H%M%S%f"))
    version = req.version or 1
    result = await handle_event(req.lat, req.long, req.magnitude, alert_id, version)
    return {"alert_id": alert_id, "version": version, **result}


@router.get("/stream")
async def stream(device_id: str, request: Request):
    """SSE：裝置訂閱，收到符合條件的地震即時推播。"""
    q = hub.subscribe(device_id)

    async def gen():
        try:
            yield {"event": "connected", "data": json.dumps({"device_id": device_id})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield {"event": "alert", "data": json.dumps(data)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}  # keep-alive
        finally:
            hub.unsubscribe(device_id, q)

    return EventSourceResponse(gen())


@router.get("/notifications")
def notifications(device_id: str):
    """debug：讀該裝置的 outbox（狀態機/版本）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(NotificationOutbox)
            .filter(NotificationOutbox.device_id == device_id)
            .order_by(NotificationOutbox.updated_at.desc())
            .all()
        )
        return [
            {
                "notification_id": r.notification_id,
                "alert_id": r.alert_id,
                "version": r.version,
                "channel": r.channel,
                "status": r.status,
            }
            for r in rows
        ]
    finally:
        db.close()
