"""user_location 持久化（env-gated）。

技術選型定案（見 DESIGN 附錄 D）：位置是 high-write（≈ 5.5K writes/s，key=device_id），
正式版用 **DynamoDB**；原型用 SQLite 的 user_location 表（同一個 SQLAlchemy engine）。

- 未設 LOCATION_TABLE → SqlLocationStore（SQLite / Postgres，跟 config/outbox 同庫）。
- 有設 LOCATION_TABLE → DynamoLocationStore（boto3，正式版 high-write）。

只存 H3 cell（前端算好直送），不存 raw lat/long → 隱私友善、寫入輕。
geo_index 由 routes 另外更新（兩者關注點分離）。
"""
import os
import time

from .database import SessionLocal
from .models import UserLocation


class SqlLocationStore:
    def upsert(self, device_id: str, user_id: str, cell: str) -> None:
        db = SessionLocal()
        try:
            row = db.get(UserLocation, device_id)
            if row:
                row.cell = cell
                row.user_id = user_id
            else:
                db.add(UserLocation(device_id=device_id, user_id=user_id, cell=cell))
            db.commit()
        finally:
            db.close()

    def get(self, device_id: str):
        db = SessionLocal()
        try:
            row = db.get(UserLocation, device_id)
            if not row:
                return None
            return {"device_id": row.device_id, "user_id": row.user_id, "cell": row.cell}
        finally:
            db.close()


class DynamoLocationStore:
    """DynamoDB 後端（正式版）。表 schema：partition key = device_id。
    寫入 `ttl_epoch = now + LOCATION_TTL_DAYS 天`，讓 DynamoDB TTL 自動過期（對齊 cell TTL）。
    Terraform 的 data 模組已在 `ttl_epoch` 屬性上啟用 TTL。
    """
    def __init__(self, table: str):
        import boto3  # 延遲匯入
        self._t = boto3.resource("dynamodb").Table(table)
        self._ttl_days = int(os.getenv("LOCATION_TTL_DAYS", "30"))

    def upsert(self, device_id: str, user_id: str, cell: str) -> None:
        now = int(time.time())
        self._t.put_item(
            Item={
                "device_id": device_id,
                "user_id": user_id,
                "cell": cell,
                "updated_at": now,
                "ttl_epoch": now + self._ttl_days * 86400,  # DynamoDB TTL 用此欄自動過期
            }
        )

    def get(self, device_id: str):
        item = self._t.get_item(Key={"device_id": device_id}).get("Item")
        return dict(item) if item else None


def _build():
    table = os.getenv("LOCATION_TABLE")
    return DynamoLocationStore(table) if table else SqlLocationStore()


location_store = _build()
