# Load Test（k6）

對 Amazon Price Tracking Service 做壓力測試:驗 NFR「價格歷史 p95 < 500ms」,順便壓 extension 回報與訂閱兩條寫入路徑。

腳本 `price_load.js`(單檔多場景),包裝 `run.sh`。

## 目的

| 路徑 | 驗什麼 |
|---|---|
| `GET /api/v1/price/{asin}?period=…` | **NFR:p95 < 500ms**。2 年 × 每小時 = 17,520 筆 raw 也要靠 `price_aggregations`(daily/weekly/monthly)一次掃幾十筆就回,而不是 raw 逐筆掃(`source=raw_fallback`) |
| `POST /api/v1/price-reports` | extension 眾包回報寫入:每筆進 `price_reports` + 正常價直接寫 price 表;驗 per-token 限流(30/min)在合法流量下不誤殺 |
| `POST /api/v1/subscriptions` | 訂閱寫入 + `subscription_count` 計數更新 |

## 前置

```bash
brew install k6
```

**server 必須以 `MOCK_AMAZON=1` 啟動**:setup 會 track 合成 ASIN(`LOADTST000`…,真 Amazon 上不存在)、用 `POST /debug/seed` 回填假歷史,兩者都只在 mock / `ALLOW_SEED=1` 模式可用。

setup 做的事(每次跑都會做,冪等):
1. track `SEED` 個商品(預設 20)→ mock 爬蟲給 `last_price`(crawl_rps=1,約 SEED 秒補齊,setup 會輪詢等)。
2. 前 5 個回填 730 天 × 24 = 17,520 筆(約 1–2s/個),其餘 30 天 × 24;每個都跑 `POST /aggregations/run`。
3. `GET /products` 收集每個 ASIN 的 `last_price`,report / subscribe 場景據此算價格。

## 場景

| SCENARIO | 打什麼 | 預設 rate | 驗什麼 | threshold |
|---|---|---|---|---|
| `history` | `GET /price/{隨機 asin}?period={7d\|30d\|90d\|1y\|2y}` | 200/s | 200、`source == "aggregation"`、`points.length > 0` | `p95 < 500ms`(只掛在此場景) |
| `report` | `POST /price-reports` price = last_price × (1 ± 2%),永不觸發 30% 可疑判定 | 100/s | 200、`status == "accepted"` | — |
| `subscribe` | `POST /subscriptions` user=`load-{VU}-{ITER}`,threshold = 價 × 0.5~1.2 | 50/s | 200 | — |

全部 executor 皆 `constant-arrival-rate`(固定 RPS,VU 依 rate 自動配)。全域:`http_req_failed < 1%`、`checks > 99%`。

**report 的限流**:server 每個 `reporter_token` 30/min。腳本用 `k6-{VU}-{floor(ITER/25)}`——每個 token 一生只送 25 筆(< 30),所以不論 RATE 多大都不會 429;代價是 server 每分鐘看到 ≈ RATE×60/25 個新 token。想**測限流本身**就把腳本的 `ROTATE_EVERY` 調到 > 30,預期看到 429 上升。

env:`BASE_URL`、`SCENARIO`(由 run.sh 第 1 參數帶)、`SEED`、`RATE`、`DURATION`、`SMOKE`。

## 本機冒煙

```bash
cd "Amazon price tracking service"
MOCK_AMAZON=1 uvicorn app.main:app --port 8020      # 另一個 terminal
cd loadtest
SMOKE=1 ./run.sh history        # rate 5、15s、SEED 3
SMOKE=1 ./run.sh report
SMOKE=1 ./run.sh subscribe
```
確認 setup 建得了商品、回填 + 彙總成功(console 印 `setup: tracked 3/3, seeded 3, aggregated 3, priced 3`)、history 全部 `source=aggregation`、thresholds 生效。(本機單行程 + SQLite,絕對數字不代表 prod。)

## 正式壓測

```bash
./run.sh history                          # 200/s × 1m
RATE=500 DURATION=3m ./run.sh history     # 加壓找 knee
SEED=100 RATE=300 ./run.sh history        # 更多商品 → cache/彙總命中更分散
RATE=200 ./run.sh report                  # 寫入路徑 200/s
./run.sh subscribe
./run.sh history --summary-export=history.json   # 第 2 參數起原樣給 k6
```

## 雲端

### 1) 起雲端 + 調高 WAF
單一壓測 IP 會被 WAF per-IP rate limit 擋,壓測前在 `infra/terraform.tfvars` 把 WAF/節流拉高再 `terraform apply`,拿 `terraform output cloudfront_url`,確認 `curl $CF/health` → 200。

### 2) seed 資料
雲端 server 預設**不開 `/debug/seed`**(需 `ALLOW_SEED=1`)。二選一:
- 部署時暫時加 `ALLOW_SEED=1`(壓完拿掉),腳本 setup 照常回填;
- 或不開 seed,改 track 真實 ASIN 讓 crawler 累積歷史,並把腳本 `asinOf()` 換成你的 ASIN 清單。沒開 seed 時 setup 的 seed 呼叫會 404,history 場景的 `source=aggregation` check 依實際歷史長短可能落 `raw_fallback`。

### 3) 開壓測機(避免跨太平洋灌水 p95)
在與部署**同 region** 開一台 c-class EC2,裝 k6:
```bash
sudo dnf install -y https://dl.k6.io/rpm/repo.rpm && sudo dnf install -y k6
```
把 `loadtest/` 複製上去。

### 4) 跑
```bash
export BASE_URL=https://<cloudfront>
./run.sh history
./run.sh report
./run.sh subscribe
```

## 觀察什麼

- **k6 summary**:`http_req_duration{scenario:history}` p50/p95/p99、`http_req_failed`、各 check 通過率。p95 超過 500ms 或 error 上升的那一階 = **knee**。
- **`GET /api/v1/price/{asin}?period=2y` 回應本身**:`source` 應為 `aggregation`(`raw_fallback` = 彙總沒跟上,回頭掃 raw);`rows_scanned` 應是幾十(monthly 24 / weekly 104 / daily ≤ 90)而非 `raw_rows_in_period` 的 17,520;`latency_ms` 是 server 端查詢時間,和 k6 端差值 = 網路 + 序列化。
- **RDS**:`price_aggregations` 讀延遲 / `ReadIOPS` / `DatabaseConnections`;report 場景看 `WriteLatency`(每筆 2 次 INSERT:`price_reports` + price 表)。
- **Redis Streams 深度**:`GET /api/v1/cdc/status` 看 CDC tailer 的 checkpoint 與 lag——report 場景高寫入時 lag 是否拉開(通知路徑落後)。
- **DynamoDB throttles**(prod 用 DynamoDB 當 price 表時):`ThrottledRequests` / `ConsumedWriteCapacityUnits`。
- **App / ALB**:CPU、TargetResponseTime、5xx;`GET /aggregations/stats?product_id=` 看彙總是否被背景 job 持續更新。

## 善後

- `LOADTST*` 商品、其歷史、回報、訂閱都會留在 DB(scheduler 也會持續排它們的 mock 爬蟲)。
- 本機:停 server,刪 `price.db`(含 `-wal` / `-shm`)。
- 雲端 Postgres:
  ```sql
  DELETE FROM subscriptions   WHERE product_id LIKE 'LOADTST%';
  DELETE FROM price_reports   WHERE product_id LIKE 'LOADTST%';
  DELETE FROM price_aggregations WHERE product_id LIKE 'LOADTST%';
  DELETE FROM prices          WHERE product_id LIKE 'LOADTST%';
  DELETE FROM crawl_log       WHERE product_id LIKE 'LOADTST%';
  DELETE FROM products        WHERE asin LIKE 'LOADTST%';
  ```
  或整套 `TRUNCATE`。壓完記得把 `ALLOW_SEED` / WAF 設定還原(`terraform apply`),不用了就 `terraform destroy`。
