"""建 DynamoDB Local 的表（compose 的 init-tables 容器 / 手動）。正式版的表由 Terraform 建，app 沒有 CreateTable 權限。

  DYNAMO_TABLE_PREFIX=messenger-local- DYNAMO_ENDPOINT=http://localhost:8101 python -m app.tools.ensure_tables
"""
import sys
import time

from ..store import DynamoStore, store


def main() -> int:
    if not isinstance(store, DynamoStore):
        print("DYNAMO_TABLE_PREFIX 未設：SQLite 模式不需要建表")
        return 0
    for attempt in range(30):  # DynamoDB Local 沒有 healthcheck，用重試等它起來
        try:
            created = store.ensure_tables()
            print(f"tables ready (created: {created or 'none'})")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"waiting for DynamoDB ({attempt + 1}/30): {type(e).__name__}: {e}")
            time.sleep(1)
    return 1


if __name__ == "__main__":
    sys.exit(main())
