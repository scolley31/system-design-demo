"""資料模型。所有時間欄位一律存 UTC（見 DESIGN.md 第 13 題）。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class UrlMapping(Base):
    __tablename__ = "url_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 第 3 題：8 碼 Base62 token；第 5 題：UNIQUE 為碰撞安全網（直接插入 + 例外重試）。
    # 第 2 題的 redirect 靠 token 查找，加 index 避免全表掃描。
    token: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 第 12 題：軟刪除（支撐 410 語意、保留分析、避免 token 回收風險）。
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ScanEvent(Base):
    """第 14 題：原型用同庫明細表 + 即時 GROUP BY；
    正式版改獨立分析庫 + 預聚合每日計數表（token, date, count）。
    """
    __tablename__ = "scan_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(8), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    __table_args__ = (Index("idx_token_scanned", "token", "scanned_at"),)
