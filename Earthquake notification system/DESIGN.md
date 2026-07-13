# Earthquake Notification System — 設計討論文件（含選項與優劣）

## Context

讀完 PDF（系統設計題「Design an Earthquake notification system」）後，討論如何實作一個地震通知系統：使用者設定通知條件（震度 magnitude、距離 distance）→ 回報位置 → 地震發生時以 sub-second 低延遲通知**符合條件**的用戶，且**避免重複 / 亂序**推播。

本專案是依此設計實作的**可跑原型**（比照同 repo 的 QR Code Generator）：本機零依賴（SQLite + 記憶體 + SSE）即可示範，`docker compose` 走 production code path（Postgres + Redis Streams），並附本 app 專屬的 AWS Terraform。技術棧 **Python + FastAPI**。

---

## 三大關鍵架構決策（決策地圖）

整個系統由三個環環相扣的決策定調——每個決策自然導向下一個：

| 關鍵決策 | 選擇 | 為什麼 / 連鎖反應 |
|---|---|---|
| **① 如何找出受影響用戶** | **Geo-index（H3 cell grid + KV index）** | raw lat/long 查表要 full scan、B-tree 不適合 2D；改用階層式 geo cell 當 key。代價：需維護 cell→devices 索引 + TTL，逼出 KV（Redis）。**位置由裝置端算好 H3 cell 直送**（計算下放 client、隱私友善）。 |
| **② 如何快速送達大量用戶** | **queue 解耦 + per-channel workers（Redis Streams）** | 直推會讓「送通知」跟「拉事件」搶資源、單點故障拖垮全鏈。用 queue 把 ingest / targeting / sending 三段解耦，per-channel 隔離故障。代價：多一層 queue → 選 memory-first 的 Redis Streams 保低延遲。 |
| **③ 如何避免重複/亂序** | **supersession cache + outbox idempotency** | fan-out + retry 必然產生重複；事件會有多版本更新。用 `notification_id = hash(alert_id\|version\|device_id)` 當冪等關卡、`supersession:{alert_id}→latest_version` 做版本覆蓋。 |

→ ①決定「找誰」、②決定「怎麼送」、③決定「不重複地送」。下面逐項展開。

---

## 需求摘要

**FR**：① 使用者設定通知條件（距離、震度）；② 地震發生時通知符合條件的用戶。
**NFR**：① sub-second 低延遲送達所有目標用戶；② 盡量避免重複推播。
**容量**（以台灣 ~2,000 萬用戶估）：每人每小時回報一次位置 → `20M / 3600 ≈ 5.5K writes/s`。位置精準度要求不高（移動 100km 常需數小時）→ 位置更新頻率遠高於設定更新 → 位置用**獨立 service/DB**。

---

## 技術選型定案（與討論結論）

| 面向 | 定案 | 為什麼（淘汰誰） |
|---|---|---|
| Geo index | **H3**（前端算 cell 直送） | 圓形擴散 + 六邊形鄰居等距（`gridDisk`）+ 前端 h3-js 好算。淘汰 Geohash（鋸齒/高緯失真）；S2 於複雜多邊形較優但本題非必要（見附錄 A）。 |
| Queue / fan-out | **Redis Streams** | memory-first、sub-second fan-out、per-channel 好切。淘汰 Kafka（disk-first 高吞吐 log，延遲不利）、SQS（延遲/排序控制較弱）。 |
| Location DB | **DynamoDB** | 位置 high-write（5.5K/s）、key=device_id、可設 TTL。原型用 SQLite 表。 |
| Config + Outbox DB | **PostgreSQL (RDS)** | config 關聯查詢、outbox 交易/批次 supersession 更新需交易語意。 |
| Geo index / Supersession | **Redis 一套全包** | 與 Streams 同一套，營運單純、延遲低。 |
| Compute | **EC2 ASG**（API/Orchestrator + Worker 各自 ASG） | 對齊 QR infra 藍本；worker 獨立 ASG 可 per-channel 擴縮。 |
| 即時送達（原型） | **SSE + 手動觸發**；production 換 **APNs/FCM** | SSE 零外部憑證即可 demo；SSE 過 CDN/APIGW 有 buffering → 標 dev-only（附錄 J）。 |

---

## API（定案，對齊 PDF，加 `/api/v1`）

| 方法 | 路徑 | 說明 | 回應 |
|------|------|------|------|
| GET | `/api/v1/config/meta` | 給前端的公開設定（H3 解析度，前後端一致） | `{h3_res}` |
| POST | `/api/v1/alerts/configuration` | `{device_id, magnitude_min, distance_km}` | 設定內容 |
| GET | `/api/v1/alerts/configuration?device_id=` | 讀設定 | 設定 / 404 |
| POST | `/api/v1/alerts/user_location` | `{device_id, cell}`（**前端算好的 H3 index**） | `{device_id, cell}` |
| POST | `/api/v1/earthquakes` | 模擬事件來源 `{lat, long, magnitude, alert_id?, version?}` | broadcast 結果 |
| GET | `/api/v1/stream?device_id=` | **SSE**：即時收通知 | event stream |
| GET | `/api/v1/notifications?device_id=` | debug：讀 outbox 狀態機 | outbox 列 |

身分：正式版 JWT（Cognito）放 header，env-gated；原型免登入。`device_id` 為裝置識別（原型由前端產生，一個分頁 = 一台裝置）。

---

## 資料模型

- **alert_config**：`device_id`(PK，原型；正式版 user_id), `magnitude_min`, `distance_km`, `status`, `created_at`, `updated_at`
- **user_location**：`device_id`(PK), `user_id`, `cell`(H3 index), `updated_at` — **只存 cell、不存 raw lat/long**（前端算、隱私友善）
- **notification_outbox**：`notification_id`(PK=`hash(alert_id|version|device_id)`), `alert_id`, `version`, `device_id`, `channel`, `status`, `updated_at`
  - status：`ENQUEUED | ATTEMPTED | VENDOR_ACCEPTED | FAILED | CANCELLED_SUPERSEDED`
  - terminal = `ATTEMPTED` 之後（含）的狀態；`CANCELLED_SUPERSEDED` 不算 terminal（可被覆蓋）

---

## 深入探討 1：如何有效率找出受影響用戶？

**寫入頻率**：5.5K writes/s（見容量）。**查找效率**：raw lat/long 需 full table scan；B-tree 不適合 2D geometry、proximity search 差 → non-starter。

**方案演進**：
1. **(Naive)** 每次 alert 用 raw lat/long 查 OLTP 算距離 → full scan，撐不住百萬用戶。
2. **(Better)** NoSQL + 自訂 geo key（cell_id）存 Cassandra/Dynamo → 高寫入、以 cell_id 分片；仍需額外結構支援即時推播。
3. **(Best，本專案)** **H3 cell grid + KV index**：用固定解析度 cells 表示地球，在高速 KV（Redis）維護 `cell → device_refs` + 反向 `device → cell`（供搬移/清理），設 TTL（7–30 天）。
   - **Writes**：App 回報的是 **cell id**（不是 lat/long），O(100k)/s 可撐。
   - **Lookups（alert time）**：對「震央圓形影響範圍」用 `gridDisk`（或對真實 shaking polygon 用 `polygon_to_cells`）→ 得 cell 清單 → 撈候選 devices → 去重 → 依 channel enqueue。
   - **距離二次裁切**：因只存 cell，用 `cell_to_latlng` 取 cell 中心對震央算 haversine，濾掉 cell 邊界的 false positive。
   - Pros：低延遲、行為可預測、隱私友善（只存粗粒度 cell）。Cons：邊界有少量 false positive（可提高解析度或精算裁切）。

**實作對應**：`app/geo.py`（cells_for_event / cell_center / haversine）、`app/geo_index.py`（記憶體 / Redis）、`app/location_store.py`（SQLite / DynamoDB）。

---

## 深入探討 2：如何把警報快速送達所有受影響用戶？

1. **(Naive)** 收事件的 server 直接推所有人 → ① 量大時無法「快速」送達；② 送通知與拉事件搶資源，推播端出問題會拖慢 event pulling。
2. **(Improved，本專案)** **用 queue 解耦以隔離失敗**：
   - **Gateway**：對地震來源維持 persistent feed（TLS/heartbeat/reconnect/backoff、malformed 隔離）。原型用手動 `POST /earthquakes` +（選配）USGS poller 模擬（`app/gateway.py`）。
   - **Broadcast Service (Orchestrator)**：geo-targeting → 過濾 config → 去重 → 依 channel 分 chunk → enqueue（`app/broadcast.py`）。
   - **Workers**：輕量 stateless，從 per-channel queue 取 job，實際呼叫 vendor（原型：SSE；正式版：APNs/FCM）（`app/worker.py`）。
   - **三層 queue 的好處**：ingest 與 targeting 可獨立 scaling；Orchestrator 重啟不掉事件（fail-soft）；per-channel 隔離（APNs brownout 只堆 APNs queue，FCM/SSE 照流）。
3. **queue 選型**：**Redis Streams**（memory-first、低延遲 fan-out）；Kafka 是 disk-first 高吞吐 log，非極低延遲 fan-out 的最佳解（見附錄 B）。

---

## 深入探討 3：如何避免重複推播 / 版本亂序？

兩種情況：**Duplicates**（同 alert 同 version 不可送同一人兩次）、**Out-of-order**（同事件較新版本要覆蓋較舊）。兩個元件：

- **Supersession cache**：`supersession:{alert_id} → latest_version`（`app/supersession.py`）。
- **notification_outbox**：每個 `notification_id` 一筆，當 idempotency gate + 狀態帳本。

**避免同版本重複**（`broadcast.py` / `worker.py`）：
- Orchestrator 算 `notification_id`；若 outbox 已存在且非可重試狀態 → 不 enqueue；否則寫 `ENQUEUED` 並入 queue。
- Worker 讀 outbox；若已 terminal → 直接 drop；送達後 upsert `ATTEMPTED → VENDOR_ACCEPTED`（或 `FAILED`）。

**處理版本覆蓋（supersession）**：
- Orchestrator 更新 supersession cache；進來 version 比 latest 舊 → 整批 drop；同 alert_id 中 `version < latest` 且未 terminal 的 outbox → 批次 `CANCELLED_SUPERSEDED`。
- Worker 送達前再讀 supersession；若 job.version < latest → 跳過並標 `CANCELLED_SUPERSEDED`（若尚未 terminal）。

---

## 原型 vs Production（env-gated）

| 面向 | 原型（本機） | Production（雲端） | 切換 env |
|---|---|---|---|
| Geo index | 記憶體 dict（含 TTL） | Redis | `REDIS_URL` |
| Queue | asyncio.Queue（per-channel） | Redis Streams | `REDIS_URL` |
| Supersession | 記憶體 dict | Redis | `REDIS_URL` |
| 送達 | SSE 到瀏覽器 | APNs / FCM worker | `PUSH_BACKEND`（擴充位） |
| Location DB | SQLite `user_location` | DynamoDB | `LOCATION_TABLE` |
| Config + Outbox | SQLite | RDS PostgreSQL | `DATABASE_URL` |
| 事件來源 | 手動 POST /（選配）in-proc poller | 獨立 Gateway persistent feed | 部署形態 |
| Worker | 主程序 asyncio task | 獨立 worker fleet | 部署形態 |

沿用 QR 的「工廠函式 + 延遲匯入」寫法：未設 env → 記憶體實作，本機零依賴。

---

## 附錄

### A. Geo index 選型（H3 vs S2 vs Geohash）

| 面向 | H3（Uber） | S2（Google） | Geohash |
|---|---|---|---|
| 形狀 | 六邊形（少數五邊形） | 四邊形（近似正方形） | 矩形 |
| 切割 | 二十面體鋪六角，每層分 7（非完美巢狀） | 立方體 6 面 quadtree，每層分 4（**完美巢狀**，父 ID 是子 range 前綴） | 經緯二分 |
| 鄰居 | **6 鄰等距**（`gridDisk` 圓形擴散乾淨） | 邊鄰/角鄰距離不等 | 需處理邊界跳格 |
| 面積均勻 | 最接近等面積（中心近似距離誤差小） | 變形可預期、易控 | 高緯失真嚴重 |
| 多邊形覆蓋 | `polyfill` 夠用 | `RegionCoverer` 最強（貼行政/斷層邊界、混層級） | 鋸齒 + cell 暴增 |
| 前端 | h3-js 成熟直覺 | JS 綁定較弱 | 簡單但精度差 |

**結論**：震央圓形影響 + 鄰居等距 + 前端好算 → **H3**。若場景是「縣市/斷層多邊形精準覆蓋 + range query」則 S2 較優；Geohash 於本題淘汰。

### B. Redis Streams vs Kafka（queue 選型）

Redis Streams：memory-first、協調成本低、極低延遲 fan-out，consumer group 可 per-channel。Kafka：disk-first、為高吞吐與高持久性 log 設計，延遲較高。地震要 sub-second → Redis Streams；若要長期回放/審計可另接 Kafka 做冷路徑。

### C. Outbox 狀態機與亂序

見「深入探討 3」。terminal = ATTEMPTED 之後。`notification_id` 冪等保證同版本不重送；supersession 保證舊版本被取消/跳過。

### D. 資料庫分工

Location→DynamoDB（high-write、key-based、TTL 自動過期）；Config+Outbox→Postgres（交易/批次更新）；Redis 扛 index/queue/supersession。三者各取所長，避免把 high-write 位置壓在單 primary RDS。

### E. Auth（Cognito）

正式版 Cognito + API Gateway JWT authorizer，資料以 device/user 隔離；env-gated（本機免登入）。

### F. Rate limiting

CloudFront 掛 WAF rate-based rule（per-IP），API Gateway 整體節流當 backstop；限速值為 Terraform 變數。

### G. Monitoring / Alerting

CloudWatch alarms → SNS email（ALB/RDS/ElastiCache/API GW/Lambda/CloudFront/DynamoDB）、app logs → CloudWatch Logs、Dashboard、Synthetic canary 每 5 分鐘探 `/health`。

### H. Cleanup cron

EventBridge Scheduler + Lambda 每日清 `notification_outbox` 超過保留期的列；`user_location` 由 DynamoDB TTL 自動過期。

### I. Error handling / 依賴降級

全域例外處理（500 不洩漏、422 可讀）、Request ID（`X-Request-ID`）、body 大小上限（413）。依賴降級：Redis 故障時的路徑退化策略（`app/errors.py`）。

### J. SSE dev-only vs APNs/FCM production

原型即時送達用 SSE（零憑證、瀏覽器可 demo），但 SSE 過 CloudFront/API Gateway 有 buffering/長連線疑慮 → 標 **dev-only**。正式版即時送達走 **APNs/FCM**（真正的裝置推播），不依賴長連線經 CDN。此時 `realtime.py` 的行程內 hub 限制也不存在。
