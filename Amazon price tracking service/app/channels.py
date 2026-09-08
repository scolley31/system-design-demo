"""通知送達通道（per-channel sender）。

- sse   原型：realtime hub 推到瀏覽器（demo 可視化）。
- email 正式版：SES（SES_FROM_EMAIL 有設）；未設 → dry-run（只記 log，標 dry_run=True）。

PDF 的 notification_type 本質是 email；SSE 是為了 demo 看得到。正式版也可加 push（APNs/FCM）。
"""
from .config import SES_FROM_EMAIL
from .errors import logger
from .realtime import hub


class SseSender:
    name = "sse"

    async def send(self, job: dict) -> bool:
        return hub.publish(job["user_id"], job["payload"])


class EmailDryRunSender:
    name = "email-dryrun"

    async def send(self, job: dict) -> bool:
        p = job["payload"]
        logger.info("[email dry-run] to=%s subject=Price drop %s → %.2f (threshold %.2f)",
                    job.get("email"), p["product_id"], p["new_price"], p["threshold"])
        job["payload"]["dry_run"] = True
        return True


class SesSender:
    name = "ses"

    def __init__(self, sender: str):
        import boto3  # 延遲匯入
        self._ses = boto3.client("ses")
        self._from = sender

    async def send(self, job: dict) -> bool:
        import asyncio
        p = job["payload"]
        to = job.get("email")
        if not to:
            return False
        subject = f"[Price Tracker] {p['product_id']} dropped to {p['currency']} {p['new_price']:.2f}"
        body = (f"{p.get('title') or p['product_id']}\n\n"
                f"Price: {p.get('old_price')} → {p['new_price']:.2f} (your threshold {p['threshold']:.2f})\n"
                f"https://www.amazon.com/dp/{p['product_id']}\n")
        try:
            await asyncio.to_thread(
                self._ses.send_email,
                Source=self._from, Destination={"ToAddresses": [to]},
                Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("SES send failed: %s", e)
            return False


def build_sender(channel: str):
    if channel == "notify:sse":
        return SseSender()
    if channel == "notify:email":
        return SesSender(SES_FROM_EMAIL) if SES_FROM_EMAIL else EmailDryRunSender()
    raise ValueError(channel)
