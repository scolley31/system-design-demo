"""Broadcast Service (Orchestrator)：geo-targeting + 去重 + supersession + enqueue。

流程（對齊 PDF「深入探討 2、3」）：
1. supersession：更新 latest_version；若進來的 version 比已知最新舊 → 整批 drop。
2. geo-target：震央+震度 → 覆蓋 cells（gridDisk）→ 從 geo_index 撈候選 devices → 去重。
3. config 過濾：magnitude >= magnitude_min 且 haversine(震央, cell 中心) <= distance_km。
4. supersede 舊版：同 alert_id、version 較舊且未 terminal 的 outbox → CANCELLED_SUPERSEDED。
5. 去重 gate：notification_id 已存在且非 FAILED → 跳過（冪等）；否則寫 ENQUEUED 並 enqueue。
"""
import hashlib

from .database import SessionLocal
from .errors import logger
from .geo import cell_center, cells_for_event, haversine_km
from .geo_index import geo_index
from .location_store import location_store
from .models import AlertConfig, NotificationOutbox
from .queue import queue
from .supersession import supersession

# terminal states = ATTEMPTED 之後（含）的所有狀態。CANCELLED_SUPERSEDED 不算 terminal（可再被覆蓋）。
TERMINAL = {"ATTEMPTED", "VENDOR_ACCEPTED", "FAILED"}


def notification_id(alert_id: str, version: int, device_id: str) -> str:
    return hashlib.sha256(f"{alert_id}|{version}|{device_id}".encode()).hexdigest()[:32]


async def broadcast(lat: float, lng: float, magnitude: float, alert_id: str, version: int) -> dict:
    latest = supersession.update(alert_id, version)
    if version < latest:
        logger.info("drop stale alert %s v%s (< latest v%s)", alert_id, version, latest)
        return {"stale": True, "targeted": 0, "enqueued": 0}

    cells, radius = cells_for_event(lat, lng, magnitude)
    candidates = geo_index.devices_in(cells)

    db = SessionLocal()
    targeted = enqueued = 0
    try:
        # 4. supersede 同 alert 舊版未 terminal 的紀錄
        db.query(NotificationOutbox).filter(
            NotificationOutbox.alert_id == alert_id,
            NotificationOutbox.version < version,
            NotificationOutbox.status.notin_(TERMINAL),
        ).update({"status": "CANCELLED_SUPERSEDED"}, synchronize_session=False)
        db.commit()

        for device_id in candidates:
            loc = location_store.get(device_id)
            if not loc:
                continue
            cfg = db.get(AlertConfig, device_id)
            if not cfg or cfg.status != "active":
                continue
            if magnitude < cfg.magnitude_min:
                continue
            clat, clng = cell_center(loc["cell"])
            dist = haversine_km(lat, lng, clat, clng)
            if dist > cfg.distance_km:  # cell 粗篩後的距離二次裁切
                continue

            targeted += 1
            nid = notification_id(alert_id, version, device_id)
            existing = db.get(NotificationOutbox, nid)
            # 5. 去重 gate：已存在且非 FAILED（已送/在途）→ 冪等跳過，避免重複推播
            if existing and existing.status != "FAILED":
                continue
            if existing:
                existing.status = "ENQUEUED"
            else:
                db.add(NotificationOutbox(
                    notification_id=nid, alert_id=alert_id, version=version,
                    device_id=device_id, channel="sse", status="ENQUEUED",
                ))
            db.commit()

            await queue.enqueue("sse", {
                "notification_id": nid,
                "alert_id": alert_id,
                "version": version,
                "device_id": device_id,
                "channel": "sse",
                "payload": {
                    "alert_id": alert_id,
                    "version": version,
                    "magnitude": magnitude,
                    "lat": lat,
                    "long": lng,
                    "radius_km": radius,
                    "distance_km": round(dist, 1),
                },
            })
            enqueued += 1
    finally:
        db.close()

    logger.info(
        "broadcast %s v%s M%s: cells=%d candidates=%d targeted=%d enqueued=%d",
        alert_id, version, magnitude, len(cells), len(candidates), targeted, enqueued,
    )
    return {
        "stale": False,
        "cells": len(cells),
        "candidates": len(candidates),
        "targeted": targeted,
        "enqueued": enqueued,
        "radius_km": radius,
    }
