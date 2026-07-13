"""Data cleanup：刪除超過保留窗的 notification_outbox 列。

單一來源:可當 AWS Lambda（lambda_handler）或本機 CLI 跑。
用 SQLAlchemy + text() 原生 SQL —— 本機對 SQLite、Lambda 對 Postgres（純 Python pg8000
驅動,避免二進位打包）皆可。

規則（保留天數可由環境變數調整）:
- notification_outbox : updated_at < now - OUTBOX_RETENTION_DAYS 的列物理刪除
  （outbox 是冪等關卡 + 狀態帳本；超過保留窗的舊列已無查詢價值,清掉省空間）。

注意:user_location 由 DynamoDB TTL 自動過期,不在此清（見 infra data 模組的 ttl 設定）。
"""
import os
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

_COND = "updated_at < :cutoff"


def _engine(database_url: str):
    # Lambda 環境改用純 Python 的 pg8000 驅動（免 psycopg2 二進位打包）
    url = database_url.replace("+psycopg2", "+pg8000")
    return create_engine(url, future=True)


def run_cleanup(database_url, outbox_retention_days=30):
    now = datetime.utcnow()
    params = {"cutoff": now - timedelta(days=outbox_retention_days)}
    engine = _engine(database_url)
    with engine.begin() as conn:
        outbox_deleted = conn.execute(
            text(f"DELETE FROM notification_outbox WHERE {_COND}"), params
        ).rowcount
    result = {"notification_outbox_deleted": int(outbox_deleted or 0)}
    print("cleanup result:", result)
    return result


def lambda_handler(event, context):
    return run_cleanup(
        os.environ["DATABASE_URL"],
        int(os.environ.get("OUTBOX_RETENTION_DAYS", "30")),
    )


if __name__ == "__main__":
    run_cleanup(
        os.environ.get("DATABASE_URL", "sqlite:///./quake.db"),
        int(os.environ.get("OUTBOX_RETENTION_DAYS", "30")),
    )
