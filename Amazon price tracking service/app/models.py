"""資料模型。所有時間欄位一律存 UTC。

- products：商品目錄 + 優先訊號（訂閱數、查看數、可疑回報）+ 爬取狀態 + CDC 消費端狀態。
- prices：append-only 價格歷史（原型 SQLite；正式版 DynamoDB，PK=product_id、SK=ts，見 price_store.py）。
  `seq` 單調遞增 = 模擬 WAL / DynamoDB Streams 的位置，CDC tailer 靠它 tail。
- subscriptions：PDF 的 (product_id, user_id) 唯一 + (product_id, price_threshold, user_id) 次索引。
- price_reports：extension 回報（匿名 reporter_token）+ 完整性驗證狀態機。
- price_aggregations：預先彙總（daily / weekly / monthly），圖表讀路徑。
- notification_outbox：每個 (subscription, 價格事件) 一筆，冪等關卡 + 狀態帳本。
- cdc_checkpoint：tailer 讀到哪（SQL seq 或 DynamoDB shard SequenceNumber）。
- crawl_log：demo 面板看每次爬取用了哪層 fetcher、耗時、結果。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Product(Base):
    __tablename__ = "products"

    asin: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|unavailable|not_found|blocked

    # 寫入端最近一次已知價格（crawler 或被接受的 extension 回報）
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_price_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # crawler|extension|mock|seed

    # 爬取狀態
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)  # 驅動 due-ness
    last_crawl_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ok|captcha|parse_error|http_error|unavailable
    last_fetcher: Mapped[str | None] = mapped_column(String(16), nullable=True)       # curl_cffi|playwright|mock
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    crawl_inflight_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 去重 enqueue

    # 優先訊號（深入探討 1：優先式爬取）
    subscription_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspicious_flag: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority_boost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # CDC 消費端狀態（只由 price-change worker 更新；與寫入端分離才能算 Δ）
    last_event_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_event_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Price(Base):
    """append-only。原型：seq autoincrement = WAL 位置。正式版：DynamoDB（product_id, ts）+ Streams。"""
    __tablename__ = "prices"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="crawler")  # crawler|extension|mock|seed
    fetcher: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_prices_product_ts", "product_id", "ts"),)


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False)
    price_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(16), nullable=False, default="sse")  # sse|email
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|paused
    last_notified_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # cooldown：再跌破才再通知
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("product_id", "user_id", name="uq_sub_product_user"),          # PDF primary key
        Index("idx_sub_threshold", "product_id", "price_threshold", "user_id"),        # PDF secondary key（covering）
    )


class PriceReport(Base):
    """extension 回報。匿名：只有 reporter_token（限流用），不綁 user_id。"""
    __tablename__ = "price_reports"

    report_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False)
    reported_price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    reporter_token: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    deviation_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="accepted")  # accepted|pending_verify|confirmed|rejected
    verify_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_report_product_status", "product_id", "status"),
        Index("idx_report_token_time", "reporter_token", "created_at"),
    )


class PriceAggregation(Base):
    """預先彙總（PDF 深入探討 3）。PK = (product_id, granularity, bucket_start)。"""
    __tablename__ = "price_aggregations"

    product_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    granularity: Mapped[str] = mapped_column(String(8), primary_key=True)  # daily|weekly|monthly
    bucket_start: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    min_price: Mapped[float] = mapped_column(Float, nullable=False)
    max_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NotificationOutbox(Base):
    """每個 (subscription_id, event_seq) 一筆：冪等關卡 + 狀態帳本。

    status：ENQUEUED | ATTEMPTED | VENDOR_ACCEPTED | FAILED | SKIPPED_COOLDOWN
    notification_id = sha256(subscription_id | event_seq)[:32]。
    """
    __tablename__ = "notification_outbox"

    notification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    old_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_price: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="sse")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ENQUEUED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CdcCheckpoint(Base):
    __tablename__ = "cdc_checkpoint"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)  # prices_seq / ddb:{shard_id}
    position: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CrawlLog(Base):
    __tablename__ = "crawl_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)   # scheduled|manual|verify_report|track
    fetcher: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
