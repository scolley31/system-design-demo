"""兩層 fetcher（深入探討 1 / 附錄 E：Amazon 反爬）。

Tier 1  curl_cffi  — `impersonate="chrome120"`（可輪替）讓 TLS/JA3/HTTP2 指紋與真瀏覽器一致。
                     Amazon 在 TLS handshake 就做指紋比對，純 requests 會直接被擋；curl_cffi
                     輕量（無瀏覽器）、每次請求數百 ms，是低量抓取的主力。
Tier 2  Playwright — 無頭 Chromium，能跑 JS、較能過 captcha 頁；但每次吃 CPU/記憶體，映像
                     +400MB。只在 CRAWLER_PLAYWRIGHT=1 且已安裝時作為 fallback。
Mock    MockFetcher — MOCK_AMAZON=1：離線假 Amazon（產生符合真實選擇器的 HTML，讓 parser
                     路徑完整被測），價格隨機漫步、可由 /debug/mock-price 指定。

速率：TokenBucket 以 CRAWL_RPS（預設 1）限制每 process 對 Amazon 的請求 + jitter，
對齊 PDF「Amazon 對每個 IP 限速 1 visit/sec」。
"""
import asyncio
import random
import time
from dataclasses import dataclass

from ..config import (AMAZON_CURRENCY, AMAZON_DOMAIN, CRAWL_RPS, CRAWLER_PLAYWRIGHT,
                      FETCH_TIMEOUT_S, FORCE_PLAYWRIGHT, MOCK_AMAZON)
from ..errors import logger
from .parser import Parsed, ParseError, is_blocked, parse_product


class BlockedError(Exception):
    """兩層都被擋（captcha / robot check）。"""


class FetchError(Exception):
    """網路 / HTTP 錯誤。"""


class NotFoundError(Exception):
    """404：ASIN 不存在。"""


@dataclass
class Snapshot:
    parsed: Parsed
    fetcher: str
    status_code: int


def product_url(asin: str) -> str:
    return f"https://{AMAZON_DOMAIN}/dp/{asin}"


class TokenBucket:
    def __init__(self, rate: float):
        self._interval = 1.0 / max(rate, 0.01)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            # jitter：避免固定節奏被判定為機器
            self._next = max(now, self._next) + self._interval * random.uniform(0.9, 1.4)
        if wait > 0:
            await asyncio.sleep(wait)


_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# 實測 2026-09：curl_cffi 預設 "chrome"（= chrome124）會拿到 "Continue shopping" 攔截頁；
# chrome120 / safari17_0 / edge101 / safari15_5 都能拿到完整商品頁。被擋就換下一個指紋。
IMPERSONATE_PROFILES = ["chrome120", "safari17_0", "edge101", "safari15_5"]


class CurlCffiFetcher:
    name = "curl_cffi"

    def __init__(self):
        self._idx = 0
        self._session = None

    @property
    def profile(self) -> str:
        return IMPERSONATE_PROFILES[self._idx % len(IMPERSONATE_PROFILES)]

    def _ensure_session(self):
        if self._session is None:
            from curl_cffi import requests as cffi_requests  # 延遲匯入
            self._session = cffi_requests.Session(impersonate=self.profile)
            # i18n-prefs：固定顯示幣別（否則依 IP 換算成當地幣，且 a-offscreen 格式不穩）
            self._session.cookies.set("i18n-prefs", AMAZON_CURRENCY, domain=f".{AMAZON_DOMAIN.removeprefix('www.')}")
        return self._session

    def rotate(self) -> str:
        """被擋 → 換下一個 TLS 指紋 + 全新 cookie jar。"""
        self._idx += 1
        self._session = None
        return self.profile

    async def fetch(self, url: str) -> tuple[str, int]:
        sess = self._ensure_session()

        def _get():
            r = sess.get(url, headers=_HEADERS, timeout=FETCH_TIMEOUT_S, allow_redirects=True)
            return r.text, r.status_code

        try:
            return await asyncio.to_thread(_get)
        except Exception as e:  # noqa: BLE001
            raise FetchError(f"curl_cffi[{self.profile}]: {e}") from e


class PlaywrightFetcher:
    """無頭 Chromium。lazy 啟動一個 browser，重複使用 context。"""
    name = "playwright"

    def __init__(self):
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._browser:
            return
        async with self._lock:
            if self._browser:
                return
            from playwright.async_api import async_playwright  # 延遲匯入
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True,
                                                            args=["--disable-blink-features=AutomationControlled"])

    async def fetch(self, url: str) -> tuple[str, int]:
        await self._ensure()
        ctx = await self._browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
            locale="en-US", viewport={"width": 1366, "height": 850},
        )
        try:
            page = await ctx.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=FETCH_TIMEOUT_S * 1000)
            try:
                await page.wait_for_selector("#productTitle, #availability, .a-price", timeout=5000)
            except Exception:
                pass
            html = await page.content()
            return html, resp.status if resp else 0
        except Exception as e:  # noqa: BLE001
            raise FetchError(f"playwright: {e}") from e
        finally:
            await ctx.close()


def playwright_available() -> bool:
    if not (CRAWLER_PLAYWRIGHT or FORCE_PLAYWRIGHT):
        return False
    try:
        import playwright.async_api  # noqa: F401
        return True
    except Exception:
        return False


class MockFetcher:
    """離線假 Amazon。輸出符合真實選擇器的 HTML，讓 parser 路徑完整被測。"""
    name = "mock"

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._pinned: set[str] = set()

    def set_price(self, asin: str, price: float) -> None:
        self._prices[asin] = price
        self._pinned.add(asin)

    def current(self, asin: str) -> float:
        if asin not in self._prices:
            rnd = random.Random(asin)
            self._prices[asin] = round(rnd.uniform(20, 300), 2)
        elif asin not in self._pinned:
            # 未指定 → 小幅隨機漫步（±1%），讓 demo 有「小波動被過濾」可看
            self._prices[asin] = round(max(1.0, self._prices[asin] * random.uniform(0.99, 1.01)), 2)
        return self._prices[asin]

    async def fetch(self, url: str) -> tuple[str, int]:
        asin = url.rstrip("/").split("/")[-1]
        await asyncio.sleep(random.uniform(0.05, 0.2))
        p = self.current(asin)
        html = f"""<html><head><title>Amazon.com: Mock {asin}</title></head><body>
<span id="productTitle">Mock Product {asin} (offline MOCK_AMAZON)</span>
<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">${p:,.2f}</span></span></div>
<div id="availability"><span>In Stock</span></div>
</body></html>"""
        return html, 200


class TwoTierFetcher:
    def __init__(self):
        self.bucket = TokenBucket(CRAWL_RPS)
        self.mock = MockFetcher() if MOCK_AMAZON else None
        self.primary = CurlCffiFetcher()
        self.fallback = PlaywrightFetcher() if playwright_available() else None
        logger.info("fetcher: mock=%s playwright_fallback=%s domain=%s rps=%s profile=%s",
                    bool(self.mock), bool(self.fallback), AMAZON_DOMAIN, CRAWL_RPS, self.primary.profile)

    async def _try(self, tier, url: str) -> Snapshot:
        html, code = await tier.fetch(url)
        if code == 404:
            raise NotFoundError(url)
        if code in (503, 429) or is_blocked(html):
            raise BlockedError(f"{tier.name}: http {code} / robot check")
        if code >= 400:
            raise FetchError(f"{tier.name}: http {code}")
        try:
            parsed = parse_product(html)
        except ParseError as e:
            raise BlockedError(f"{tier.name}: {e}") from e
        return Snapshot(parsed=parsed, fetcher=tier.name, status_code=code)

    async def fetch_product(self, asin: str) -> Snapshot:
        url = product_url(asin)
        if self.mock:
            return await self._try(self.mock, url)

        await self.bucket.acquire()
        if FORCE_PLAYWRIGHT and self.fallback:
            return await self._try(self.fallback, url)

        try:
            return await self._try(self.primary, url)
        except (BlockedError, FetchError) as e1:
            # tier 1 被擋：先換一個 TLS 指紋再試一次（便宜），還不行才升級 Playwright（貴）
            prof = self.primary.rotate()
            logger.warning("crawl %s: %s → rotate impersonate to %s", asin, e1, prof)
            await self.bucket.acquire()
            try:
                return await self._try(self.primary, url)
            except (BlockedError, FetchError) as e2:
                if not self.fallback:
                    raise
                logger.warning("crawl %s: tier1 failed again (%s) → playwright fallback", asin, e2)
                await self.bucket.acquire()
                return await self._try(self.fallback, url)


fetcher = TwoTierFetcher()
