"""QR 圖片渲染與儲存（DESIGN.md 第 15 題）。

QR 渲染邏輯集中在此，供 routes 的 /image（即時生圖）與 create（預生成上傳 S3）共用。

S3 儲存為 env-gated：
- 未設 S3_BUCKET → enabled() 為 False，create 走即時生圖路徑（本機行為不變）。
- 有設 S3_BUCKET → create 時把 PNG 上傳 S3，qr_code_url 指向 CDN_BASE/qr-img/{token}.png，
  由 CloudFront 服務（正式版，第 15 題）。
"""
import io
import os

import qrcode
from PIL import Image

S3_BUCKET = os.getenv("S3_BUCKET")
CDN_BASE = os.getenv("CDN_BASE", "").rstrip("/")
QR_PREFIX = "qr-img"


def enabled() -> bool:
    return bool(S3_BUCKET)


def render_qr_png(
    data: str, color: str = "000000", border: int = 4, dimension: int | None = None
) -> bytes:
    """把字串渲染成 QR PNG bytes（單一渲染來源）。"""
    qr = qrcode.QRCode(box_size=10, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    pil_img = qr.make_image(fill_color=f"#{color}", back_color="white")

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    if dimension:
        buf.seek(0)
        resized = Image.open(buf).resize((dimension, dimension), Image.NEAREST)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
    return buf.getvalue()


def object_key(token: str) -> str:
    return f"{QR_PREFIX}/{token}.png"


def cdn_url(token: str) -> str:
    return f"{CDN_BASE}/{object_key(token)}"


def upload_qr(token: str, png: bytes) -> str:
    """上傳 PNG 到 S3，回傳 CloudFront 上的圖片 URL。需先 enabled()。"""
    import boto3  # 延遲匯入：本機未裝 boto3 也不受影響

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=object_key(token),
        Body=png,
        ContentType="image/png",
        CacheControl="public, max-age=31536000, immutable",
    )
    return cdn_url(token)
