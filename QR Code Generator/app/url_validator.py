"""URL 驗證與正規化（DESIGN.md 第 9、10 題）。

第 9 題（保守正規化）：只 lower-case host，保留 path/query 大小寫與原 scheme，
移除單一尾斜線——避免 repo 「整串小寫 + 強制 https」導致導向錯誤資源。
第 10 題（惡意阻擋）：原型用靜態黑名單 + SSRF/內網位址阻擋示意；
正式版改可設定黑名單 + 外部 Safe Browsing API，並對 hostname 做 DNS 解析後再檢查。
"""
import ipaddress
from urllib.parse import urlparse, urlunparse

MAX_URL_LENGTH = 2048  # 第 1 題

BLOCKED_DOMAINS = {"evil.com", "malware.example.com", "phishing.example.com"}


class UrlValidationError(ValueError):
    """驗證失敗；routes 會轉成 HTTP 400。"""


def _is_private_host(host: str) -> bool:
    """第 10 題：擋 SSRF / 內網位址，避免短鏈被拿來打內部服務。"""
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 非 IP 字面值（一般網域）。正式版應解析 DNS 後再對解析出的 IP 檢查。
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def validate_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise UrlValidationError("URL is empty")
    if len(url) > MAX_URL_LENGTH:
        raise UrlValidationError(f"URL exceeds max length ({MAX_URL_LENGTH})")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlValidationError("Only http/https schemes are allowed")
    if not parsed.hostname:
        raise UrlValidationError("URL has no host")

    host = parsed.hostname.lower()
    if host in BLOCKED_DOMAINS:
        raise UrlValidationError("Domain is on blocklist")
    if _is_private_host(host):
        raise UrlValidationError("Private/internal address is not allowed")

    # 保守正規化：重建 netloc，只把 host 轉小寫；保留 userinfo / port。
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        netloc = f"{userinfo}@{netloc}"

    # 只移除單一尾斜線（根路徑的 "/" 也一併去掉）；path/query 大小寫保留不動。
    path = parsed.path
    if path.endswith("/"):
        path = path[:-1]

    return urlunparse(
        (parsed.scheme, netloc, path, parsed.params, parsed.query, parsed.fragment)
    )
