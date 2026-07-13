"""DB 連線設定（config + notification_outbox）。

技術選型定案：正式版 config 與 outbox 用 **PostgreSQL**（config 關聯查詢、outbox
交易/批次 supersession 更新都需要交易語意）。原型用 SQLite 以零設定上手。

注意：user_location 走獨立的 location_store（正式版 DynamoDB，high-write），不在這裡。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./quake.db")

# check_same_thread=False：SQLite 允許被多執行緒存取（worker 在 to_thread、background task）。
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
