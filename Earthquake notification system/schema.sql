-- 參考用 DDL（原型以 SQLAlchemy 建表；正式版用 migration 工具）。
-- config 與 outbox 存 PostgreSQL；user_location 正式版存 DynamoDB（此處僅列原型的 SQL 版）。

CREATE TABLE alert_config (
    device_id      VARCHAR(64) PRIMARY KEY,   -- 原型 key；正式版以 user_id 為 key
    magnitude_min  DOUBLE PRECISION NOT NULL DEFAULT 4.0,
    distance_km    DOUBLE PRECISION NOT NULL DEFAULT 50.0,
    status         VARCHAR(16) NOT NULL DEFAULT 'active',   -- active / inactive
    created_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

-- 正式版搬 DynamoDB（partition key=device_id，可設 TTL 屬性自動過期）。
CREATE TABLE user_location (
    device_id  VARCHAR(64) PRIMARY KEY,
    user_id    VARCHAR(64) NOT NULL,
    cell       VARCHAR(20) NOT NULL,   -- H3 index（前端算好直送）
    updated_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX idx_userloc_cell ON user_location(cell);
CREATE INDEX idx_userloc_user ON user_location(user_id);

CREATE TABLE notification_outbox (
    notification_id VARCHAR(64) PRIMARY KEY,  -- hash(alert_id | version | device_id)
    alert_id        VARCHAR(64) NOT NULL,
    version         INTEGER NOT NULL,
    device_id       VARCHAR(64) NOT NULL,
    channel         VARCHAR(16) NOT NULL DEFAULT 'sse',
    -- ENQUEUED | ATTEMPTED | VENDOR_ACCEPTED | FAILED | CANCELLED_SUPERSEDED
    status          VARCHAR(24) NOT NULL DEFAULT 'ENQUEUED',
    updated_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX idx_outbox_alert  ON notification_outbox(alert_id);
CREATE INDEX idx_outbox_device ON notification_outbox(device_id);
