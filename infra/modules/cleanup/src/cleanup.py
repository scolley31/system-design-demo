"""Data cleanup：清掉過期 QR 與軟刪除超期的列（連帶刪其 scan_events）。

單一來源:可當 AWS Lambda（lambda_handler）或本機 CLI 跑。
用 SQLAlchemy + text() 原生 SQL —— 本機對 SQLite、Lambda 對 Postgres（純 Python pg8000
驅動,避免二進位打包）皆可。

規則（保留天數可由環境變數調整）:
- 過期 QR    : expires_at < now - EXPIRED_GRACE_DAYS
- 軟刪除超期 : is_deleted = true AND updated_at < now - DELETED_RETENTION_DAYS
"""
import os
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

# 同時命中兩類要刪的列
_COND = (
    "(expires_at IS NOT NULL AND expires_at < :exp) "
    "OR (is_deleted = :tru AND updated_at < :del)"
)


def _engine(database_url: str):
    # Lambda 環境改用純 Python 的 pg8000 驅動（免 psycopg2 二進位打包）
    url = database_url.replace("+psycopg2", "+pg8000")
    return create_engine(url, future=True)


def run_cleanup(database_url, expired_grace_days=30, deleted_retention_days=30):
    now = datetime.utcnow()
    params = {
        "exp": now - timedelta(days=expired_grace_days),
        "del": now - timedelta(days=deleted_retention_days),
        "tru": True,
    }
    engine = _engine(database_url)
    with engine.begin() as conn:
        # 先刪連帶的 scan_events（用 subquery,需在刪 url_mappings 之前）
        scan_deleted = conn.execute(
            text(f"DELETE FROM scan_events WHERE token IN (SELECT token FROM url_mappings WHERE {_COND})"),
            params,
        ).rowcount
        url_deleted = conn.execute(
            text(f"DELETE FROM url_mappings WHERE {_COND}"), params
        ).rowcount
    result = {
        "url_mappings_deleted": int(url_deleted or 0),
        "scan_events_deleted": int(scan_deleted or 0),
    }
    print("cleanup result:", result)
    return result


def lambda_handler(event, context):
    return run_cleanup(
        os.environ["DATABASE_URL"],
        int(os.environ.get("EXPIRED_GRACE_DAYS", "30")),
        int(os.environ.get("DELETED_RETENTION_DAYS", "30")),
    )


if __name__ == "__main__":
    run_cleanup(
        os.environ.get("DATABASE_URL", "sqlite:///./qr_code.db"),
        int(os.environ.get("EXPIRED_GRACE_DAYS", "30")),
        int(os.environ.get("DELETED_RETENTION_DAYS", "30")),
    )
