"""即時送達（原型：SSE）。

user_id → 一組 asyncio.Queue（同一使用者可能多個 SSE 連線/分頁）。sender 送達時
publish 到該使用者的所有連線，SSE endpoint 從 queue 取出推給瀏覽器。

限制：hub 在行程內記憶體 → 只在單一 process 有效（本機 uvicorn / compose 用單 worker）。
正式版通知走 **email（SES）**（PDF 的 notification_type），SSE 只是 demo 的即時可視化
（見 DESIGN 附錄 H）。
"""
import asyncio
from collections import defaultdict


class RealtimeHub:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(user_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(user_id, None)

    def publish(self, user_id: str, data: dict) -> bool:
        subs = self._subs.get(user_id)
        if not subs:
            return False
        for q in subs:
            q.put_nowait(data)
        return True

    def is_online(self, user_id: str) -> bool:
        return user_id in self._subs


hub = RealtimeHub()
