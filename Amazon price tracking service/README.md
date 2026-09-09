# Amazon Price Tracking Service

依「Design Amazon Price Tracking Service」PDF 實作的可跑原型：使用者查看 Amazon 商品**價格歷史**（< 500ms）→ 訂閱**降價門檻**通知（價格變動後 1 小時內）→ crawler **真的抓 amazon.com** + 模擬 Chrome extension **眾包回報**（含完整性驗證）。

設計依據見 [`DESIGN.md`](./DESIGN.md)（三大架構決策 + 深入探討 + 附錄，含 Amazon 反爬實測筆記）。本程式是依該設計實作、可本機零依賴跑起來的原型。

## 技術棧

FastAPI + SQLAlchemy + SQLite（原型）+ **curl_cffi**（模仿 Chrome TLS 指紋抓 Amazon）+ **selectolax**（解析）+ **sse-starlette**（即時通知）。Python 3.10+。Playwright 無頭瀏覽器為選配 fallback。

三大技術核心（見 DESIGN）：**extension 眾包 + 優先式爬取 + 完整性驗證** · **CDC → Redis Streams → price-change worker（outbox 冪等 + cooldown）** · **pre-aggregation 表（daily/weekly/monthly）供圖表**。

## 安裝與啟動

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8020          # 真抓 amazon.com
# 或離線 / CI：
MOCK_AMAZON=1 uvicorn app.main:app --reload --port 8020
```

啟動後開 **http://localhost:8020/** 即是 demo 前端。

可用環境變數覆寫（本機留空即走原型路徑）：`DATABASE_URL`、`REDIS_URL`、`PRICE_TABLE`（DynamoDB）、`MOCK_AMAZON`、`AMAZON_DOMAIN`（預設 www.amazon.com）、`AMAZON_CURRENCY`（USD）、`CRAWL_RPS`（1）、`CRAWLER_PLAYWRIGHT`、`SUSPICIOUS_DROP_PCT`（0.3）、`MIN_CHANGE_PCT`（0.01）、`SES_FROM_EMAIL`、`SCHEDULER_TICK_S`、`AGG_INTERVAL_S`、`ROLE`。

> **從台灣 IP 抓 amazon.com**：可國際配送的商品（書、配件，例如 `0135957052`、`B0CHX3QBCH`）看得到價格；很多電子產品會是「This item cannot be shipped to your selected delivery location」→ 系統標 `region_locked`（Amazon 對國際 IP 改配送地要登入）。正式版 crawler 要放美國出口 IP，見 DESIGN 附錄 E。

Playwright fallback（選配）：`pip install playwright==1.49.1 && playwright install chromium`，再以 `CRAWLER_PLAYWRIGHT=1` 啟動；`FORCE_PLAYWRIGHT=1` 可強制走 fallback 驗證。已本機驗證：curl_cffi 兩個指紋都被擋時自動升級 Playwright，同樣抓到 $45.97（USD cookie 一致），每次約 2–3.5 秒（curl_cffi 約 0.3–2 秒）。

## 前端（demo 頁）

單一檔 `app/static/index.html`（原生 HTML/JS，零建置，inline SVG 折線圖），由 FastAPI 在 `/` serve。六個面板：

1. **追蹤商品 + 價格歷史**：輸入 ASIN 或商品 URL → 註冊 + 最高優先爬取；period 7d/30d/90d/1y/2y；顯示 `source`（aggregation / raw_fallback）、讀了幾列、查詢毫秒。
2. **訂閱門檻 + 即時通知**：`POST /subscriptions` + SSE feed；列出訂閱與上次通知價（cooldown）。
3. **模擬 Chrome extension 回報**：「正常回報 −3%」直接入庫；「可疑回報 −60%」→ pending_verify → 最高優先重爬 → confirmed / rejected。
4. **Crawler 面板**：每個商品的優先分數、爬取間隔、下次到期、用了哪層 fetcher、狀態；「立即爬」。
5. **Outbox 狀態機 + CDC 狀態**：ENQUEUED → ATTEMPTED → VENDOR_ACCEPTED / SKIPPED_COOLDOWN；checkpoint / head / lag。
6. **立即彙總 / raw vs 彙總列數**（mock 模式多一個「灌 2 年假資料」按鈕看 17,520 列 → 30 列的差異）。

> 開多個瀏覽器分頁 = 多個使用者（各自 user_id / reporter_token）。

## API

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/` | demo 前端 |
| GET | `/health` | `{"status":"ok"}` |
| GET | `/api/v1/config/meta` | 公開設定（mock / domain / 粒度規則 / 後端型別） |
| GET | `/api/v1/price/{product_id}?period=30d&granularity=` | **PDF API 1**：價格歷史（彙總表；無彙總則 raw fallback） |
| POST | `/api/v1/subscriptions` | **PDF API 2**：`{user_id, product_id, price_threshold, notification_type, email?}` |
| GET / DELETE | `/api/v1/subscriptions?user_id=` / `/api/v1/subscriptions/{id}` | 讀 / 取消 |
| POST | `/api/v1/price-reports` | extension 回報 `{product_id, price, reporter_token}` → accepted / pending_verify |
| GET | `/api/v1/price-reports?product_id=` | 回報狀態機 |
| POST | `/api/v1/products/track` | `{product_id}`（ASIN 或 URL）註冊 + 最高優先爬 |
| GET | `/api/v1/products` | 商品 + score / interval / due_in / fetcher / 狀態 |
| POST | `/api/v1/products/{asin}/crawl?sync=` | 立即爬取 |
| GET | `/api/v1/crawl/log` | 爬取紀錄 |
| POST | `/api/v1/aggregations/run?product_id=` | 立即彙總 |
| GET | `/api/v1/aggregations/stats?product_id=` | raw vs daily/weekly/monthly 列數 |
| GET | `/api/v1/stream?user_id=` | SSE 即時通知 |
| GET | `/api/v1/notifications?user_id=` | outbox 狀態機 |
| GET | `/api/v1/cdc/status` | CDC checkpoint / lag / queue 深度 |
| POST | `/api/v1/debug/seed`、`/api/v1/debug/mock-price` | 只在 `MOCK_AMAZON=1` / `ALLOW_SEED=1` 開放 |

## 與設計決策的對應

| 決策 | 實作位置 |
|------|---------|
| 真抓 Amazon：curl_cffi 指紋輪替 → Playwright fallback、token bucket 限速、mock | `app/crawler/fetcher.py` |
| 商品頁解析（主價格區塊、region_locked、captcha 偵測） | `app/crawler/parser.py` |
| 優先式爬取（分數 / 間隔 / due-ness / urgent） | `app/scheduler.py`、`app/catalog.py` |
| Crawl worker + 完整性驗證結案 | `app/worker_crawl.py` |
| extension 回報 ingest（可疑 → 不入庫先重爬；匿名限流） | `app/reports.py` |
| append-only Price DB（SQLite / DynamoDB） | `app/price_store.py` |
| CDC tailer（seq 輪詢 / DynamoDB Streams） | `app/cdc.py` |
| per-channel queue（asyncio / Redis Streams） | `app/queue.py` |
| price-change worker（join 訂閱、過濾、outbox 冪等、cooldown）+ sender | `app/worker_notify.py`、`app/channels.py` |
| pre-aggregation（粒度選擇、raw fallback、增量彙總） | `app/aggregation.py` |
| 即時送達（SSE） | `app/realtime.py`、`routes.py` `stream` |
| 全面 error handling | `app/errors.py` |

## 原型 vs Production（env-gated）

程式改動全 env-gated：本機不設環境變數 → SQLite + 記憶體 queue + SSE，**雲端與本機互不影響**。

- **Price DB + CDC**：SQLite seq 輪詢 → **DynamoDB + DynamoDB Streams**（`PRICE_TABLE`）
- **Queue**：asyncio.Queue → **Redis Streams**（`REDIS_URL`）
- **目錄 / 訂閱 / 彙總 / Outbox**：SQLite → **RDS PostgreSQL**（`DATABASE_URL`）
- **通知**：SSE / email dry-run → **SES**（`SES_FROM_EMAIL`）
- **Crawler**：本機 IP → worker ASG（美國出口 IP / proxy pool）；`MOCK_AMAZON=1` 離線
- **背景迴圈**：主程序 asyncio task → worker ASG（`ROLE=worker`）

## 驗證

已本機驗證（mock 與真抓皆跑過）：追蹤 → 爬取 → 訂閱 → 降價通知（SSE）→ 同價過濾 → 再跌再通知 → 正常回報入庫 → 可疑回報被拒且不觸發通知 → 17,520 列彙總後 30 天圖讀 31 列 < 1ms。

```bash
B=http://localhost:8020/api/v1; J='Content-Type: application/json'
# 真抓（可國際配送的商品）
curl -s -XPOST "$B/products/0135957052/crawl?sync=1"        # {"status":"ok","price":45.97,"fetcher":"curl_cffi",...}
curl -s -XPOST "$B/products/B09B8V1LZ3/crawl?sync=1"        # 非美國 IP → "status":"region_locked"

# mock 模式（MOCK_AMAZON=1 啟動）走完整鏈
curl -s -XPOST $B/products/track -H "$J" -d '{"product_id":"B0MOCK0001"}'
curl -s -XPOST $B/debug/seed -H "$J" -d '{"product_id":"B0MOCK0001","days":730}'   # 17,520 列，checkpoint 推過
curl -s "$B/price/B0MOCK0001?period=30d"                     # source=raw_fallback, rows_scanned=720
curl -s -XPOST "$B/aggregations/run?product_id=B0MOCK0001"
curl -s "$B/price/B0MOCK0001?period=30d"                     # source=aggregation, rows_scanned=31
curl -s -XPOST $B/subscriptions -H "$J" -d '{"user_id":"u1","product_id":"B0MOCK0001","price_threshold":150,"notification_type":"sse"}'
curl -s -N "$B/stream?user_id=u1" &                          # SSE
curl -s -XPOST $B/debug/mock-price -H "$J" -d '{"product_id":"B0MOCK0001","price":140}'
curl -s -XPOST "$B/products/B0MOCK0001/crawl?sync=1"         # → CDC → worker → SSE price_drop
curl -s "$B/notifications?user_id=u1"                        # VENDOR_ACCEPTED
curl -s -XPOST $B/price-reports -H "$J" -d '{"product_id":"B0MOCK0001","price":30,"reporter_token":"r1"}'  # pending_verify → rejected
curl -s $B/cdc/status
```

## 本機 production path（Postgres + Redis Streams）

```bash
docker compose up --build                 # Postgres(5434) + Redis(6381) + app(8020)，預設真抓
MOCK_AMAZON=1 docker compose up --build   # 離線
WITH_PLAYWRIGHT=1 docker compose up --build   # 含 Playwright fallback（映像 +400MB）
```

`PRICE_TABLE` 不設 → prices 走 Postgres（BIGSERIAL seq 給 tailer 輪詢，本地免 DynamoDB）；SSE 需單一 process。

### 本機驗 DynamoDB + Streams 路徑（DynamoDB Local）

已驗證：put → Streams shard → tailer 發事件 → worker → SSE 收到；重啟後從 `cdc_checkpoint`（per-shard SequenceNumber）續讀、舊事件不重播。

```bash
docker run -d --rm --name ddb-local -p 8100:8000 amazon/dynamodb-local -jar DynamoDBLocal.jar -inMemory -sharedDb
export AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://localhost:8100
python -c "import boto3;boto3.client('dynamodb').create_table(TableName='price-local',AttributeDefinitions=[{'AttributeName':'product_id','AttributeType':'S'},{'AttributeName':'ts','AttributeType':'N'}],KeySchema=[{'AttributeName':'product_id','KeyType':'HASH'},{'AttributeName':'ts','KeyType':'RANGE'}],BillingMode='PAY_PER_REQUEST',StreamSpecification={'StreamEnabled':True,'StreamViewType':'NEW_IMAGE'})"
PRICE_TABLE=price-local MOCK_AMAZON=1 uvicorn app.main:app --port 8020   # /api/v1/config/meta → price_backend=dynamodb；/cdc/status → backend=dynamodb_streams
```

## 壓測

`loadtest/`（k6）：`history`（驗 p95 < 500ms）、`report`（extension 寫入路徑）、`subscribe`。見 [`loadtest/README.md`](./loadtest/README.md)。

## 簡報

`./.venv/bin/pip install python-pptx && ./.venv/bin/python build_ppt.py` → `Amazon_Price_Tracking_Service.pptx`。

本機預覽 / 檢查版面（無 Keynote/PowerPoint 時）：`brew install --cask libreoffice && brew install poppler`，然後

```bash
FONTCONFIG_FILE=/opt/homebrew/etc/fonts/fonts.conf /Applications/LibreOffice.app/Contents/MacOS/soffice \
  --headless --convert-to pdf Amazon_Price_Tracking_Service.pptx   # 不設 FONTCONFIG_FILE 會找不到 PingFang，中文全空白
pdftoppm -png -r 60 Amazon_Price_Tracking_Service.pdf slide         # 每頁一張 PNG
```

## AWS 部署（production）

用 **Terraform** 部署到 AWS（`ap-northeast-1`），IaC 與 runbook 在 [`./infra/`](./infra/)。架構：

```
  Client / extension ─► [WAF per-IP rate limit] ─► CloudFront (CDN)
                     │ VPC Link
                     ▼
              API Gateway ─► 內部 ALB ─► EC2 ASG (api)
                                          ├── RDS PostgreSQL   (products / subscriptions / aggregations / outbox / checkpoints)
                                          ├── DynamoDB price   (append-only, Streams NEW_IMAGE = CDC)
                                          └── ElastiCache Redis(Streams: crawl / price_changed / notify:*)
              EC2 ASG (worker) ─ crawler(→ amazon.com) · CDC tailer(DynamoDB Streams) · notify workers ─► SES

  Cognito(JWT) · SSM(設定) · Secrets Manager(DB 密碼) · ECR(映像)
  EventBridge + Lambda(cleanup) · CloudWatch/SNS(監控告警)
```

> Amazon 對非美國 IP 藏 buybox 價格；正式版 worker 需美國出口 IP 或 proxy pool（DESIGN 附錄 E）。demo 的 SSE 過 CloudFront/API Gateway 有 buffering，屬 dev-only（附錄 H）。

### CI/CD（GitHub Actions，`.github/workflows/deploy-price.yml`）

目前 `workflow_dispatch` 手動觸發（infra 尚未 apply）。流程：OIDC assume role → `docker buildx --platform linux/arm64` → push ECR → `ssm put-parameter /price/IMAGE_URI` → `ssm send-command tag:app=price` → EC2 `deploy-app.sh`。前置 repo variable `AWS_DEPLOY_ROLE_ARN`（= `terraform output gha_deploy_role_arn`）。

### 部署 / 銷毀

```bash
cd infra && export PATH="$HOME/bin:$PATH"
terraform init && terraform apply       # 會計費
terraform output cloudfront_url         # 對外網址
terraform destroy                       # 不用時收掉
```

完整步驟見 [`./infra/README.md`](./infra/README.md)。
