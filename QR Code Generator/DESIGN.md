# QR Code Generator — 設計討論文件（含完整討論與優劣）

## Context

讀完 PDF（系統設計面試題）與 GitHub repo（bohr109/build-moat-live-sessions/qr_code_generator，FastAPI 練習）後，討論如何實作 QR code generator。本次範圍：**只討論設計**（暫不寫程式）、功能對齊 repo 的**完整版**、技術棧 **Python + FastAPI**。本文件記錄 16 題逐一討論的選項、優劣與定案。

---

## 三大關鍵架構決策（決策地圖）

整個系統由三個環環相扣的決策定調——每個決策自然導向下一個：

| 關鍵決策 | 選擇 | 為什麼 / 連鎖反應 |
|---|---|---|
| **① 動態 vs 靜態 QR** | **動態**（QR 編 short URL → server redirect） | 需求是「可修改 + 可追蹤」；靜態改不了、無法統計。代價：server 變 SPOF → 逼出 cache/CDN/monitoring。靜態反而適合一次性印刷/離線/醫療等不可改場景。 |
| **② Token 生成策略** | **SHA-256 + nonce + Base62（8 碼）** | 演進：隨機（碰撞靠運氣）→ 純 hash（同 URL 同 token、無法獨立追蹤）→ **+nonce**（解決同 URL、碰撞換 nonce 確定性重試 + DB 兜底）。流量再上去可換 Pre-generated Pool / Snowflake。 |
| **③ 301 vs 302 redirect** | **302（暫時）** | 動態 QR → 需要 redirect → 301 被瀏覽器永久快取、跳過我們 server（分析流失、改不了）；302 每次回源 → 可改/刪/分析。代價：latency → 逼出 cache + CDN。 |

→ ①決定要 server、②決定短碼怎麼來、③決定 redirect 怎麼回，三者串成主架構。下表 16 項為其下的實作層決策。

## 決策總表

| # | 題目 | 決策 | 與 repo 差異 |
|---|------|------|------|
| 1 | URL 長度上限 | **2048** | PDF 寫 20，採 repo |
| 2 | API 命名風格 | **repo 風格 + v1**：`/api/v1/qr/*`、`/r/{token}`、PATCH | 加 v1 |
| 3 | Token 演算法/長度 | **SHA-256(url+nonce)→Base62→8 碼，不加 user_id** | 改良 repo（repo 用 7 碼） |
| 4 | 同 URL 去重 | **不去重**，每次新 token | 對齊 repo |
| 5 | 碰撞重試 | **直接插入 + UNIQUE 例外重試**，上限 10 | 改良 repo（避免 race） |
| 6 | Redirect 狀態碼 | **302** | 對齊兩份資料 |
| 7 | Cache | **Redis 分散式快取** | repo 用記憶體 dict |
| 8 | Scan 寫入 | **非同步/佇列** | repo 同步寫 |
| 9 | URL 正規化 | **保守正規化**（只 lower-case host） | 修正 repo bug |
| 10 | 惡意網域 | **可設定黑名單 + Safe Browsing + SSRF 阻擋** | repo 僅靜態 set |
| 11 | 錯誤語意 | **404/410 區分** | 對齊 repo |
| 12 | 刪除 | **軟刪除** | 對齊 repo |
| 13 | 過期/清理 | **惰性過期 + cron 兜底** | repo 僅惰性 |
| 14 | 分析儲存 | **獨立分析庫 + 預聚合** | repo 同庫明細 |
| 15 | QR 圖片 | **預生成 + object store + CDN** | repo 即時生成 |
| 16 | DB 擴展 | **單 primary + read replica + failover** | 對齊 PDF |

---

## 需求摘要

**FR**：提交 URL → 回 token + QR 圖；掃描 302 導向；可改/軟刪/設過期；掃描分析；URL 驗證。
**NFR**：24/7 HA；redirect < 100ms；10 億 QR、1 億用戶；read-heavy ≈ 100:1。
**容量**：1B × ~200B ≈ 200GB；~5,800 redirect/秒。

---

## API（定案）

| 方法 | 路徑 | 說明 | 回應 |
|------|------|------|------|
| POST | `/api/v1/qr/create` | body `{url, expires_at?}` | `{token, short_url, qr_code_url, original_url}` |
| GET | `/r/{token}` | 掃描入口：cache→DB→302 / 404 / 410 | 302 |
| GET | `/api/v1/qr/{token}` | metadata | 已刪→404 |
| PATCH | `/api/v1/qr/{token}` | 改 url / expires_at（→ invalidate cache） | metadata |
| DELETE | `/api/v1/qr/{token}` | 軟刪除（→ invalidate cache） | `{detail}` |
| GET | `/api/v1/qr/{token}/image` | 預生成 → object store/CDN URL | image/png 或 CDN URL |
| GET | `/api/v1/qr/{token}/analytics` | 讀預聚合 | `{token, total_scans, scans_by_day[]}` |

**`CreateResponse` 四欄**（`create` 一次回齊，前端不用再往返就能渲染短網址 + QR 圖）：

| 欄位 | 是什麼 | 用途 |
|---|---|---|
| `token` | 8 碼 Base62 短碼，這筆 QR 的唯一識別 | 後續所有操作（GET/PATCH/DELETE/image/analytics）的 handle |
| `short_url` | 可掃描/分享的完整短網址 `BASE_URL + /r/{token}` | 編進 QR 的內容；打它 → 302 導向原始 URL |
| `qr_code_url` | 取 QR PNG 的網址（指 `/image` 端點） | 前端 `<img src>` |
| `original_url` | **正規化後**的目標 URL（`validate_url` 回傳值） | 讓 client 確認實際存入的值（host 已小寫、去尾斜線，可能異於輸入） |

> 路由用 `response_model=CreateResponse`，FastAPI 自動做序列化、回應驗證與 OpenAPI 文件 —— 它同時是資料容器與 API 合約。

---

## 資料模型

**`url_mappings`**：`id` PK、`token` String(8) UNIQUE+index、`original_url` Text、`created_at`/`updated_at`、`expires_at` nullable、`is_deleted` Boolean。所有時間存 **UTC**。

**分析（獨立）**：明細 `scan_events(token, scanned_at, user_agent, ip)`（量大時分庫/歸檔）+ 預聚合 `daily_counts(token, date, count)`，`/analytics` 只讀聚合表。

完整 DDL 見專案 `schema.sql`（ORM `app/models.py` 為 source of truth）：

```sql
CREATE TABLE url_mappings (
    id            INTEGER      NOT NULL PRIMARY KEY,
    token         VARCHAR(8)   NOT NULL,              -- 8 碼 Base62 短碼
    original_url  TEXT         NOT NULL,              -- 正規化後 URL
    created_at    DATETIME     NOT NULL,
    updated_at    DATETIME     NOT NULL,
    expires_at    DATETIME         NULL,              -- 可選過期（惰性過期）
    is_deleted    BOOLEAN      NOT NULL DEFAULT 0     -- 軟刪除
);
CREATE UNIQUE INDEX ix_url_mappings_token ON url_mappings (token);  -- UNIQUE 兼 redirect 查找索引

CREATE TABLE scan_events (
    id          INTEGER      NOT NULL PRIMARY KEY,
    token       VARCHAR(8)   NOT NULL,
    scanned_at  DATETIME     NOT NULL,
    user_agent  VARCHAR(500)     NULL,
    ip_address  VARCHAR(45)      NULL
);
CREATE INDEX idx_token_scanned ON scan_events (token, scanned_at);  -- 分析複合索引
```

---

# 逐題討論與優劣

## 第 1 題：URL 長度上限

**衝突點**：PDF 寫「ASCII 最長 20 字元」，repo 用 2048。

| 選項 | 優 | 劣 |
|---|---|---|
| **2048（採用）** | 業界 URL 實務上限，能容 query string/UTM；dynamic QR 下原始 URL 長不影響 QR 複雜度 | 略增儲存（可忽略） |
| 20（PDF） | 忠於原題 | `https://example.com` 就 19 字元，幾乎不可用，僅像考試簡化假設 |

**定案**：2048。理由：真實 URL 常帶參數，20 太短；dynamic QR 編的是短碼不是原始 URL，長 URL 不影響掃描。

## 第 2 題：API 命名風格

**衝突點**：repo `/api/qr/*` + `/r/{token}`；PDF `v1/qr_code`、PUT 更新。

| 面向 | repo | PDF |
|---|---|---|
| 掃描入口 | 獨立超短 `/r/{token}`，利於單獨走 cache/CDN | 無獨立短路徑 |
| 版本化 | 無 | 有 `v1`，改版不破壞舊 client |
| 更新語意 | PATCH 部分更新（貼合「只改 URL」） | PUT 整筆取代 |

**定案**：repo 風格 + 加 `v1` → 管理 `/api/v1/qr/*`、掃描 `/r/{token}`、PATCH。取兩者優點：短路徑掃描體驗 + 版本化。

## 第 3 題：Token 演算法與長度

**(a) hash vs 純隨機**

| 選項 | 優 | 劣 |
|---|---|---|
| **Hash（SHA-256+nonce+Base62，採用）** | 教學上好講熵/Base62；對齊 repo | 加 nonce 後「確定性」優勢已消失，行為其實近似純隨機，仍須查 DB 防碰撞 |
| 純隨機（secrets） | 工程更乾淨、天生不可預測 | 教學敘事較弱 |

**(b) nonce 是什麼，以及它「不」負責什麼（重要釐清）**

- nonce = "number used once"，每次都變動的值（repo 用 `時間戳_重試次數`），加進 hash 輸入。
- SHA-256 是**確定性**的：同輸入永遠同輸出。第 4 題決定「同 URL 不去重、每次產生獨立 token」，所以必須用 nonce 打破「同 URL → 同 token」。
- ⚠️ **nonce 只解決「確定性」，不解決「碰撞」**。不論純隨機或 hash+nonce，最終都截斷成固定長度的 Base62 token，輸出空間相同 → 碰撞機率也相同。nonce 讓「輸入不同」，但不同輸入截斷後仍可能落在同一個 token 上。
- 完整 SHA-256（2^256）幾乎不可能碰撞，但我們**砍到只剩 N 碼**後，真正的空間是 62^N，碰撞就活在這裡。

**(c) 長度：7 碼 vs 8 碼**

| 長度 | 空間 | 10 億規模預期碰撞次數 | 首次碰撞約在 |
|---|---|---|---|
| 7 碼 | 62^7 ≈ 3.52×10^12（~41.7 bit） | ~142,000 次 | ~220 萬筆 |
| **8 碼（採用）** | 62^8 ≈ 2.18×10^14（~47.6 bit） | ~2,290 次（少 62 倍） | ~1,740 萬筆 |

每多 1 碼，空間 ×62、預期碰撞次數降到 1/62。8 碼在 10 億規模下單筆插入碰撞率僅 ~0.0005%，短碼只多 1 字元，CP 值高 → 採 8 碼（repo 原用 7 碼）。

**(d) user_id**：已有 nonce 保證每次不同，**不加 user_id**（token 不帶用戶資訊，隱私較佳）。

**定案**：SHA-256+nonce+Base62、**8 碼**、不加 user_id。

**(e) Hash → Encode 實作拆解**

```
"url" + nonce ──.encode()──► bytes(UTF-8) ──sha256().digest()──► 32 bytes
   ──int.from_bytes(big)──► 256-bit 大整數 ──base62 除62取餘──► ~43 字元 ──[:8]──► token
```

- **Hash（攪亂）**：`sha256(...).digest()` 把任意輸入壓成固定 32 bytes 指紋；確定性、雪崩、單向。`.digest()` 回原始 bytes（`.hexdigest()` 則回 64 字元 hex）。
- **Encode（變可讀短碼）**：`int.from_bytes(data,"big")` 把 32 bytes 當一個大整數，再**不斷除以 62、記餘數**轉成 62 進位（餘數 0–61 對映 `0-9a-zA-Z`）；`reversed` 因餘數從最低位算起。選 Base62 而非 Base64：不含 `/ + =`，URL 安全。
- **截斷**：62^43 ≈ 2^256，取前 8 碼把空間砍到 62^8 ≈ 2.18×10^14（碰撞即源於此）。

⚠ 程式裡有**兩個 encode** 別混淆：`(url+nonce).encode()` 是字元編碼（文字→bytes，給 hash 吃）；`base62_encode()` 是 bytes→可讀文字（給網址用）。

小範例（除 62 取餘）：整數 `3842` → `3842÷62 = 商61 餘60 → 'Y'`、`61÷62 = 商0 餘61 → 'Z'` → reversed → `"ZY"`。

**(f) 其他 token 策略與比較（演進路徑）**

展示「從最直覺逐步推到最合適」的思考路徑：

| 策略 | 簡單度 | 唯一性 | 可預測性 | 適用場景 |
|---|---|---|---|---|
| Auto-increment | 高 | 高 | **高（危險：可枚舉）** | 內部系統 |
| 隨機字串 / UUID 截短 | 中 | 中 | 低 | 低流量 |
| 純 hash(URL) | 中 | **低（同 URL 同 token）** | 低 | ✗ 無法獨立追蹤 |
| **SHA-256+nonce+Base62（採用）** | 中 | 高 | 低 | 通用方案 |
| Pre-generated Pool | 低 | 高 | 低 | 高流量（寫入零碰撞） |
| Snowflake-like | 低 | 高 | 中 | 分散式系統 |

- **演進**：隨機字串（碰撞靠運氣、auto-increment 還可枚舉）→ 純 hash（同 URL 永遠同 token、無法針對不同使用者獨立追蹤）→ **SHA-256+nonce**（解決同 URL 問題；碰撞時換 nonce 確定性重試，不是再碰運氣；DB UNIQUE 兜底）。
- **Pre-generated Pool**：預先產一批 unique ID 存 DB，寫入時直接分配 → **寫入路徑零碰撞、零重試**，適合超高寫入；代價是要維護 pool。
- **Snowflake-like**：`timestamp(41) + machine ID(10) + sequence(12) = 64 bits`，各節點本地生成、不需協調，適合分散式；但產出是數字、可預測性中等、且比短碼長。
- 我們選 SHA-256+nonce 是**通用方案**（唯一性高、不可預測、實作中等）；寫入流量再上去可換 **Pre-generated Pool**（第 5 題的碰撞重試在零碰撞下就免了）。

## 第 4 題：同 URL 去重

**核心**：dynamic QR 每個必須是獨立實體，不只是省空間問題。

| 選項 | 優 | 劣 |
|---|---|---|
| **不去重（採用）** | 改/刪/分析/過期互不影響，符合 dynamic QR 本質 | 同 URL 多筆，略費空間 |
| 去重共用 token | 最省空間 | A 改/刪會影響 B；分析混在一起；過期時間衝突 |

**定案**：不去重。去重只適合「純靜態、不可變、不需分析」場景（那更接近 static QR）。

## 第 5 題：碰撞重試

**為什麼一定要這層（接第 3 題）**：token 截斷成固定長度後，碰撞在 10 億規模下**必然會發生多次**（8 碼預期約 2,290 次）。nonce 不防碰撞，所以 `UNIQUE + 重試`不是裝飾，而是真正的安全網——少了它，碰撞會**靜默覆蓋**別人的 QR（舊 QR 突然指向別人的 URL），屬災難級錯誤。好在每筆插入撞車機率極低（8 碼 ~0.0005%），重試幾乎都在第 2 次成功。

**race condition 點**：repo「先 SELECT 查存在 → 再 INSERT」在高並發下兩請求可能同時查到不存在 → 都插入 → 第二個炸 UNIQUE。

| 選項 | 優 | 劣 |
|---|---|---|
| **直接插入 + 例外重試（採用）** | 無 race；少一次 DB 查詢；靠 DB UNIQUE 把關 | 須處理 IntegrityError |
| 先查再插（repo） | 直覺好懂 | 高並發有 race；多一次查詢 |

**重試上限**：10 次（連撞 10 次機率趨近零；3~5 次其實已夠，10 給安全邊際）。

**定案**：直接插入 + UNIQUE 例外重試，上限 10。

## 第 6 題：302 vs 301

| 面向 | 301 永久 | 302 暫時（採用） |
|---|---|---|
| 瀏覽器快取 | 會快取，後續跳過我方 server | 不快取，每次回源 |
| 改/刪目標 | 失效慢，舊裝置可能永遠跳舊址 | 立即生效 |
| 分析 | 漏記 | 每次都記得到 |
| 延遲 | 第二次起極快 | 每次一趟 server |
| SEO | 傳權重 | 不傳 |

**定案**：302。dynamic QR 的核心價值（可改/刪/分析）301 全犧牲；延遲代價由第 7/8/15 題的 cache/CDN/非同步補到 <100ms。

## 第 7 題：Cache

**一致性前提**：第 2 題選了 stateless 水平擴展 → cache 不能放行程內。

| 選項 | 優 | 劣 |
|---|---|---|
| **Redis（採用）** | 全 server 共享、hit rate 高、invalidation 全域生效；與 stateless 自洽 | 多一元件、成本/運維 |
| 記憶體 dict（repo） | 零依賴最簡單 | 多台 server 時 hit rate 低；PATCH/DELETE 只清得掉本機 → 其他台回舊 URL（正確性 bug） |
| 兩層 local+Redis | 最高效能 | 最複雜，原型不需 |

**定案**：Redis。原型可暫用 dict 並註明替換點。

## 第 8 題：Scan 寫入（同步 vs 非同步）

**這題在處理什麼**：每次掃 QR 打到 `/r/{token}` 時，我們要做兩件事——①記一筆掃描紀錄（給分析用）②回 302 跳轉。「Scan 寫入」指第①件。問題是：紀錄要**卡在使用者面前寫完**（同步），還是**先放使用者走、背景再補**（非同步）？核心判斷：跳轉不能等、記帳可以慢，所以把「可容忍延遲的分析」從「不可容忍延遲的跳轉」路徑上搬走。

**延遲前提**：redirect 是 <100ms 關鍵路徑 + 302 每次回源（不被瀏覽器快取，每次都回我方 server）+ ~5,800 QPS（高頻），這條路徑上每多做一件事都被放大 5,800 倍/秒。

| 選項 | 優 | 劣 |
|---|---|---|
| **非同步/佇列（採用）** | redirect 路徑幾乎零額外成本 | 分析秒級延遲、極端當機掉少量（可容忍） |
| 同步寫（repo） | 分析即時準確 | 每次 redirect +1 寫入 commit，拖慢且壓 DB |
| Redis 近似計數 | 最高效 | 每日明細要另想辦法 |

**定案**：非同步/佇列（原型可用 `BackgroundTasks` 輕量版）。分析資料天生可容忍延遲與少量遺失，不該擋在 redirect 上。

## 第 9 題：URL 正規化（含 repo bug）

repo 做 `url.lower().rstrip("/")` + 強制 http→https。

| 規則 | 評價 |
|---|---|
| 整串 `url.lower()` | ⚠️ bug：path/query 大小寫敏感，`/AbC`≠`/abc`，硬轉小寫會導錯頁。應**只**小寫 host |
| `rstrip("/")` | ⚠️ 會刪多個尾斜線；只移「單一」尾斜線較安全 |
| 強制 http→https | ⚠️ 有些站無 https 版，強制升級會掛；應保留原 scheme |
| scheme 限 http/https | ✅ 擋 `javascript:`/`file:` |

**定案**：保守正規化 = 只 lower-case host + 移單一尾斜線 + 保留 path/query 與原 scheme。回答 PROMPT Q4：host 大小寫不敏感、尾斜線可忽略使等價 URL 對映單一資源，但不該整串小寫或強改 scheme。

## 第 10 題：惡意網域阻擋

| 選項 | 優 | 劣 |
|---|---|---|
| 靜態黑名單（repo） | 原型/教學夠用 | 無法涵蓋真實威脅，改要重部署 |
| 可設定黑名單(DB/設定) | 可動態更新 | 仍需自行維護名單 |
| **+ 外部 Safe Browsing API（採用）** | production 等級、涵蓋實時威脅 | 延遲/成本/外部依賴 |

**補強**：加 **SSRF/內網位址阻擋**（`localhost`/`127.*`/`169.254.*`/私有網段）——比假網域黑名單實際得多。

**定案**：可設定黑名單 + Safe Browsing + SSRF 防護（原型保留靜態 set 示意）。

## 第 11 題：錯誤語意（404 vs 410）

| 情況 | HTTP | 語意 |
|---|---|---|
| token 從沒存在 | 404 | 資源不存在 |
| 軟刪除 | 410（掃描）/ 404（管理） | 曾存在已移除 |
| 已過期 | 410 | 曾存在已失效 |

**優**：410 讓爬蟲更快移除索引、可對使用者顯示「已刪/過期」差異化訊息、利於 debug 區分死連結。**管理 API 對已刪回 404、掃描路徑回 410** 是刻意差異（管理面當「找不到」、掃描面告知「曾有已刪」），文件須註明避免誤會。

**定案**：維持 404/410 區分。

## 第 12 題：軟刪除 vs 硬刪除

| 面向 | 軟刪除（採用） | 硬刪除 |
|---|---|---|
| 掃描已刪 | 可回 410 | 只能 404 |
| 分析歷史 | 保留 | 消失 |
| token 回收 | 不回收，避免舊 QR 指到新目標 | 可能回收 → 安全風險 |
| 稽核/復原 | 可 | 不可 |
| 儲存 | 累積（靠 cron 二次清） | 省 |

**綁定關係**：第 11 題的 410「曾存在已移除」**必須靠軟刪除**才能實現；token 回收安全問題也由軟刪除天然避免。

**定案**：軟刪除，長期由 cron 物理清理超期者。

## 第 13 題：過期 + 清理 cron

**(a) 判定**：惰性過期（掃描時檢查 `expires_at < now`）零背景成本、當下即時生效；缺點是沒人掃的過期資料躺 DB。

**(b) cron 三職責**：① 清已過期 ② 通知擁有者+清長期未點擊（PDF 要求先通知後刪）③ 物理清理超期軟刪資料；同時 invalidate cache。

**補強**：cache 設 TTL 當最終一致性保險；時間欄位一律存 **UTC**。

**定案**：惰性過期 + cron 兜底（兩者並用）。

## 第 14 題：分析儲存

規模：~6 千筆/秒、一年上看千億筆。

| 選項 | 優 | 劣 |
|---|---|---|
| 同庫明細表（repo） | 即時 GROUP BY、簡單 | 表爆量、查詢慢、與主表搶資源 |
| **獨立庫 + 預聚合（採用）** | 查詢只讀「每日計數表」極快省空間 | 需建聚合管線 |
| Kafka→ClickHouse | 超大規模多維分析 | 架構重 |

**關鍵**：`/analytics` 只需總數+每日數 → 用預聚合(`token,date,count`)即可，不必存每筆明細；明細只在需要逐次 UA/IP/地理時才留。

**定案**：獨立分析庫 + 預聚合（原型沿用同庫明細，文件以預聚合為準）。

## 第 15 題：QR 圖片

**關鍵性質**：QR 編的是短碼 `/r/{token}`（不變），所以圖是**靜態內容**，目標 URL 改了圖不需重生。

| 選項 | 優 | 劣 |
|---|---|---|
| 即時生成（repo） | 零儲存、URL 改圖自動對 | 每次吃 CPU，高 QPS app server 變算圖機 |
| 預生成 + object store | 一次算之後純讀 | 需儲存 |
| **+ CDN（採用）** | 最低延遲、最省 server（PDF 建議） | 多 CDN 元件 |

**細節**：樣式參數(`dimension/color/border`)不同是不同圖 → 用 `(token,樣式)` 當 key 首次生成後存起走 CDN。

**定案**：預生成 + object store + CDN（原型保留即時生成）。

## 第 16 題：DB 擴展

容量 200GB、讀寫 100:1。

**(a) 單機夠**：200GB 不大，瓶頸在讀 QPS 非容量 → 先不分片。

**(b) read replica（read-heavy 的核心手段）**：

為什麼需要 replica，從數字看：

```
容量：     1B × 200 bytes = 200GB                （單機放得下，瓶頸不在容量）
讀流量：   100,000,000 users × 5 redirects/day = 500,000,000 redirects/day
           500,000,000 / 86,400s ≈ 5,787 redirects/sec
讀寫比：   ≈ 100:1（redirect/查詢 遠多於 create/update/delete）
```

200GB 單機裝得下，但**~5,800 QPS 的讀**全壓在一台 primary 上會先撐不住——瓶頸是**讀吞吐**，不是容量。read-heavy 的標準解法就是**讀寫分離 + 多個 read replica**：

```
   寫（create / update / delete）
App ───────────────────────────────►  Primary (Write)
 │                                        │ 非同步複寫
 │  讀（redirect / get，load-balanced）    ├──►  Read Replica 1
 └─────────────────────────────────────── ┼──►  Read Replica 2
                                          └──►  Read Replica N
```

- **寫走 primary、讀走 replica**：把高頻的讀分散到多台 replica，水平擴讀。
- **再加 N 台只擴讀**：讀不夠就加 replica；配合 Redis cache，真正打到 DB 的讀已先被擋掉一大半。
- **容錯 failover**：primary 掛掉時把一台 read replica 升為新 primary。
- **複寫延遲(replication lag)**：replica 是非同步跟上的，剛 create 完立刻去 replica 查可能讀不到 → 靠 create 後**暖 cache**緩解，或關鍵讀走 primary。

**(c) 分片**：寫入/資料超單 primary 才需要，分片鍵選 token（hash 均勻），代價是跨片查詢+運維 → 列未來選項。

**定案**：單 primary + 多 read replica + failover；分片以 token 為鍵備用。

---

## 核心流程

**建立**：保守正規化 + 惡意檢查 → 生 token（直接插入，IntegrityError 重試≤10）→ 寫 DB → 暖 Redis cache → 預生成 QR 存 object store → 回應。

**Redirect（`/r/{token}`，低延遲關鍵路徑）**：
1. Redis 命中 → 非同步記 scan → 302
2. miss → DB 查（走 read replica）
3. 找不到→404；`is_deleted`→410；`expires_at < now(UTC)`→410
4. 回填 cache(設 TTL) → 非同步記 scan → 302

掃描一個短碼（例：`/r/JodCIMZx` → `https://buildmoat.teachable.com/...`）的完整流程：

```
                         GET http://localhost:8000/r/JodCIMZx
   ┌──────────┐  ①              │
   │ 掃描者/   │ ───────────────┘
   │ 瀏覽器    │ ◄───────────────┐
   └──────────┘  ⑦ 302 Location: │ https://buildmoat.teachable.com/courses/2026/lectures/65104709
        │                        │
        │ ⑧ 瀏覽器自動跟隨 302    │
        ▼                        │
   ┌─────────────────────────┐   │
   │ buildmoat.teachable.com │   │   （真正的目標頁，不經過我們）
   └─────────────────────────┘   │
                                 │
   ════════════ 我們的服務 (FastAPI redirect handler) ════════════
                                 │
        ②  抽出 token = "JodCIMZx"、user-agent、IP
                                 │
        ▼
   ┌─────────────────────────────────────────────┐
   │ ③ 查 Cache（原型:記憶體 dict / 正式:Redis）   │
   └─────────────────────────────────────────────┘
        │                                  │
   命中 │                            miss  │
        ▼                                  ▼
   ┌──────────────────┐         ┌─────────────────────────────┐
   │ 過期了嗎?         │         │ ④ 查 DB（正式:read replica） │
   │ expires_at < now │         │    SELECT ... WHERE token=?  │
   └──────────────────┘         └─────────────────────────────┘
     否 │      是 │                         │
        │        │ 刪 cache         ┌───────┼─────────┬──────────┐
        │        └──────────────►   │       │         │          │
        │                       找不到   is_deleted  已過期    正常
        │                          │       │         │          │
        │                          ▼       ▼         ▼          ▼
        │                       ┌─────┐ ┌─────┐  ┌─────┐  ┌──────────────┐
        │                       │ 404 │ │ 410 │  │ 410 │  │ ⑤ 回填 cache  │
        │                       └─────┘ └─────┘  └─────┘  │   (含 TTL)    │
        │                                                 └──────────────┘
        │                                                        │
        └───────────────────────┬────────────────────────────────┘
                                 ▼
              ┌──────────────────────────────────────────┐
              │ ⑥ 排程「非同步記錄掃描」(BackgroundTasks)   │
              │    → 回應送出後才寫 scan_events，不擋路徑    │
              └──────────────────────────────────────────┘
                                 │
                                 ▼
                   回傳 302 + Location 標頭（上面的 ⑦）
```

這張圖凸顯三個關鍵設計：cache 命中時整條路不碰 DB（③→⑥→⑦，第 7 題低延遲）；302 每次回源讓改/刪即時生效、掃描可統計（第 6 題）；記帳是 ⑥ 的背景任務、跳轉 ⑦ 先走（第 8 題不擋關鍵路徑）。

302 回應在線路上就是「狀態碼 + Location 標頭」，瀏覽器收到後自動對 Location 再發一次 GET：

```
HTTP/1.1 302 Found
location: https://buildmoat.teachable.com/courses/2026/lectures/65104709
```

redirect 回應碼矩陣：

| 情況 | 路徑 | 回應 |
|---|---|---|
| 熱門、cache 命中且未過期 | 快取命中 | 302（最快，不碰 DB） |
| cache miss / 首次掃描、正常 | 查 DB → 回填 cache | 302 |
| cache 命中但已過期 | 刪 cache → 落 DB | 410 |
| token 從沒存在 | DB 查無 | 404 |
| 已軟刪除 | DB `is_deleted` | 410 |
| 已過期 | DB `expires_at < now` | 410 |

**redirect < 100ms：兩層查找 + 索引**

```
掃描 → ① cache.get(token)          記憶體/Redis，O(1)，<1ms
          命中 → 回 302  ✅ 絕大多數請求停在這、完全不碰 DB
          miss → ② SELECT ... WHERE token=?   走 ix_url_mappings_token，O(log n)
                   → 回填 cache → 回 302
        scan 記錄非同步（BackgroundTasks），不算進這條延遲
```

- **第 1 層 Cache**（`app/cache.py`、`routes.py` redirect 第一段）：熱門 token 命中即回，免查 DB；原型記憶體 dict、正式版 Redis（第 7 題）。
- **第 2 層 Indexed DB**（`routes.py` 的 `filter(UrlMapping.token == token)`）：cache miss 才查；token 有 **UNIQUE B-tree 索引 `ix_url_mappings_token`**，10 億筆也是 O(log n) 次定位，而非全表掃描 O(n)。

| | 無索引 | 有索引（現況） |
|---|---|---|
| 查 1 個 token（10 億筆） | 全表掃描 O(n)，數秒 | B-tree seek O(log n)，次毫秒 |

三道防線共同保 <100ms：cache 擋掉多數讀（第 7 題）＋ token 索引讓 miss 也快（第 2 題）＋ scan 非同步不擋路（第 8 題）；正式版再加 read replica 分攤讀流量（第 16 題）。

**更新/刪除**：改 DB(primary) → invalidate Redis。

**取 QR 圖片（`GET /api/v1/qr/{token}/image`）**：

```
GET .../{token}/image?dimension=300&color=0000ff&border=2
   │
   ▼
 ① 驗證 token（找不到/已軟刪 → 404）
   │
   ▼
 ② 組短碼字串 short_url = BASE_URL + "/r/{token}"
   │   ⚠ 編進 QR 的是「不變的短碼」，不是原始 URL → 改目標 URL 不需重生圖（dynamic QR）
   ▼
 ③④ qrcode：short_url 編碼 → Reed-Solomon 糾錯 → 排進 2D 模組矩陣
   │     （finder / timing / alignment patterns + masking；fit=True 自動選最小版本）
   ▼
 ⑤ make_image：每模組 → box_size 像素方塊；border=quiet zone；fill_color/back_color
   ▼
 ⑥ 存成 PNG bytes（io.BytesIO，不落地）
   ▼
 ⑦ 若帶 dimension → Pillow NEAREST 縮放（保持方塊銳利）
   ▼
 ⑧ StreamingResponse(media_type="image/png") 串流回傳
```

QR 編短碼而非原始 URL，是 dynamic QR 的關鍵：目標 URL 改了圖不用重生。
圖片是靜態內容 → 原型即時生成；正式版預生成存 object store + CDN（第 15 題）。
樣式參數：`dimension`（像素邊長）、`color`（hex 黑塊色）、`border`（quiet zone 模組數）。

---

## 對 repo 的四個關鍵改良

1. **第 5 題**：repo「先查再插」有並發 race → 改「直接插入 + UNIQUE 例外重試」。
2. **第 7 題**：repo 行程內 dict cache 與 stateless 衝突（invalidation 破功）→ 改 Redis。
3. **第 8 題**：repo 同步寫 scan 卡在 redirect 關鍵路徑 → 改非同步。
4. **第 9 題**：repo 整串小寫破壞 path、強制 https 可能導向失效站 → 改保守正規化。

---

## PROMPT.md 五個 Design Questions 對照

1. **Static vs Dynamic** → 第 4/15 題：採 dynamic（編短碼），可改/刪/分析；static 僅適合永不變動且不需追蹤。
2. **Token/碰撞** → 第 3/5 題：SHA-256+nonce+Base62 取 8 碼；nonce 只解決「同 URL 同 token」的確定性問題、不防碰撞；碰撞靠 UNIQUE + 直接插入例外重試兜底，規模增大可再加長 N。
3. **302 vs 301** → 第 6 題：可改/刪/分析優先，延遲靠 cache/CDN/非同步補。
4. **URL 正規化** → 第 9 題：host 大小寫不敏感、尾斜線可忽略；但只正規化 host、不動 path/query、不強改 scheme。
5. **錯誤語意** → 第 11/12 題：刪除/過期回 410、不存在回 404；410 需靠軟刪除實現。

---

## 原型 vs 設計文件落差（若日後實作）

文件以 production 架構為準（Redis/非同步/object store+CDN/預聚合/replica）。最小可跑原型可暫用：記憶體 dict cache、`BackgroundTasks` 非同步、`qrcode.make()` 即時生成、同庫 `scan_events` 明細，並於註解標明替換點。

技術棧：FastAPI + SQLAlchemy + (SQLite 原型 / PostgreSQL 正式) + `qrcode[pil]`。

驗證：PROMPT.md curl 測試（create→302→get→patch→delete→410→404→image→analytics）。

---

## 附錄 A：NoSQL 替代方案（DynamoDB 風格）

我們主設計用關聯式 DB；若改用 NoSQL（DynamoDB 類），key 設計是成敗關鍵。

**PK / SK 本質**：PK（分區鍵）決定資料存在哪個分區，查詢**必須提供 PK** 才能 O(1) 定位，否則只能 Scan 全表；SK（排序鍵）在同分區內排序、可做範圍查詢。**NoSQL 要先列存取模式，再決定 key。**

**直覺方案 `PK=user_id, SK=created_at#qr_token`**

```
PK = user_id     SK = created_at#qr_token      attrs: original_url, expires_at, is_deleted
user_42   2026-06-01T10:00Z#2smhAH15   ...
user_42   2026-06-26T03:00Z#JodCIMZx   ...
```

- ✅ 「列出我的 QR」完美：`Query PK=user_id`（同 user 同分區、按時間排序），直接滿足 PDF FR。
- ❌ **致命問題**：redirect 掃 `/r/{qr_token}` 時手上**只有 token、沒有 user_id**，但 PK 是 user_id → 無法定位分區 → 退化成 **Scan O(n)**，10 億筆數秒、5,800 QPS 成本爆掉，違反 <100ms。這是最高頻路徑，不能犧牲。

**修正：圍著 redirect 設計**

| 方案 | Base table | GSI |
|---|---|---|
| 你的方案 + 補 GSI | PK=user_id, SK=created_at#qr_token | **PK=qr_token**（給 redirect） |
| **建議** | **PK=qr_token**（最熱、均勻分區、可保證唯一） | PK=user_id, SK=created_at（列我的 QR） |

建議 base PK=qr_token 的理由：redirect 直接 `GetItem(PK=qr_token)` 最快且強一致；用高亂度的 qr_token 當 PK 流量**均勻分散**，避免 user_id 造成 **hot partition**（爆紅用戶/被狂掃的 QR 打爆單一分區）。

**NoSQL 特有考量**

- **token 唯一性**：DynamoDB 不保證非 key 欄位唯一 → 用「qr_token 當 base PK + 條件寫入 `attribute_not_exists`」擋碰撞（等同 SQL 的 UNIQUE+重試，第 5 題）。token 只在 GSI 則無法保證唯一。
- **過期**：`expires_at` 設原生 **TTL** 自動刪（省掉大半 cron，第 13 題）；但 TTL 刪除可能延遲達 48h，仍需惰性過期檢查回 410。
- **分析**：高寫入 OK，但每日 GROUP BY 非強項 → 仍走非同步 + 預聚合/分析庫（第 14 題）。

**SQL vs NoSQL 對照**

| | 目前 SQL | NoSQL（PK=user_id） | NoSQL（PK=qr_token + GSI） |
|---|---|---|---|
| redirect 查 token | B-tree O(log n) ✅ | **Scan O(n)** ❌ | GetItem O(1) ✅ |
| 列出我的 QR | `WHERE user_id`+索引 | 天生最佳 ✅ | GSI 查 ✅ |
| token 唯一 | UNIQUE ✅ | 需額外處理 | 條件寫入 ✅ |
| 水平擴展 | 需分片（第 16 題） | 自動分區 ✅ | 自動分區 ✅ |

**結論**：`PK=user_id` 對「管理我的 QR」最佳，卻漏掉系統第一公民——按 token redirect。應以 **base PK=qr_token** 為主、user 清單放 **GSI(PK=user_id, SK=created_at)**。

---

## 附錄 B：CDN 邊緣 redirect 變體（可選最佳化）

主設計**選擇不快取 redirect**：每次回源才能即時反映改/刪、並記錄每次掃描，因此 redirect 不放 CDN 邊緣；CDN 只服務靜態 QR 圖片。

⚠ 釐清：這與「302 vs CDN」**無因果關係**——302 只是狀態碼，本身**可以**被 CDN 快取（明確設 `Cache-Control: public, max-age=N` 即可）。真正的取捨是「**快取 redirect** vs **即時性/分析**」，跟用 301 還 302 無關。301 vs 302 是**瀏覽器**預設快取行為（第 6 題），CDN 快取是另一層。

本變體就是把那個取捨倒過來：對熱門 token 的 302 設**短 TTL**、讓 CDN 邊緣直接跳轉，用分析準確度與改/刪即時性換更低延遲（即 PDF 說的「redirection 在 CDN 完成」）：

```
            GET /r/{token}
   ┌──────────┐
   │ 掃描者    │ ──────────────┐
   │ 瀏覽器    │ ◄─────────┐   │
   └──────────┘   302      │   ▼
        │ 跟隨 302         │  ┌───────────────────────────┐
        ▼                  │  │   CDN Edge（近使用者）      │
     目標頁                │  │  edge cache: token→URL      │
                           │  │  短 TTL（例：60s）          │
                           │  └───────────────────────────┘
                           │      命中│              miss│
                           │          │                  ▼
                           └── CDN 直接回 302      回源到我們 Service
                               (不回源,最低延遲)    → Cache/DB → 回 302
                                                   → CDN 記錄此回應(短 TTL)
```

**取捨**：

| | 主設計（每次回源 + Redis） | CDN 邊緣 redirect 變體 |
|---|---|---|
| 延遲 | 低（回源一趟 + Redis） | **最低**（熱門在邊緣直接跳） |
| 改 / 刪即時性 | 立即生效 | **TTL 內延遲**（最多一個 TTL 才更新） |
| 掃描分析 | 每次都記 | **TTL 內漏記**（邊緣命中不回源） |
| 適用 | 預設（正確性優先） | 超高流量、可容忍 TTL 內誤差的熱門 QR |

**緩解**：TTL 設短（秒級）把誤差控制在可接受範圍；或只對「極熱、目標穩定」的 QR 開啟此模式，一般 QR 仍走主路徑。我們**預設不採用**，把它列為流量極大時的可選最佳化。

---

## 附錄 C：從 Prototype 到 Production 的落差（production-readiness）

本專案聚焦核心功能與架構決策；要真上 production，以下面向仍待補（對照我們現況）：

| 面向 | 原型現況 | Production 需要 |
|---|---|---|
| Error handling | 驗證回 400，但未全面 | 全面輸入驗證、結構化錯誤回應、不因壞輸入 crash |
| **Rate limiting** | **無** | create 等端點限流，防 script 灌爆 API |
| **Auth & 多租戶** | **無 user 概念，資料全域** | 登入 + `user_id` 隔離（清單/權限）、見 PDF FR |
| **Monitoring / Alerting** | **無** | metrics、結構化日誌、服務掛掉告警（動態 QR 的 server 是 SPOF） |
| Data cleanup | 設計有 cron（第 13 題），未實作 | 定期清過期/長期未點擊，避免 DB bloat |
| Caching / CDN | 記憶體 dict + 即時生圖 | Redis + object store + CDN（第 7/15 題） |

其中 **rate limiting、auth/多租戶、monitoring** 是目前設計**完全沒談**的三塊，列為 production 前必補。注意：動態 QR 把 server 變成 single point of failure，所以 caching + CDN + monitoring 不是加分項而是**動態方案的必要代價**。

> **靜態的反向場景**：若需求是「印好不改、離線可用、極高可靠（如醫療器材）」，反而該選**靜態 QR**（編碼原始 URL、不需 server）。我們選動態純粹因為需求是「可修改 + 可追蹤」。

---

## 附錄 D：AWS 部署架構與 CI/CD（已實際部署）

本設計已用 **Terraform** 部署到 AWS（region `ap-northeast-1`），程式改動全 env-gated、與本機啟動分離。完整 runbook 見 `infra/README.md`。

### 部署架構

```
                 ┌──────────── CloudFront (CDN, *.cloudfront.net) ────────────┐
   Client ───────┤  /qr-img/*       ──────────────► S3 (QR PNG, private+OAC)   │ 長快取
                 │  /, /r/*, /api/* ──► API Gateway (HTTP API)                  │ redirect 不快取
                 └───────────────────────────────┬────────────────────────────┘
                                                  │ VPC Link
                                                  ▼
                                    內部 ALB (private subnets, health /health)
                                                  ▼
                            EC2 Auto Scaling Group (Docker/gunicorn, private)
                                  ├── RDS PostgreSQL (private)
                                  └── ElastiCache Redis (private)

  設定/密鑰：SSM Parameter Store（DATABASE_URL / REDIS_URL / S3_BUCKET / CDN_BASE / BASE_URL / IMAGE_URI）
            Secrets Manager（DB 密碼）· ECR（容器映像）· NAT Gateway（private egress）
```

對應設計決策：API Gateway 前門、CloudFront 服務靜態 QR 圖且 **redirect 路徑關快取**（第 6 題每次回源）、Redis 共享 cache（第 7 題）、RDS 讀寫分離可擴（第 16 題）、S3+CloudFront 圖片（第 15 題）。EC2 開機/部署時從 SSM 撈設定注入容器（env-gated，本機無這些 env 則走 SQLite+記憶體+即時生圖）。

### CI/CD 流程（GitHub Actions）

```
 git push（路徑含 QR Code Generator/**）
        │
        ▼
 GitHub Actions ── OIDC assume role（無長期金鑰，role 由 cicd module 建立）──► AWS
        │
        ├─ docker buildx --platform linux/arm64   （EC2 是 t4g/arm64，須跨平台 build）
        ├─ push → ECR（tag = git SHA + latest）
        ├─ SSM put-parameter /qrcode/IMAGE_URI = <新映像>
        └─ SSM send-command（targets tag:app=qrcode）→ 每台 EC2 執行 deploy-app.sh
                                                        └─ ECR login → docker pull → docker run（換新容器）
        ▼
 ALB health check /health 通過 → 新版上線
```

### 部署時踩到並修正的兩個重點

1. **EC2 instance role 少 `s3:PutObject`** → create 上傳圖失敗回 500。修法：edge module 補上對 QR bucket 的 PutObject。
2. **CI 在 amd64 runner build、但 EC2 是 arm64(t4g)** → 容器 exec format error、ALB 502。修法：workflow 改用 **buildx + QEMU build `linux/arm64`**。

> 教訓呼應附錄 C：動態 QR 的 server 是 SPOF，cache/CDN/monitoring 是必要代價；且**跨架構容器**與**最小權限 IAM** 是 prototype→production 常見的坑。
