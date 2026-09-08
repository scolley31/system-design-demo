"""DB 連線設定（products / subscriptions / price_aggregations / outbox / checkpoints / crawl_log）。

技術選型定案：正式版這些表用 **PostgreSQL (RDS)**（訂閱 join 查詢、彙總表索引、outbox 交易語意）。
原型用 SQLite 以零設定上手。

注意：append-only 的 `prices` 表走獨立的 price_store（正式版 DynamoDB + Streams），原型時
同樣落在這個 engine 的 `prices` 表（seq autoincrement 模擬 WAL 位置）。
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")
# check_same_thread=False：SQLite 允許被多執行緒存取（worker 在 to_thread、background task）。
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)

if _is_sqlite:
    # WAL 模式：多個背景 task（tailer / worker / scheduler）並發讀寫 SQLite 時不互相卡死。
    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
