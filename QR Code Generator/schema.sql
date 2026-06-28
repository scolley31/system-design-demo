-- QR Code Generator — Database Schema (DDL)
--
-- ORM 定義（app/models.py）為 source of truth；本檔是人類可讀的 DDL 文件。
-- 原型：SQLite。正式版：PostgreSQL（型別等價，並改用 Alembic migration 管理，而非 create_all）。
-- 所有時間欄位一律存 UTC。

-- =========================================================
-- 短碼 → 目標 URL 對映（主表）
-- =========================================================
CREATE TABLE url_mappings (
    id            INTEGER      NOT NULL PRIMARY KEY,   -- SQLite 自動 rowid；PG 用 SERIAL/IDENTITY
    owner_id      VARCHAR(64)  NOT NULL,               -- 擁有者 = Cognito sub（本機=local-dev）；多租戶隔離
    token         VARCHAR(8)   NOT NULL,               -- 8 碼 Base62 短碼（第 3 題）
    original_url  TEXT         NOT NULL,               -- 正規化後的目標 URL（第 9 題）
    created_at    DATETIME     NOT NULL,
    updated_at    DATETIME     NOT NULL,               -- 更新時自動帶新值（ORM onupdate）
    expires_at    DATETIME         NULL,               -- 可選過期時刻（第 13 題，惰性過期）
    is_deleted    BOOLEAN      NOT NULL DEFAULT 0      -- 軟刪除（第 12 題，支撐 410）
);

-- token 唯一索引：
--   (1) 碰撞安全網 —— 直接插入 + 例外重試（第 5 題）
--   (2) redirect 以 token 查找走 O(log n) B-tree，避免全表掃描（第 2 題 indexing，<100ms）
CREATE UNIQUE INDEX ix_url_mappings_token ON url_mappings (token);

-- owner_id 索引：加速「列我的 QR」WHERE owner_id=?（Auth & Isolation 多租戶）
CREATE INDEX ix_url_mappings_owner_id ON url_mappings (owner_id);


-- =========================================================
-- 掃描事件明細（分析）
--   原型：同庫明細 + 即時 GROUP BY。
--   正式版：獨立分析庫 + 預聚合每日計數表 daily_counts(token, date, count)（第 14 題）。
-- =========================================================
CREATE TABLE scan_events (
    id          INTEGER      NOT NULL PRIMARY KEY,
    token       VARCHAR(8)   NOT NULL,
    scanned_at  DATETIME     NOT NULL,
    user_agent  VARCHAR(500)     NULL,
    ip_address  VARCHAR(45)      NULL                  -- 容納 IPv6
);

-- 複合索引：依 token + 時間查分析（WHERE token=? GROUP BY date）
CREATE INDEX idx_token_scanned ON scan_events (token, scanned_at);
