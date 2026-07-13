"""Sender Worker：從 per-channel queue 取 job → 送達 → 更新 outbox 狀態。

對齊 PDF「Avoiding duplicates / out-of-order」的 worker 端：
- 讀 outbox；若已 terminal → 直接 drop（冪等）。
- 讀 supersession cache；若 job.version < latest → 跳過並標 CANCELLED_SUPERSEDED。
- 送達（原型：realtime.hub SSE）；upsert ATTEMPTED → VENDOR_ACCEPTED。
  正式版換成呼叫 APNs / FCM，成功後 VENDOR_ACCEPTED，失敗 FAILED（可重試）。

worker 在主程序內以 asyncio task 跑（每個 channel 一個）。正式版是獨立 worker fleet，
per-channel 各自擴縮（見 DESIGN 附錄 B）。
"""
import asyncio

from .broadcast import TERMINAL
from .database import SessionLocal
from .errors import logger
from .models import NotificationOutbox
from .queue import CHANNELS, queue
from .realtime import hub
from .supersession import supersession


async def _handle(job: dict) -> None:
    db = SessionLocal()
    try:
        nid = job["notification_id"]
        rec = db.get(NotificationOutbox, nid)
        if rec and rec.status in TERMINAL:
            return  # 冪等：已終態，不重送

        # supersession：較舊版本 → 取消不送
        if job["version"] < supersession.latest(job["alert_id"]):
            if rec and rec.status not in TERMINAL:
                rec.status = "CANCELLED_SUPERSEDED"
                db.commit()
            return

        if rec:
            rec.status = "ATTEMPTED"
            db.commit()

        # 實際送達（原型：SSE 推到瀏覽器；正式版：APNs / FCM）
        delivered = hub.publish(job["device_id"], job["payload"])

        if rec:
            rec.status = "VENDOR_ACCEPTED" if delivered else "FAILED"
            db.commit()
        logger.info(
            "%s %s v%s → %s",
            "delivered" if delivered else "no-subscriber",
            job["alert_id"], job["version"], job["device_id"],
        )
    finally:
        db.close()


async def run_worker(channel: str) -> None:
    logger.info("worker started (channel=%s)", channel)
    while True:
        try:
            job = await queue.dequeue(channel)
            await _handle(job)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001 — 單一 job 失敗不可拖垮 worker
            logger.exception("worker error: %s", e)


def start_workers() -> list[asyncio.Task]:
    return [asyncio.create_task(run_worker(c)) for c in CHANNELS]
