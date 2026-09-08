# Amazon Price Tracking Service — 設計討論文件（含選項與優劣）

## Context

讀完 PDF（系統設計題「Design Amazon Price Tracking Service」，buildmoat.org）後，討論如何實作一個 Amazon 價格追蹤服務：使用者能看商品的**價格歷史**（網站 / Chrome extension），並**訂閱降價通知**（設定門檻）。規模 5 億商品、價格歷史查詢 < 500ms、價格變動後 1 小時內通知、可用性優先（eventual consistency 可接受）。

本專案是依此設計實作的**可跑原型**（比照同 repo 的 QR Code Generator / Earthquake）：本機零依賴（SQLite + 記憶體 queue + SSE）即可示範，`docker compose` 走 production code path（Postgres + Redis Streams），並附本 app 專屬的 AWS Terraform。技術棧 **Python + FastAPI**。

**與前兩題最大的不同**：crawler 不是 mock，而是**真的抓 amazon.com**（`curl_cffi` 模仿 Chrome TLS 指紋 + `selectolax` 解析，Playwright 無頭瀏覽器當 fallback）。反爬的實測踩雷都寫在附錄 E。

---

## 三大關鍵架構決策（決策地圖）

| 關鍵決策 | 選擇 | 為什麼 / 連鎖反應 |
|---|---|---|
| **① 如何追蹤 5 億商品** | **extension 眾包 + 優先式爬取 + 完整性驗證** | 盲爬：Amazon 每 IP 限 1 req/s，1000 IP 掃一輪要 `5e8/1000/86400 ≈ 5.8 天`，資料太舊。改成「誰重要誰先爬」（訂閱數 / 查看數當優先分數），再讓 **browser extension** 把使用者正在看的價格回報上來（天然涵蓋熱門商品、順便發現新商品）。代價：使用者上傳的資料不能直接信 → 可疑降價**不入庫、先最高優先重爬**，crawler 結果才是真相。 |
| **② 如何 1 小時內通知** | **CDC → Redis Streams → price-change worker** | cron 每 2 小時全表掃：延遲吃 cron 頻率、每次 full scan。改成 event-driven：寫入端只 append price 表，**CDC** tail 變更日誌（原型：seq 輪詢；正式版：DynamoDB Streams）把每筆新價格變成事件，worker 用 `(product_id, price_threshold, user_id)` 索引 join 訂閱表。代價：at-least-once → 需要 outbox 冪等 + cooldown 避免吵使用者。 |
| **③ 如何 < 500ms 畫圖** | **pre-aggregation 表（daily / weekly / monthly）** | 熱門商品每小時一筆，2 年 = 17,520 列；每次畫圖 `date_trunc + avg` 撐不住。背景 job 預先彙總進 `price_aggregations`，30 天圖讀 30 列、2 年圖讀 24 列。代價：新鮮度落後一個彙總週期（圖看趨勢、即時價另外給），新商品用 raw fallback。 |

→ ①決定「資料從哪來」、②決定「變動怎麼變成通知」、③決定「歷史怎麼快速讀」。下面逐項展開。

---

## 需求摘要

**FR**：① 查看 Amazon 商品價格歷史（網站 / Chrome extension）；② 訂閱降價通知並設門檻。
**Out of scope**：站內搜尋/探索、跨零售商比價、評論整合。
**NFR**：可用性 > 一致性；5 億商品；價格歷史查詢 < 500ms；價格變動後 1 小時內通知。

**容量與數學**：
- 盲爬：`5e8 商品 / (1000 IP × 1 req/s) / 86400 ≈ 5.8 天` 掃一輪 → 淘汰。
- 價格寫入：5 億商品 × 每天 1 次 ≈ 5.8K writes/s（熱門商品更頻繁）→ append-only、高寫入 → DynamoDB（product_id 分區）。
- 訂閱查詢：`SELECT user_id FROM subscriptions WHERE product_id=:pid AND price_threshold >= :new_price` → 索引 `(product_id, price_threshold, user_id)` 直接 range scan。
- 圖表：每小時一筆 × 2 年 = 17,520 列 / 商品；彙總後 daily 730、weekly 104、monthly 24。

---

## 技術選型定案（與討論結論）

| 面向 | 定案 | 為什麼（淘汰誰） |
|---|---|---|
| Price DB（append-only、高寫入） | **DynamoDB**（PK=product_id, SK=ts epoch ms）+ **DynamoDB Streams** | 存取模式就是「依商品分區、依時間排序、只 append」；Streams 是現成的 log-based CDC。淘汰 Cassandra/Keyspaces（同模型但沒原生 Streams）、單一 Postgres（5 億商品的寫入壓在單 primary）。原型 SQLite `prices` 表 + seq。 |
| 價格事件流 | **CDC（tail 變更日誌）** | 寫入端只寫 DB，沒有「DB 成功、事件失敗」的雙寫不一致；過濾小波動 / 合併改放 consumer 端（附錄 A）。淘汰 dual-write。 |
| Queue | **Redis Streams**（`REDIS_URL`）/ asyncio.Queue（原型） | 與 Earthquake 同一套、compose 輕；1 小時 SLA 不需要 Kafka 的吞吐/長期持久化；per-channel（crawl / price_changed / notify:sse / notify:email）好切。淘汰 Kafka（量大或需回放時再上，附錄 B）、SQS（沒 consumer group）。 |
| 訂閱 / 彙總 / outbox / 目錄 | **PostgreSQL (RDS)** / SQLite（原型） | 訂閱 join 查詢、彙總表複合索引、outbox 交易語意都需要關聯 DB。 |
| 圖表讀路徑 | **pre-aggregation 表** | 見決策③；OLAP/TSDB（ClickHouse / InfluxDB）為進階替代（附錄 F）。 |
| Crawler fetcher | **curl_cffi（TLS impersonate）+ selectolax 主、Playwright fallback** | Amazon 在 TLS handshake 就做指紋比對，純 requests 直接被擋；curl_cffi 輕量、每次數百 ms；Playwright 映像 +400MB、每次吃 CPU，只當 fallback。淘汰 requests+bs4（被擋）、Playwright-only（成本 50×）。 |
| Extension | demo 頁**面板模擬**打 `POST /price-reports`；回報**匿名**（只有 reporter_token 限流） | 零安裝即可 demo 眾包 + 完整性驗證；不記使用者瀏覽紀錄（隱私）。 |
| 通知送達 | 原型 **SSE**（demo 可視）；正式版 **email（SES）** | PDF 的 notification_type 本質是 email；SSE 只為了看得到（附錄 H）。 |
| Compute | **EC2 ASG**（api / worker 各自 ASG） | 對齊 repo 藍本；worker 跑 crawler + CDC tailer + notify workers，可獨立擴縮。 |

---

## API（定案，對齊 PDF，加 `/api/v1`）

PDF 兩支：

| 方法 | 路徑 | 說明 | 回應 |
|------|------|------|------|
| GET | `/api/v1/price/{product_id}?period=30d&granularity=` | 價格歷史（period 7d/30d/90d/1y/2y；granularity 可覆寫） | `{granularity, source: aggregation\|raw_fallback, points[{t,avg,min,max,close,n}], rows_scanned, raw_rows_in_period, latency_ms, current_price}` |
| POST | `/api/v1/subscriptions` | `{user_id, product_id, price_threshold, notification_type: sse\|email, email?}`（upsert） | 訂閱內容 |

眾包 / crawler / 彙總 / 維運：

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/price-reports` | extension 回報 `{product_id, price, reporter_token}` → `accepted` 或 `pending_verify`（可疑 → 最高優先重爬）；429 超過限流 |
| GET | `/api/v1/price-reports?product_id=` | 回報狀態機（pending_verify → confirmed / rejected） |
| POST | `/api/v1/products/track` | `{product_id}`（ASIN 或商品 URL）→ 註冊 + 最高優先爬取 |
| GET | `/api/v1/products` | 商品 + 優先分數 / 爬取間隔 / 下次到期 / fetcher / 狀態 |
| POST | `/api/v1/products/{asin}/crawl?sync=` | 立即爬取（sync=1 同步等結果，測試用） |
| GET | `/api/v1/crawl/log` | 爬取紀錄（用了哪層 fetcher、耗時、結果） |
| POST | `/api/v1/aggregations/run?product_id=` | 立即彙總 |
| GET | `/api/v1/aggregations/stats?product_id=` | raw 列數 vs daily/weekly/monthly 列數 |
| GET | `/api/v1/stream?user_id=` | SSE 即時通知（dev-only） |
| GET | `/api/v1/notifications?user_id=` | outbox 狀態機 |
| GET | `/api/v1/cdc/status` | checkpoint / head / lag / queue 深度 |
| POST | `/api/v1/debug/seed`、`/debug/mock-price` | 只在 `MOCK_AMAZON=1`（或 `ALLOW_SEED=1`）開放 |

身分：正式版 JWT（Cognito）env-gated；原型 `user_id` 由前端產生（一個分頁 = 一個使用者）。

---

## 資料模型

- **products**：`asin`(PK)、`title`、`currency`、`status`(active|unavailable|region_locked|not_found|blocked)；寫入端 `last_price / last_price_at / last_price_source`；爬取狀態 `last_crawled_at / last_seen_at / last_crawl_status / last_fetcher / crawl_inflight_at`；優先訊號 `subscription_count / view_count / suspicious_flag / priority_boost`；CDC 消費端 `last_event_price / last_event_seq`（**只由 notify worker 更新**，與寫入端分離才能算 Δ）。
- **prices**（append-only）：`seq`(autoincrement，模擬 WAL 位置)、`product_id`、`price`、`currency`、`source`(crawler|extension|mock|seed)、`fetcher`、`ts`；索引 `(product_id, ts)`。正式版 DynamoDB：hash `product_id`、range `ts`、Streams NEW_IMAGE。
- **subscriptions**：`subscription_id`(PK)、`user_id`、`product_id`、`price_threshold`、`notification_type`、`email`、`status`、`last_notified_price`（cooldown）；`UNIQUE(product_id, user_id)`（PDF PK）+ `INDEX(product_id, price_threshold, user_id)`（PDF secondary，covering）。
- **price_reports**：`report_id`、`product_id`、`reported_price`、`reporter_token`（匿名）、`baseline_price`、`deviation_pct`、`status`(accepted|pending_verify|confirmed|rejected)、`verify_price`。
- **price_aggregations**：PK `(product_id, granularity, bucket_start)`、`avg/min/max/close_price`、`sample_count`。
- **notification_outbox**：`notification_id`(PK = `sha256(subscription_id|event_seq)[:32]`)、`event_seq`、`old_price`、`new_price`、`threshold`、`channel`、`status`(ENQUEUED|ATTEMPTED|VENDOR_ACCEPTED|FAILED|SKIPPED_COOLDOWN)。
- **cdc_checkpoint**：`consumer`(`prices_seq` / `ddb:{shard_id}`)、`position`。
- **crawl_log**：demo 面板用。

---

## 深入探討 1：如何有效率地發現並追蹤 5 億商品？

**方案演進**：
1. **(Naive) 盲爬**：seed → BFS 抓連結 → 平行處理 → 去重。Amazon 每 IP 1 req/s，1000 IP 也要 5.8 天掃一輪，資料太舊。
2. **(Better) 優先式爬取**：流量集中在少數商品 → 誰重要誰先爬。訊號：訂閱數（有人在等）、查看數（有人在看）、轉換率。冷啟動問題：新熱門商品沒人搜過就沒分數。
3. **(Best，本專案) browser extension 眾包**：使用者裝 extension 逛 Amazon，extension 回報 (product_id, 看到的價格)。天然涵蓋「有人在意」的商品、即時、還能發現新商品；crawler 只補近期沒人看的。把最大的限制（要監控上億商品）變成優勢。

**優先分數與排程**（`app/scheduler.py`）：
```
score    = 10·subscription_count + 2·log1p(view_count) + 100·suspicious_flag + priority_boost
interval = clamp(3600 / (1 + score), 60s, 86400s)      # 分數越高爬越勤；captcha 狀態 ×4 退避
due      = now - last_seen_at ≥ interval               # extension 回報也算「看過」
```
每 tick 取 due 商品依 `score × age/interval` 排序取前 N 入 `crawl` queue；訂閱新商品 / 手動爬 / 可疑回報走 `enqueue_urgent()` 直接入列並設 flag（job 掉了下個 tick 會撿回）。

**完整性驗證**（`app/reports.py` → `app/worker_crawl.py`）：使用者上傳的資料不能直接信。
- `deviation = (baseline − reported) / baseline`；`> 30%`（或根本沒 baseline）→ `pending_verify`：**不寫 price 表**（所以不會觸發通知），把商品以最高優先送進 crawl queue。
- crawler 回來：`|crawler 價 − 回報價| / crawler 價 ≤ 5%` → `confirmed`，否則 `rejected`。真正入庫 → CDC → 通知的永遠是 crawler 的價格。
- 非可疑回報直接 `accepted` 入庫（source=extension）、更新 `last_seen_at`（crawler 可以晚點再來）。
- 匿名：只有 `reporter_token`（每分鐘 30 次限流），不綁 user_id、不記瀏覽紀錄。

**真抓 Amazon**（`app/crawler/`）：兩層 fetcher，實測踩雷見附錄 E。

---

## 深入探討 2：如何有效率地處理價格變動並通知訂閱者？

**現況問題**：cron 每 2 小時掃 price 表 → ① 延遲吃 cron 頻率 ② 每次昂貴 full scan。→ 從 pull 批次改成 push 單一事件。

**CDC vs dual-write**（附錄 A）：
- **CDC（本專案）**：一個 process 持續 tail 資料庫變更日誌（MySQL binlog / Postgres WAL / **DynamoDB Streams**），轉成事件丟 queue。寫入端不變、不會有「DB 成功、事件失敗」；順序由 log 保證。
- **dual-write**：寫 DB 同時發事件；可在寫入端就過濾/合併，但要處理部分失敗（需 transactional outbox）。

**事件流**（`app/cdc.py` → `app/worker_notify.py`）：
1. **Tailer**：原型 `SELECT … WHERE seq > checkpoint ORDER BY seq LIMIT 500` 每秒一次，發完存 checkpoint（at-least-once）；正式版 DynamoDB Streams：`describe_stream` → 每個 shard 用 checkpoint（SequenceNumber）取 iterator → `get_records` → per-shard checkpoint（附錄 B）。
2. **price-change worker**：`prev = last_event_price`；查 `subscriptions WHERE product_id AND status='active' AND price_threshold >= new`（走 covering index，本質是 stream ⋈ DB）；`|Δ| < 1%` 且沒有訂閱「這次才跨過門檻」→ 過濾（小波動不吵人）；否則每個訂閱 → cooldown → outbox 冪等 gate → enqueue `notify:{type}`。
3. **sender**（per channel）：terminal 檢查 → ATTEMPTED → 送 → VENDOR_ACCEPTED / FAILED；成功寫 `last_notified_price`。

**冪等與 cooldown**（附錄 G）：`notification_id = hash(subscription_id | event_seq)` → at-least-once 重播不重送；`last_notified_price`：已通知過 $120 後 $118 才再通知、$119.5 回升不吵；價格回到門檻之上 → 重設，下次再跌破會再通知。

**回填不是變動**：`/debug/seed` 灌歷史後把 checkpoint 推過回填列，否則 17,520 筆假事件會炸通知。

---

## 深入探討 3：如何快速提供價格歷史查詢以支援圖表？

**問題**：`SELECT date_trunc('day', ts), avg(price) … WHERE product_id=:pid AND ts >= now()-2y GROUP BY 1` 對熱門商品要掃 17,520 列，數百萬使用者同時畫圖撐不住。

**方案**：
1. **(本專案) pre-aggregation**（`app/aggregation.py`）：背景 job 把 raw 依 daily / weekly / monthly 彙總進 `price_aggregations`（PK=(product_id, granularity, bucket_start)，含 avg/min/max/close/n）。API 依 period 選粒度：≤90d daily、≤1y weekly、>1y monthly → 30 天圖讀 30 列、2 年圖讀 24 列，毫秒級回。原型每 5 分鐘增量重算有新價格的商品 + 「立即彙總」按鈕；正式版夜間 job（可掛 read replica 專門掃）。
   - 新鮮度：最多落後一個彙總週期（正式版 ≤ 24h）；圖表看趨勢可接受，即時價從 `products.last_price` 另給。
   - 新商品沒彙總 → **raw fallback**（現算），回應標 `source="raw_fallback"`，圖照樣能畫；demo 面板顯示 raw 列數 vs 彙總列數與延遲，看得到差異。
2. **OLAP / TSDB**（附錄 F）：若還有跨商品分析、heavy join → OLTP → CDC → stream → ClickHouse / InfluxDB（column-oriented 做 aggregation 快）。本專案的 CDC 管線可以直接多接一個 consumer 灌 OLAP。

**實測**（本機 SQLite）：17,520 列 → daily 731 / weekly 106 / monthly 25；30 天圖 raw fallback 掃 720 列 2.9ms → 彙總後掃 31 列 0.65ms；2 年圖掃 25 列 0.57ms。

---

## 原型 vs Production（env-gated）

| 面向 | 原型（本機） | Production（雲端） | 切換 env |
|---|---|---|---|
| Price DB | SQLite `prices`（seq） | DynamoDB（product_id, ts）| `PRICE_TABLE` |
| CDC | seq 輪詢 tailer | DynamoDB Streams tailer | `PRICE_TABLE` |
| Queue | asyncio.Queue（per-channel） | Redis Streams | `REDIS_URL` |
| 目錄 / 訂閱 / 彙總 / outbox | SQLite | RDS PostgreSQL | `DATABASE_URL` |
| Crawler | 真抓 amazon.com（curl_cffi）；`MOCK_AMAZON=1` 離線假 Amazon | 同，worker ASG + 美國出口 IP / proxy pool | `MOCK_AMAZON`、`AMAZON_DOMAIN`、`CRAWL_RPS` |
| Playwright fallback | 未裝不啟用 | `--build-arg WITH_PLAYWRIGHT=1` | `CRAWLER_PLAYWRIGHT` |
| 通知 | SSE 到瀏覽器；email dry-run | SES email | `SES_FROM_EMAIL` |
| 彙總 | in-proc 每 5 分鐘 | EventBridge 夜間 job + in-proc 增量 | `AGG_INTERVAL_S` |
| Scheduler / tailer / workers | 主程序 asyncio task | worker ASG（`ROLE=worker`） | `ROLE` |
| reporter 限流 | 記憶體 deque | Redis INCR + EXPIRE | 部署形態 |
| Auth | 無（前端產 user_id） | Cognito JWT | `AUTH_ENABLED` |

沿用 QR / Earthquake 的「工廠函式 + 延遲匯入」寫法：未設 env → 記憶體/SQLite 實作，本機零依賴。

---

## 附錄

### A. CDC vs dual-write

| 面向 | CDC（log-based） | Dual-write |
|---|---|---|
| 一致性 | 寫入端只寫 DB；事件從 log 衍生，不會漏 | DB 成功、發事件失敗 → 漏通知；反過來 → 幽靈通知。需 transactional outbox 補救 |
| 順序 | log 順序 = commit 順序（DynamoDB Streams per-shard 有序） | 併發寫入時事件順序不保證 |
| 過濾 / 合併 | 在 consumer 端做（本專案：worker 過濾 <1% 波動） | 可在寫入端就做 |
| 侵入性 | 寫入端零改動；多一個 tailer process | 每個寫入路徑都要記得發事件 |
| 延遲 | log → 事件通常 < 1s（DynamoDB Streams 近即時） | 即時 |

本專案：CDC。原型 seq 輪詢是「窮人版 log tail」，正式版換 DynamoDB Streams 事件格式不變。

### B. DynamoDB Streams 機制與消費方式

- Streams 記錄 24 小時內的每筆變更（INSERT/MODIFY/REMOVE），依 partition 分成 shard；同一 shard 內有序、同一 item 的變更一定在同一 shard 鏈。
- 消費：`describe_stream` 列 shard → `get_shard_iterator(TRIM_HORIZON | AFTER_SEQUENCE_NUMBER)` → `get_records` 迴圈；shard 會關閉並分裂成 child shard，要定期重列。
- 三種消費形態：
  1. **自己 tail**（本專案）：worker 用 boto3 直接讀，checkpoint（SequenceNumber）存 Postgres `cdc_checkpoint`。單一 codebase、不需 Lambda；多 worker 時要自己做 shard lease。
  2. **Lambda event source mapping**：AWS 管 checkpoint / 重試，Lambda 把事件轉丟 Redis Streams；多一個 runtime。
  3. **KCL + DynamoDB Streams Kinesis Adapter**：多 worker shard lease 現成，較重。
- Redis Streams vs Kafka：1 小時 SLA、每秒數千事件 → Redis Streams 夠；若要長期回放 / 多下游（OLAP、data warehouse）→ Kafka 當 log，Redis 當工作佇列。

### C. 優先式爬取：分數、衰減與升級

- 分數（見深入探討 1）。`log1p(view_count)` 讓查看數邊際遞減，避免被刷。
- 衰減：用「距上次看到的時間 / 應爬間隔」排序，久沒爬的自然浮上來，冷門商品也不會餓死（上限 24h 爬一次）。
- 退避：`captcha` 狀態 ×4；`not_found` 拉到最大間隔。
- 升級：原型單一 FIFO `crawl` channel + `enqueue_urgent` 插隊。正式版 **Redis ZSET** 當 priority queue（score 即優先權）、多 crawler consumer group 分工、per-IP token bucket 集中 Redis、proxy pool 輪替 IP。

### D. 完整性驗證流程

```
extension 回報 (asin, price)
   │ baseline = products.last_price
   ├─ 沒 baseline 或 跌幅 > 30% ──► price_reports(pending_verify)、suspicious_flag=1、enqueue_urgent
   │                                   └─ crawler 回來 → |crawl−reported|/crawl ≤ 5% ? confirmed : rejected
   │                                      （入庫的是 crawler 價 → CDC → 通知）
   └─ 否則 ──► accepted：append(source=extension) → CDC → 通知；last_seen_at 更新
```
容忍度 5% 留給幣別換算 / 優惠券差異。攻擊面：惡意回報只會讓系統多爬一次（有限流），不會造成假通知。

### E. Amazon 反爬：實測筆記（2026-09）

- **TLS 指紋**：Amazon 在 TLS handshake 就比對 JA3/HTTP2 指紋；純 `requests` 直接被擋。`curl_cffi` 用 curl-impersonate 讓指紋與真瀏覽器一致。
- **`impersonate="chrome"`（= chrome124）會拿到「Click the button below to continue shopping」攔截頁**（3.7KB、HTTP 200），`chrome120 / safari17_0 / edge101 / safari15_5` 都能拿到完整商品頁（1.2–2.7MB）。本專案預設 chrome120，被擋就輪替下一個指紋 + 新 cookie jar，再不行才升級 Playwright。
- **幣別**：cookie `i18n-prefs=USD` 固定顯示幣別，否則依 IP 換算（且 `.a-offscreen` 格式不穩）。
- **價格只能從主價格區塊解析**（`#corePrice_feature_div` / `#corePriceDisplay_desktop_feature_div` / `#apex_desktop` …）的第一個 `.a-price`，用 `.a-price-whole` + `.a-price-fraction` 組價。抓整頁第一個 `.a-price` 會抓到「相關商品輪播」的價格；`#apex_desktop` 的 "Save 20% with Trade-In" 文字會被當 $20 —— 都是實測踩過的雷。
- **區域鎖定**：從非美國 IP 看很多商品是「This item cannot be shipped to your selected delivery location」→ buybox 價格不顯示；Amazon 現在對國際 IP 改配送地點要**登入**（glow address-change 回 sign-in），不登入無解。本專案把它判成 `region_locked` 狀態（非缺貨）。**正式版 crawler 要放美國出口 IP 或 proxy pool**；demo 用可國際配送的商品（書、配件）就看得到價格。
- **限速**：token bucket `CRAWL_RPS`（預設 1）+ 0.9–1.4× jitter，對齊 PDF「每 IP 1 visit/sec」。
- **Playwright**：能跑 JS、較能過 captcha，但映像 +400MB、每次吃 CPU/記憶體；只當 fallback（`CRAWLER_PLAYWRIGHT=1`）。實測：無頭 Chromium 每次 2–3.5 秒（curl_cffi 0.3–2 秒），結果與 curl_cffi 一致（含 region_locked 判定）；context 也要帶 `i18n-prefs` cookie 否則幣別依 IP 換算。
- **robots / ToS**：Amazon robots.txt 對 `/dp/` 沒有明確 disallow 但 ToS 禁止自動化存取；本專案為系統設計教學 demo，1 rps、不登入、不繞 captcha；正式產品應評估 Product Advertising API / 合法資料供應商。

### F. 彙總粒度與 OLAP 替代

| period | 粒度 | 列數（每小時一筆） | raw 列數 |
|---|---|---|---|
| 7d / 30d / 90d | daily | 7 / 30 / 90 | 168 / 720 / 2,160 |
| 1y | weekly | 52 | 8,760 |
| 2y | monthly | 24 | 17,520 |

新鮮度：彙總落後 ≤ 一個週期；最新 bucket 是「開放中」的，每次彙總會重算。儲存：每商品 daily 365×年 + weekly + monthly，線性成長。
OLAP：ClickHouse / InfluxDB（column-oriented）對 `avg/min/max` 很快，適合跨商品分析；ingestion 走 OLTP → CDC → stream → OLAP，本專案的 CDC 管線加一個 consumer 即可。

### G. 冪等、cooldown 與回升重設

- `notification_id = sha256(subscription_id | event_seq)[:32]`：同一價格事件對同一訂閱只會有一筆 outbox；tailer at-least-once 重播、worker 重啟都不會重送。
- cooldown：`last_notified_price`。已通知 $120 → $118 再通知（更便宜了）、$119.5 不通知（沒更便宜）；記 `SKIPPED_COOLDOWN` 讓狀態可觀測。
- 回升重設：價格回到門檻之上 → `last_notified_price = NULL`，下次再跌破視為新事件。
- 小波動過濾：`|Δ| < 1%` 且沒人「這次才跨過門檻」→ 不通知也不寫 outbox。

### H. SSE dev-only vs SES email

原型 SSE 零憑證即可 demo；過 CloudFront/API Gateway 有 buffering、需單 process → dev-only。正式版 `notification_type=email` 走 SES：需驗證寄件身分、sandbox 內只能寄給已驗證收件人、要處理 bounce/complaint（SNS 回饋）；未設 `SES_FROM_EMAIL` 時 email channel 為 dry-run（只記 log）。push（APNs/FCM）可再加一個 channel。

### I. Auth 與匿名回報

正式版 Cognito + API Gateway JWT authorizer，訂閱以 `user_id` 隔離；env-gated（本機免登入）。extension 回報刻意**不帶 user_id**：只有 `reporter_token`（隨機、只用來限流），系統不知道誰看了什麼，符合使用者對隱私友善設計的偏好。

### J. Rate limiting

CloudFront + WAF per-IP、API Gateway 節流（infra 變數）；app 層 `reporter_token` 每分鐘 30 次（原型記憶體 deque，正式版 Redis INCR+EXPIRE）；對 Amazon 的出站限速 token bucket `CRAWL_RPS`。

### K. Monitoring

CloudWatch alarms → SNS（ALB/RDS/Redis/DynamoDB/API GW/CloudFront）、Synthetic canary 探 `/health`。app 指標：CDC lag（`/cdc/status`）、crawl 成功率 / captcha 率 / region_locked 率、通知 p95 延遲（事件 ts → VENDOR_ACCEPTED，對 1 小時 SLA）、queue 深度。

### L. Cleanup cron

EventBridge + Lambda 每日清 `notification_outbox`、`crawl_log`、已結案 `price_reports` 超過保留期的列；`prices` / `price_aggregations` 是產品本身，不清（DynamoDB 不設 TTL）。

### M. Error handling / 降級

全域例外處理（500 不洩漏、422 可讀）、Request ID、body 大小上限（413）。fail-soft：crawler 被擋只影響該商品（退避），API 照常服務歷史；Redis 故障 → 事件停在 checkpoint 之後不會丟（tailer 重連後補發，冪等吸收）；彙總 job 掛掉 → 讀路徑 raw fallback 仍可畫圖。
