"""Supersession cache：supersession:{alert_id} → latest_version。

用來記錄某個 alert_id 目前已知的最新版本，讓「較新更新覆蓋較舊通知」（out-of-order）。
技術選型：正式版 Redis（與 geo index / Streams 同一套），原型行程內 dict。介面相同。
"""
import os


class InMemorySupersession:
    def __init__(self):
        self._d: dict[str, int] = {}

    def update(self, alert_id: str, version: int) -> int:
        """記錄並回傳目前已知最新版本（取 max）。"""
        cur = self._d.get(alert_id, 0)
        if version > cur:
            self._d[alert_id] = version
            cur = version
        return cur

    def latest(self, alert_id: str) -> int:
        return self._d.get(alert_id, 0)


class RedisSupersession:
    _K = "supersession:"

    def __init__(self, url: str):
        import redis  # 延遲匯入
        self._r = redis.Redis.from_url(url, decode_responses=True)

    def update(self, alert_id: str, version: int) -> int:
        # 原型示範用 GET/SET；正式版可用 Lua 保原子（避免並發覆蓋）。
        cur = int(self._r.get(self._K + alert_id) or 0)
        if version > cur:
            self._r.set(self._K + alert_id, version)
            cur = version
        return cur

    def latest(self, alert_id: str) -> int:
        return int(self._r.get(self._K + alert_id) or 0)


def _build():
    url = os.getenv("REDIS_URL")
    return RedisSupersession(url) if url else InMemorySupersession()


supersession = _build()
