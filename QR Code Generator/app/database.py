"""DB 連線設定。

第 16 題：正式版用 PostgreSQL（單 primary + 多 read replica + failover），
讀走 replica、寫走 primary。原型用 SQLite 以零設定上手。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./qr_code.db")

# check_same_thread=False 讓 SQLite 可被多執行緒存取（含 BackgroundTasks）。
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
