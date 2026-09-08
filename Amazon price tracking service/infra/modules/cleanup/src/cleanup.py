"""Data cleanup：刪除超過保留窗的 notification_outbox / crawl_log / 已結案 price_reports 列。

單一來源:可當 AWS Lambda（lambda_handler）或本機 CLI 跑。
用 SQLAlchemy + text() 原生 SQL —— 本機對 SQLite、Lambda 對 Postgres（純 Python pg8000
驅動,避免二進位打包）皆可。

規則（保留天數可由環境變數調整）:
- notification_outbox : updated_at < now - OUTBOX_RETENTION_DAYS 的列物理刪除
  （outbox 是冪等關卡 + 狀態帳本；超過保留窗的舊列已無查詢價值,清掉省空間）。
- crawl_log           : created_at < now - CRAWL_LOG_RETENTION_DAYS 的列物理刪除
  （每次爬取一筆的觀測日誌,只供近期除錯 / 儀表板）。
- price_reports       : status IN ('confirmed','rejected','accepted') 且
  created_at < now - CRAWL_LOG_RETENTION_DAYS 的列物理刪除
  （已結案的眾包回報；pending_verify 仍待重爬驗證,不動）。

注意:prices（DynamoDB append-only 價格歷史）與 price_aggregations（讀路徑預先彙總）
皆不在此清 —— 歷史價格是產品本身。
"""
import os
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

_REPORT_DONE = "('confirmed', 'rejected', 'accepted')"


def _engine(database_url: str):
    # Lambda 環境改用純 Python 的 pg8000 驅動（免 psycopg2 二進位打包）
    url = database_url.replace("+psycopg2", "+pg8000")
    return create_engine(url, future=True)


def run_cleanup(database_url, outbox_retention_days=30, crawl_log_retention_days=7):
    now = datetime.utcnow()
    outbox_cutoff = now - timedelta(days=outbox_retention_days)
    log_cutoff = now - timedelta(days=crawl_log_retention_days)
    engine = _engine(database_url)
    with engine.begin() as conn:
        outbox_deleted = conn.execute(
            text("DELETE FROM notification_outbox WHERE updated_at < :cutoff"),
            {"cutoff": outbox_cutoff},
        ).rowcount
        crawl_log_deleted = conn.execute(
            text("DELETE FROM crawl_log WHERE created_at < :cutoff"),
            {"cutoff": log_cutoff},
        ).rowcount
        reports_deleted = conn.execute(
            text(
                f"DELETE FROM price_reports WHERE status IN {_REPORT_DONE} "
                "AND created_at < :cutoff"
            ),
            {"cutoff": log_cutoff},
        ).rowcount
    result = {
        "notification_outbox_deleted": int(outbox_deleted or 0),
        "crawl_log_deleted": int(crawl_log_deleted or 0),
        "price_reports_deleted": int(reports_deleted or 0),
    }
    print("cleanup result:", result)
    return result


def _days(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


def lambda_handler(event, context):
    return run_cleanup(
        os.environ["DATABASE_URL"],
        _days("OUTBOX_RETENTION_DAYS", "30"),
        _days("CRAWL_LOG_RETENTION_DAYS", "7"),
    )


if __name__ == "__main__":
    run_cleanup(
        os.environ.get("DATABASE_URL", "sqlite:///./price.db"),
        _days("OUTBOX_RETENTION_DAYS", "30"),
        _days("CRAWL_LOG_RETENTION_DAYS", "7"),
    )
