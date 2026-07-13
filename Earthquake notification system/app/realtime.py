"""即時送達（原型：SSE）。

device_id → 一組 asyncio.Queue（同一裝置可能多個 SSE 連線/分頁）。worker 送達時
publish 到該裝置的所有連線，SSE endpoint 從 queue 取出推給瀏覽器。

限制：hub 在行程內記憶體 → 只在單一 process 有效（本機 uvicorn / compose 用單 worker）。
正式版即時送達走 **APNs / FCM**（不靠 SSE 過 CDN），所以此限制在正式版不存在（見 DESIGN 附錄 J）。
"""
import asyncio
from collections import defaultdict


class RealtimeHub:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, device_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[device_id].add(q)
        return q

    def unsubscribe(self, device_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(device_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(device_id, None)

    def publish(self, device_id: str, data: dict) -> bool:
        subs = self._subs.get(device_id)
        if not subs:
            return False
        for q in subs:
            q.put_nowait(data)
        return True

    def is_online(self, device_id: str) -> bool:
        return device_id in self._subs


hub = RealtimeHub()
