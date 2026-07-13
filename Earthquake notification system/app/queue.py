"""per-channel job queue（Broadcast Service → Workers 之間）。

技術選型定案（見 DESIGN 附錄 B）：正式版 **Redis Streams**（memory-first、低延遲
fan-out、per-channel 好切；非 Kafka——Kafka 是 disk-first 高吞吐 log，不利 sub-second）。
原型用行程內 asyncio.Queue。介面相同（enqueue/dequeue）。

per-channel：不同信道（sse/apns/fcm/sms）各自一條 queue，可套不同 retry / rate limit，
單一 vendor brownout 不影響其他信道。原型只有 "sse" 一種，其餘為擴充位。
"""
import asyncio
import json
import os

CHANNELS = ["sse"]  # 擴充位：["sse", "apns", "fcm", "sms"]


class InMemoryQueue:
    def __init__(self):
        self._q: dict[str, asyncio.Queue] = {c: asyncio.Queue() for c in CHANNELS}

    async def enqueue(self, channel: str, job: dict) -> None:
        await self._q[channel].put(job)

    async def dequeue(self, channel: str) -> dict:
        return await self._q[channel].get()


class RedisStreamQueue:
    """Redis Streams 後端（正式版）。每個 channel 一條 stream + consumer group。"""

    def __init__(self, url: str):
        import redis  # 延遲匯入
        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._group = "workers"
        for c in CHANNELS:
            try:
                self._r.xgroup_create(self._stream(c), self._group, id="0", mkstream=True)
            except Exception:
                pass  # BUSYGROUP：group 已存在

    def _stream(self, channel: str) -> str:
        return f"quake:stream:{channel}"

    async def enqueue(self, channel: str, job: dict) -> None:
        await asyncio.to_thread(
            self._r.xadd, self._stream(channel), {"job": json.dumps(job)}
        )

    async def dequeue(self, channel: str) -> dict:
        stream = self._stream(channel)

        def _read():
            while True:
                resp = self._r.xreadgroup(
                    self._group, "w1", {stream: ">"}, count=1, block=5000
                )
                if resp:
                    _, msgs = resp[0]
                    msg_id, fields = msgs[0]
                    self._r.xack(stream, self._group, msg_id)
                    return json.loads(fields["job"])

        # 用 to_thread 跑阻塞式 XREADGROUP，不卡住 event loop。
        return await asyncio.to_thread(_read)


def _build():
    url = os.getenv("REDIS_URL")
    return RedisStreamQueue(url) if url else InMemoryQueue()


queue = _build()
