"""Geo index：cell → devices 正向索引 + device → cell 反向索引（供搬移/清理）。

技術選型定案（見 DESIGN 附錄 A/D）：正式版用 **Redis**（與 per-channel Streams、
supersession cache 共用一套），原型用行程內 dict。兩者介面相同，broadcast 無需區分。

env-gated（延遲匯入，本機未裝 redis 也不受影響）：
- 未設 REDIS_URL → InMemoryGeoIndex（本機/原型）。
- 有設 REDIS_URL → RedisGeoIndex（正式版；多台 server 共享 + 全域一致）。

TTL：位置回報帶 TTL（預設 30 天），過期自動淘汰（對齊 PDF 的 cell TTL 7–30 天）。
"""
import os
import time

DEFAULT_TTL = int(os.getenv("GEO_INDEX_TTL", str(30 * 24 * 3600)))  # 30 天


class InMemoryGeoIndex:
    def __init__(self, ttl: int = DEFAULT_TTL):
        # cell -> {device_id: deadline_ts}
        self._cell_devices: dict[str, dict[str, float]] = {}
        # device_id -> cell（反向，搬移時清舊 cell）
        self._device_cell: dict[str, str] = {}
        self._ttl = ttl

    def set_device_cell(self, device_id: str, cell: str) -> None:
        old = self._device_cell.get(device_id)
        if old and old != cell:
            self._cell_devices.get(old, {}).pop(device_id, None)
        self._cell_devices.setdefault(cell, {})[device_id] = time.time() + self._ttl
        self._device_cell[device_id] = cell

    def devices_in(self, cells) -> set[str]:
        now = time.time()
        out: set[str] = set()
        for c in cells:
            m = self._cell_devices.get(c)
            if not m:
                continue
            for d, deadline in list(m.items()):
                if deadline < now:
                    m.pop(d, None)
                    continue
                out.add(d)
        return out

    def remove(self, device_id: str) -> None:
        c = self._device_cell.pop(device_id, None)
        if c:
            self._cell_devices.get(c, {}).pop(device_id, None)


class RedisGeoIndex:
    """Redis 後端（正式版）。
    - cell → devices：ZSET，member=device_id、score=deadline_ts（用 score 做 TTL 兜底）。
    - device → cell：String，帶 EX ttl（反向索引，搬移時清舊 cell）。
    """
    _CELL = "geo:cell:"
    _DEV = "geo:dev:"

    def __init__(self, url: str, ttl: int = DEFAULT_TTL):
        import redis  # 延遲匯入
        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._ttl = ttl

    def set_device_cell(self, device_id: str, cell: str) -> None:
        old = self._r.get(self._DEV + device_id)
        if old and old != cell:
            self._r.zrem(self._CELL + old, device_id)
        self._r.zadd(self._CELL + cell, {device_id: time.time() + self._ttl})
        self._r.set(self._DEV + device_id, cell, ex=self._ttl)

    def devices_in(self, cells) -> set[str]:
        now = time.time()
        out: set[str] = set()
        for c in cells:
            key = self._CELL + c
            self._r.zremrangebyscore(key, "-inf", now)  # 清過期
            out.update(self._r.zrange(key, 0, -1))
        return out

    def remove(self, device_id: str) -> None:
        c = self._r.get(self._DEV + device_id)
        if c:
            self._r.zrem(self._CELL + c, device_id)
        self._r.delete(self._DEV + device_id)


def _build():
    url = os.getenv("REDIS_URL")
    return RedisGeoIndex(url) if url else InMemoryGeoIndex()


geo_index = _build()
