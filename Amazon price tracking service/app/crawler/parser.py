"""Amazon 商品頁解析（selectolax，C 實作、比 BeautifulSoup 快一個數量級）。

實測 2026-09 amazon.com（附錄 E）：
- 標題：#productTitle
- 價格：**只看主價格區塊**（#corePrice_feature_div / #corePriceDisplay_desktop_feature_div / #apex_desktop …）
  裡的第一個 .a-price，用 .a-price-whole + .a-price-fraction 組價（.a-offscreen 有時是空的、
  有時沒有小數點）。**不能**抓整頁第一個 .a-price：主價格被藏起來時，第一個命中的會是
  「相關商品輪播」的價格（實測踩雷）。
- 缺貨：#availability / #outOfStock 含 "unavailable"
- 區域鎖定：#availability 含 "cannot be shipped to your selected delivery location" → 從這個
  IP（非美國）看不到 buybox 價格；Amazon 對國際 IP 改配送地要登入，所以 crawler 要放美國出口
  （或 proxy pool）。demo 用可國際配送的商品（書、配件）即可。
- 被擋：Robot Check / captcha 表單 / "Continue shopping" 攔截頁（chrome124 指紋會踩到）。
"""
import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser

# 主價格容器（依優先順序）
MAIN_PRICE_CONTAINERS = [
    "#corePrice_feature_div",
    "#corePriceDisplay_desktop_feature_div",
    "#corePrice_desktop",
    "#apex_desktop",
    "#tp_price_block_total_price_ww",
    "#price",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#priceblock_saleprice",
    "#sns-base-price",
]

_BLOCK_MARKERS = (
    "robot check",
    "enter the characters you see below",
    "validatecaptcha",
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
    "click the button below to continue shopping",
)

_SYMBOLS = [("US$", "USD"), ("NT$", "TWD"), ("C$", "CAD"), ("A$", "AUD"), ("$", "USD"), ("¥", "JPY"), ("￥", "JPY"),
            ("£", "GBP"), ("€", "EUR")]
_ISO_RE = re.compile(r"(?<![A-Z])(USD|TWD|JPY|EUR|GBP|CAD|AUD)(?![A-Z])")


class ParseError(Exception):
    pass


@dataclass
class Parsed:
    title: str | None
    price: float | None
    currency: str
    available: bool
    region_locked: bool = False  # 這個 IP 看不到 buybox（非缺貨）


def is_blocked(html: str) -> bool:
    head = html[:30000].lower()
    return any(m in head for m in _BLOCK_MARKERS)


def detect_currency(text: str, default: str = "USD") -> str:
    m = _ISO_RE.search(text or "")
    if m:
        return m.group(1)
    for sym, code in _SYMBOLS:
        if sym in (text or ""):
            return code
    return default


def parse_money(text: str) -> tuple[float, str]:
    """'$1,234.56' → (1234.56, 'USD')；'￥12,345' → (12345, 'JPY')；'1.234,56 €' → (1234.56, 'EUR')。"""
    raw = (text or "").strip()
    currency = detect_currency(raw)
    num = re.sub(r"[^\d.,]", "", raw)
    if not num:
        raise ParseError(f"無法解析價格: {raw!r}")
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):   # 歐式 1.234,56
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        parts = num.split(",")
        num = num.replace(",", ".") if len(parts) == 2 and len(parts[1]) == 2 else num.replace(",", "")
    try:
        return float(num), currency
    except ValueError as e:
        raise ParseError(f"無法解析價格: {raw!r}") from e


# 這些 id 本身就是純價格文字（舊版版型），才允許直接解析容器文字；其他容器只認 .a-price
# （否則 #apex_desktop 的 "Save 20% with Trade-In" 會被當成 $20，實測踩雷）。
LEGACY_TEXT_PRICE_IDS = {"#priceblock_ourprice", "#priceblock_dealprice", "#priceblock_saleprice", "#sns-base-price", "#price"}


def _price_from_container(node, allow_raw_text: bool = False) -> tuple[float, str] | None:
    p = node.css_first(".a-price")
    if p:
        whole, frac, sym, off = (p.css_first(".a-price-whole"), p.css_first(".a-price-fraction"),
                                 p.css_first(".a-price-symbol"), p.css_first(".a-offscreen"))
        if whole and whole.text(strip=True):
            w = re.sub(r"[^\d]", "", whole.text(strip=True))
            f = re.sub(r"[^\d]", "", frac.text(strip=True)) if frac else ""
            if w:
                cur = detect_currency((sym.text(strip=True) if sym else "") or (off.text(strip=True) if off else ""))
                return float(f"{w}.{f or '0'}"), cur
        if off and off.text(strip=True):
            try:
                return parse_money(off.text(strip=True))
            except ParseError:
                return None
        return None
    if not allow_raw_text:
        return None
    text = node.text(strip=True)
    if text and re.search(r"\d", text) and len(text) < 40:
        try:
            return parse_money(text)
        except ParseError:
            return None
    return None


def parse_product(html: str) -> Parsed:
    if is_blocked(html):
        raise ParseError("blocked: captcha / robot check / interstitial")
    tree = HTMLParser(html)
    tree.strip_tags(["script", "style", "noscript"])

    title_node = tree.css_first("#productTitle")
    title = title_node.text(strip=True) if title_node else None

    avail_parts = []
    for sel in ("#availability", "#outOfStock"):
        n = tree.css_first(sel)
        if n:
            avail_parts.append(n.text(strip=True).lower())
    avail_text = " ".join(avail_parts)
    region_locked = "cannot be shipped to your selected delivery location" in avail_text
    unavailable = "unavailable" in avail_text and not region_locked

    price, currency = None, "USD"
    for sel in MAIN_PRICE_CONTAINERS:
        node = tree.css_first(sel)
        if not node:
            continue
        got = _price_from_container(node, allow_raw_text=sel in LEGACY_TEXT_PRICE_IDS)
        if got:
            price, currency = got
            break
    if region_locked:
        price = None  # buybox 對這個 IP 不可見；任何殘留價格片段都不可信

    if price is None and title is None:
        raise ParseError("頁面結構不符（無標題、無價格）")
    return Parsed(title=title, price=price, currency=currency,
                  available=not unavailable and not region_locked, region_locked=region_locked)
