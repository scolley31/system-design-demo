"""事件來源 Gateway（對齊 PDF「Gateway」）。

原型：由 `POST /api/v1/earthquakes` 手動觸發（模擬事件來源），呼叫 broadcast。
（選配）USGS GeoJSON feed poller：設 USGS_POLL=1 開啟，定時拉近期地震灌進 broadcast。

正式版：獨立 Gateway service 對地震來源維持 persistent feed，負責 TLS、heartbeat、
reconnect、exponential backoff、rate-limit / malformed payload 隔離；與 Orchestrator
之間隔一層 queue，讓 ingest 與 targeting 可獨立 scaling、fail-soft。
"""
import asyncio
import json
import os
import urllib.request

from .broadcast import broadcast
from .errors import logger


async def handle_event(lat, lng, magnitude, alert_id, version) -> dict:
    return await broadcast(lat, lng, magnitude, alert_id, version)


USGS_URL = os.getenv(
    "USGS_FEED_URL",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
)


async def usgs_poller(interval_s: int = 60) -> None:
    """選配：拉 USGS all_hour feed，把每筆地震灌進 broadcast（alert_id=USGS id、version=updated ms）。"""
    seen: dict[str, int] = {}
    logger.info("USGS poller started (%s)", USGS_URL)
    while True:
        try:
            def _fetch():
                with urllib.request.urlopen(USGS_URL, timeout=10) as r:
                    return json.loads(r.read())

            data = await asyncio.to_thread(_fetch)
            for f in data.get("features", []):
                fid = f.get("id")
                props = f.get("props", f.get("properties", {})) or {}
                mag = props.get("mag")
                version = int(props.get("updated", 0)) // 1000  # 秒
                coords = (f.get("geometry", {}) or {}).get("coordinates", [])
                if fid is None or mag is None or len(coords) < 2:
                    continue
                if seen.get(fid, -1) >= version:
                    continue
                seen[fid] = version
                lng, lat = coords[0], coords[1]
                await handle_event(lat, lng, float(mag), fid, version)
        except Exception as e:  # noqa: BLE001
            logger.exception("USGS poll error: %s", e)
        await asyncio.sleep(interval_s)


def poller_enabled() -> bool:
    return os.getenv("USGS_POLL", "false").lower() in ("1", "true", "yes")
