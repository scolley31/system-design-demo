"""per-channel job queue。

技術選型定案（見 DESIGN 附錄 A/B）：正式版 **Redis Streams**（memory-first、consumer group、
per-channel 好切；1 小時 SLA 不需要 Kafka 的吞吐/持久化 log）。原型用行程內 asyncio.Queue。
介面相同（enqueue/dequeue）。

channels：
- crawl          scheduler / urgent → crawl worker（爬取 job）
- price_changed  CDC tailer → price-change worker（價格事件）
- notify:sse     price-change worker → SSE sender
- notify:email   price-change worker → SES sender
"""
import asyncio
import json

from .config import REDIS_URL

CHANNELS = ["crawl", "price_changed", "notify:sse", "notify:email"]


class InMemoryQueue:
    def __init__(self):
        self._q: dict[str, asyncio.Queue] = {c: asyncio.Queue() for c in CHANNELS}

    async def enqueue(self, channel: str, job: dict) -> None:
        await self._q[channel].put(job)

    async def dequeue(self, channel: str) -> dict:
        return await self._q[channel].get()

    def depth(self, channel: str) -> int:
        return self._q[channel].qsize()


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
        return f"price:stream:{channel}"

    async def enqueue(self, channel: str, job: dict) -> None:
        await asyncio.to_thread(self._r.xadd, self._stream(channel), {"job": json.dumps(job)})

    async def dequeue(self, channel: str) -> dict:
        stream = self._stream(channel)

        def _read():
            while True:
                resp = self._r.xreadgroup(self._group, "w1", {stream: ">"}, count=1, block=5000)
                if resp:
                    _, msgs = resp[0]
                    msg_id, fields = msgs[0]
                    self._r.xack(stream, self._group, msg_id)
                    return json.loads(fields["job"])

        return await asyncio.to_thread(_read)

    def depth(self, channel: str) -> int:
        try:
            return int(self._r.xlen(self._stream(channel)))
        except Exception:
            return -1


def _build():
    return RedisStreamQueue(REDIS_URL) if REDIS_URL else InMemoryQueue()


queue = _build()
