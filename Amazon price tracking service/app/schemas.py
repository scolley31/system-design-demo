import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
_URL_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)", re.I)


def extract_asin(value: str) -> str:
    """接受純 ASIN 或 Amazon 商品 URL（/dp/XXXX、/gp/product/XXXX），回傳大寫 ASIN；無效丟 ValueError。"""
    v = (value or "").strip()
    if v.lower().startswith(("http://", "https://")):
        path = urlparse(v).path
        m = _URL_ASIN_RE.search(path)
        if not m:
            raise ValueError("URL 內找不到 ASIN（需含 /dp/XXXXXXXXXX）")
        v = m.group(1)
    v = v.upper()
    if not ASIN_RE.match(v):
        raise ValueError("product_id 需為 10 碼英數 ASIN 或 Amazon 商品 URL")
    return v


class _HasProduct(BaseModel):
    product_id: str

    @field_validator("product_id")
    @classmethod
    def _norm(cls, v: str) -> str:
        return extract_asin(v)


class TrackRequest(_HasProduct):
    pass


class SubscriptionRequest(_HasProduct):
    user_id: str = Field(min_length=1, max_length=64)
    price_threshold: float = Field(gt=0)
    notification_type: str = Field(default="sse", pattern="^(sse|email)$")
    email: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if v is not None and "@" not in v:
            raise ValueError("email 格式不正確")
        return v


class PriceReportRequest(_HasProduct):
    """extension 回報：匿名，只有 reporter_token（限流用）。"""
    price: float = Field(gt=0)
    currency: str = "USD"
    title: str | None = Field(default=None, max_length=512)
    reporter_token: str = Field(min_length=1, max_length=64)


class MockPriceRequest(_HasProduct):
    price: float = Field(gt=0)


class SeedRequest(_HasProduct):
    days: int = Field(default=730, ge=1, le=1500)
    per_day: int = Field(default=24, ge=1, le=48)
    base_price: float | None = Field(default=None, gt=0)
