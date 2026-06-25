"""Redirect 快取（DESIGN.md 第 7 題）。

原型用行程內 dict 模擬；正式版換 Redis —— 因為我們是 stateless 水平擴展（第 2 題），
cache 必須是所有 server 共享的外部服務，否則 PATCH/DELETE 的 invalidation 只清得掉
本機、其他台會繼續導向舊 URL（正確性 bug）。

已內建 TTL（第 13 題：cache 設 TTL 當最終一致性保險，過期值最終自動淘汰）。
換 Redis 時只要把 get/set/delete 換成對應指令（SET key val EX ttl / GET / DEL）即可。
"""
import time

DEFAULT_TTL_SECONDS = 3600


class RedirectCache:
    """快取值帶 business 過期時間 (expires_at)，與 cache 自身 TTL 分開。

    redirect 命中時要重新檢查 expires_at（第 13 題：惰性過期必須在 cache 路徑也生效，
    否則暖在 cache 裡的已過期連結會錯誤回 302 而非 410）。
    """
    def __init__(self, ttl: int = DEFAULT_TTL_SECONDS):
        # token -> (url, expires_at, cache_deadline)
        self._store: dict[str, tuple[str, "datetime | None", float]] = {}
        self._ttl = ttl

    def get(self, token: str):
        """命中回 (url, expires_at)，未命中或 cache 過期回 None。"""
        item = self._store.get(token)
        if item is None:
            return None
        url, expires_at, cache_deadline = item
        if time.time() > cache_deadline:
            self._store.pop(token, None)
            return None
        return url, expires_at

    def set(self, token: str, url: str, expires_at=None) -> None:
        self._store[token] = (url, expires_at, time.time() + self._ttl)

    def delete(self, token: str) -> None:
        self._store.pop(token, None)


cache = RedirectCache()
