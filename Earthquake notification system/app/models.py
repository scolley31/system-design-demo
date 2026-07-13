"""資料模型。所有時間欄位一律存 UTC。

原型三張表都在同一個 SQLAlchemy engine（SQLite / Postgres）：
- alert_config：使用者通知條件（震度門檻、距離）。
- user_location：裝置最新位置（只存 H3 cell，不存 raw lat/long）。正式版搬到 DynamoDB（見 location_store.py）。
- notification_outbox：每個 device-level 通知的 idempotency gate + 狀態帳本。

原型簡化：config 與 location 都以 `device_id` 為 key（一個瀏覽器分頁 = 一台裝置）。
正式版 config 以 `user_id` 為 key（一個 user 的多台裝置共用一份 config），location 才以 device 為 key。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AlertConfig(Base):
    __tablename__ = "alert_config"

    # 原型：以 device_id 當 key（讓一個瀏覽器多分頁 = 多台裝置各自設定，方便 demo）。
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    magnitude_min: Mapped[float] = mapped_column(Float, nullable=False, default=4.0)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active / inactive
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UserLocation(Base):
    """只存 H3 cell（前端算好直送，server 不換算）→ 隱私友善、寫入輕。
    正式版此表搬到 DynamoDB（high-write ≈ 5.5K/s），見 location_store.py。
    """
    __tablename__ = "user_location"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cell: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # H3 index
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class NotificationOutbox(Base):
    """每個 (alert_id, version, device_id) 一筆，作為冪等關卡 + 最新狀態帳本。

    status：ENQUEUED | ATTEMPTED | VENDOR_ACCEPTED | FAILED | CANCELLED_SUPERSEDED
    terminal states = ATTEMPTED 之後的所有狀態（見 broadcast/worker 的 TERMINAL）。
    notification_id = hash(alert_id | version | device_id)。
    """
    __tablename__ = "notification_outbox"

    notification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="sse")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ENQUEUED")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
