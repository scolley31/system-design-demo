"""Token 產生（DESIGN.md 第 3 題）。

SHA-256(url + nonce) -> Base62 -> 取前 8 碼。

重點釐清：
- nonce = "number used once"，每次變動（時間戳_重試次數），用來打破 SHA-256 的
  「同輸入同輸出」確定性，讓同一個 URL 每次也能產生不同 token（第 4 題：不去重）。
- nonce 只解決「確定性」，**不防碰撞**。截斷成 8 碼後輸出空間是 62^8，碰撞仍可能，
  由 DB UNIQUE + 重試兜底（第 5 題）。實際插入/重試邏輯在 routes.create_qr。
"""
import hashlib
import string
import time

BASE62 = string.digits + string.ascii_lowercase + string.ascii_uppercase  # 0-9a-zA-Z
TOKEN_LENGTH = 8
MAX_RETRIES = 10


def base62_encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    if num == 0:
        return BASE62[0]
    out = []
    while num > 0:
        num, rem = divmod(num, 62)
        out.append(BASE62[rem])
    return "".join(reversed(out))


def make_token(url: str, attempt: int = 0) -> str:
    # 毫秒級時間戳 + attempt，確保同一秒內的重試也會得到不同 nonce。
    nonce = f"{int(time.time() * 1000)}_{attempt}"
    digest = hashlib.sha256((url + nonce).encode()).digest()
    return base62_encode(digest)[:TOKEN_LENGTH]