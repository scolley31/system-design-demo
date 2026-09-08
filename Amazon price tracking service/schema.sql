-- 參考用 DDL（原型以 SQLAlchemy 建表；正式版用 migration 工具）。
-- 除 prices 外全部存 PostgreSQL；prices 正式版存 DynamoDB（PK=product_id, SK=ts epoch ms, Streams NEW_IMAGE 當 CDC），
-- 此處列的是原型 / docker compose 的 SQL 版（seq 模擬 WAL 位置）。

CREATE TABLE products (
    asin               VARCHAR(16) PRIMARY KEY,
    title              VARCHAR(512),
    currency           VARCHAR(8)  NOT NULL DEFAULT 'USD',
    status             VARCHAR(16) NOT NULL DEFAULT 'active',   -- active / unavailable / region_locked / not_found / blocked
    -- 寫入端最近已知價
    last_price         DOUBLE PRECISION,
    last_price_at      TIMESTAMP,
    last_price_source  VARCHAR(16),                             -- crawler / extension / mock / seed
    -- 爬取狀態
    last_crawled_at    TIMESTAMP,
    last_seen_at       TIMESTAMP,                               -- 驅動 due-ness（crawl 或被接受的回報）
    last_crawl_status  VARCHAR(16),                             -- ok / captcha / parse_error / http_error / unavailable / region_locked
    last_fetcher       VARCHAR(16),                             -- curl_cffi / playwright / mock
    last_error         VARCHAR(512),
    crawl_inflight_at  TIMESTAMP,
    -- 優先訊號（優先式爬取）
    subscription_count INTEGER NOT NULL DEFAULT 0,
    view_count         INTEGER NOT NULL DEFAULT 0,
    suspicious_flag    INTEGER NOT NULL DEFAULT 0,
    priority_boost     DOUBLE PRECISION NOT NULL DEFAULT 0,
    -- CDC 消費端狀態（只由 price-change worker 更新）
    last_event_price   DOUBLE PRECISION,
    last_event_seq     BIGINT,
    created_at         TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at         TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX idx_products_last_seen ON products(last_seen_at);

-- append-only；正式版 DynamoDB。seq 單調遞增 = 模擬 WAL / Streams 位置。
CREATE TABLE prices (
    seq        BIGSERIAL PRIMARY KEY,
    product_id VARCHAR(16) NOT NULL,
    price      DOUBLE PRECISION NOT NULL,
    currency   VARCHAR(8)  NOT NULL DEFAULT 'USD',
    source     VARCHAR(16) NOT NULL DEFAULT 'crawler',
    fetcher    VARCHAR(16),
    ts         TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX idx_prices_product_ts ON prices(product_id, ts);

CREATE TABLE subscriptions (
    subscription_id     VARCHAR(32) PRIMARY KEY,
    user_id             VARCHAR(64) NOT NULL,
    product_id          VARCHAR(16) NOT NULL,
    price_threshold     DOUBLE PRECISION NOT NULL,
    notification_type   VARCHAR(16) NOT NULL DEFAULT 'sse',    -- sse / email
    email               VARCHAR(256),
    status              VARCHAR(16) NOT NULL DEFAULT 'active',
    last_notified_price DOUBLE PRECISION,                       -- cooldown：再跌破才再通知
    last_notified_at    TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at          TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    CONSTRAINT uq_sub_product_user UNIQUE (product_id, user_id)          -- PDF primary key
);
CREATE INDEX idx_sub_threshold ON subscriptions(product_id, price_threshold, user_id);  -- PDF secondary key（covering）
CREATE INDEX idx_sub_user      ON subscriptions(user_id);

CREATE TABLE price_reports (
    report_id      BIGSERIAL PRIMARY KEY,
    product_id     VARCHAR(16) NOT NULL,
    reported_price DOUBLE PRECISION NOT NULL,
    currency       VARCHAR(8)  NOT NULL DEFAULT 'USD',
    reporter_token VARCHAR(64) NOT NULL,                        -- 匿名限流用，不綁 user
    baseline_price DOUBLE PRECISION,
    deviation_pct  DOUBLE PRECISION,
    status         VARCHAR(16) NOT NULL DEFAULT 'accepted',     -- accepted / pending_verify / confirmed / rejected
    verify_price   DOUBLE PRECISION,
    created_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    resolved_at    TIMESTAMP
);
CREATE INDEX idx_report_product_status ON price_reports(product_id, status);
CREATE INDEX idx_report_token_time     ON price_reports(reporter_token, created_at);

CREATE TABLE price_aggregations (
    product_id   VARCHAR(16) NOT NULL,
    granularity  VARCHAR(8)  NOT NULL,                          -- daily / weekly / monthly
    bucket_start TIMESTAMP   NOT NULL,
    avg_price    DOUBLE PRECISION NOT NULL,
    min_price    DOUBLE PRECISION NOT NULL,
    max_price    DOUBLE PRECISION NOT NULL,
    close_price  DOUBLE PRECISION NOT NULL,
    sample_count INTEGER NOT NULL,
    updated_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (product_id, granularity, bucket_start)         -- PDF (product_id, granularity, date)
);

CREATE TABLE notification_outbox (
    notification_id VARCHAR(64) PRIMARY KEY,                    -- sha256(subscription_id | event_seq)[:32]
    subscription_id VARCHAR(32) NOT NULL,
    user_id         VARCHAR(64) NOT NULL,
    product_id      VARCHAR(16) NOT NULL,
    event_seq       BIGINT NOT NULL,
    old_price       DOUBLE PRECISION,
    new_price       DOUBLE PRECISION NOT NULL,
    threshold       DOUBLE PRECISION NOT NULL,
    channel         VARCHAR(16) NOT NULL DEFAULT 'sse',
    -- ENQUEUED | ATTEMPTED | VENDOR_ACCEPTED | FAILED | SKIPPED_COOLDOWN
    status          VARCHAR(24) NOT NULL DEFAULT 'ENQUEUED',
    created_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    updated_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX idx_outbox_user    ON notification_outbox(user_id);
CREATE INDEX idx_outbox_product ON notification_outbox(product_id);

CREATE TABLE cdc_checkpoint (
    consumer   VARCHAR(64) PRIMARY KEY,                         -- prices_seq / ddb:{shard_id}
    position   VARCHAR(128) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE TABLE crawl_log (
    id          BIGSERIAL PRIMARY KEY,
    product_id  VARCHAR(16) NOT NULL,
    reason      VARCHAR(16) NOT NULL,                           -- scheduled / manual / verify_report / track
    fetcher     VARCHAR(16),
    status      VARCHAR(16) NOT NULL,
    price       DOUBLE PRECISION,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error       VARCHAR(512),
    created_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
CREATE INDEX idx_crawl_log_time ON crawl_log(created_at);
