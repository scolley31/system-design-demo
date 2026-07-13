# Earthquake Notification System

依「Design an Earthquake notification system」PDF 實作的可跑原型：使用者設定通知條件（震度 magnitude、距離 distance）→ 裝置回報位置 → 地震發生時，系統以低延遲通知**符合條件**的用戶，並**避免重複 / 亂序**推播。

設計依據見 [`DESIGN.md`](./DESIGN.md)（三大架構決策 + 深入探討 + 附錄）。本程式是依該設計實作、可本機零依賴跑起來的原型。

## 技術棧

FastAPI + SQLAlchemy + SQLite（原型）+ **h3**（server 端 geo）+ **h3-js**（前端算 cell）+ **sse-starlette**（即時送達）。Python 3.10+。

三大技術核心（見 DESIGN）：**H3 cell geo-index 找受影響用戶** · **per-channel queue + workers（Redis Streams）做 fan-out** · **supersession cache + outbox 做去重/版本覆蓋**。

## 安裝與啟動

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8010
```

啟動後開 **http://localhost:8010/** 即是 demo 前端。

可用環境變數覆寫（本機留空即走原型路徑）：`DATABASE_URL`、`REDIS_URL`、`LOCATION_TABLE`、`H3_RES`（預設 5）、`USGS_POLL`（設 1 開啟 USGS feed 自動灌入）。

## 前端（demo 頁）

單一檔 `app/static/index.html`（原生 HTML/JS，零建置）+ vendored `app/static/h3-js.umd.js`，由 FastAPI 在 `/` serve：

- 設定門檻（magnitude_min / distance_km）+ 座標 → **前端用 h3-js `latLngToCell` 算好 H3 cell 直送**（server 不換算）
- 開 SSE（`/api/v1/stream`）即時接收符合條件的地震通知
- 「觸發地震」面板（震央 + 震度 + version）模擬事件來源
- 看本裝置 outbox 狀態機（ENQUEUED → VENDOR_ACCEPTED / CANCELLED_SUPERSEDED）

> 開多個瀏覽器分頁 = 多台裝置，各自設不同門檻/座標，觀察只有符合者即時收到、且重複觸發不重送。

## API

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/` | demo 前端 |
| GET | `/health` | `{"status":"ok"}` |
| GET | `/api/v1/config/meta` | H3 解析度（前後端一致） |
| POST | `/api/v1/alerts/configuration` | `{device_id, magnitude_min, distance_km}` |
| GET | `/api/v1/alerts/configuration?device_id=` | 讀設定 |
| POST | `/api/v1/alerts/user_location` | `{device_id, cell}`（前端算好的 H3 index） |
| POST | `/api/v1/earthquakes` | 模擬事件 `{lat, long, magnitude, alert_id?, version?}` |
| GET | `/api/v1/stream?device_id=` | SSE 即時通知 |
| GET | `/api/v1/notifications?device_id=` | 讀 outbox 狀態機 |

## 與設計決策的對應

| 決策 | 實作位置 |
|------|---------|
| H3 cell geo-index（前端算 cell 直送） | `app/geo.py`、`app/geo_index.py`、`app/static/index.html` |
| Location 獨立儲存（DynamoDB / SQLite） | `app/location_store.py` |
| Broadcast Orchestrator（geo-target + 去重 + supersession） | `app/broadcast.py` |
| per-channel queue（Redis Streams / asyncio.Queue） | `app/queue.py` |
| Sender Worker（送達 + outbox 狀態機） | `app/worker.py` |
| Supersession cache（版本覆蓋） | `app/supersession.py` |
| Outbox 冪等（notification_id 雜湊） | `app/models.py`、`app/broadcast.py` |
| 即時送達（SSE） | `app/realtime.py`、`routes.py` `stream` |
| 事件來源 Gateway（手動 / USGS poller） | `app/gateway.py` |
| 全面 error handling | `app/errors.py` |

## 原型 vs Production（env-gated）

程式改動全 env-gated：本機不設環境變數 → SQLite + 記憶體 index/queue/cache + SSE，**雲端與本機互不影響**。

- **Geo index / Queue / Supersession**：記憶體 → **Redis / Redis Streams**（`REDIS_URL`）
- **Location DB**：SQLite → **DynamoDB**（`LOCATION_TABLE`，high-write ≈ 5.5K/s）
- **Config + Outbox DB**：SQLite → **RDS PostgreSQL**（`DATABASE_URL`）
- **送達**：SSE 到瀏覽器 → **APNs / FCM** worker（`app/worker.py` 換 sender）
- **事件來源**：手動 POST → 獨立 **Gateway** persistent feed
- **Worker**：主程序 asyncio task → 獨立 **worker fleet**

## 驗證

已本機驗證（見下）：符合條件者收到、門檻不符不收、距離外不收、重複不送、舊版本被 supersede。

```bash
B=http://localhost:8010/api/v1
# 前端會用 h3-js 算 cell；純 curl 測時用 python h3 代算（同 res）
CELL=$(python -c "import h3;print(h3.latlng_to_cell(25.03,121.56,5))")

# 裝置 A：門檻4 / 50km / 台北
curl -s -XPOST $B/alerts/configuration -H 'Content-Type: application/json' -d '{"device_id":"A","magnitude_min":4,"distance_km":50}'
curl -s -XPOST $B/alerts/user_location  -H 'Content-Type: application/json' -d "{\"device_id\":\"A\",\"cell\":\"$CELL\"}"

# 觸發 M6 台北地震 → A 命中（targeted=1）
curl -s -XPOST $B/earthquakes -H 'Content-Type: application/json' -d '{"lat":25.03,"long":121.56,"magnitude":6.0,"alert_id":"eq1","version":1}'
# 重送同 version → enqueued=0（冪等）；version=2 → 再送；version=1 → stale drop
curl -s "$B/notifications?device_id=A"   # 看 outbox 狀態機
```

## 本機 production path（Postgres + Redis Streams）

```bash
docker compose up --build   # Postgres(5433) + Redis(6380) + app(8010, uvicorn 單 process)
```

`LOCATION_TABLE` 不設 → user_location 走 Postgres（本地免 DynamoDB）；SSE 需單一 process（見 `app/realtime.py`）。

## AWS 部署（production）

用 **Terraform** 部署到 AWS（`ap-northeast-1`），IaC 與 runbook 在 [`./infra/`](./infra/)。架構：

```
  Client ─► [WAF per-IP rate limit] ─► CloudFront (CDN)
                     │ VPC Link
                     ▼
              API Gateway ─► 內部 ALB ─► EC2 ASG (API/Orchestrator)
                                          ├── RDS PostgreSQL   (config + outbox)
                                          ├── DynamoDB         (user_location, high-write)
                                          └── ElastiCache Redis(geo index + Streams + supersession)
              EC2 ASG (Worker) ─ 消費 Redis Streams ─► APNs / FCM

  Cognito(JWT) · SSM(設定) · Secrets Manager(DB 密碼) · ECR(映像)
  EventBridge + Lambda(outbox cleanup) · CloudWatch/SNS(監控告警)
```

> **即時送達**：正式版走 **APNs/FCM**（真正裝置推播）。demo 的 SSE（`/api/v1/stream`）過 CloudFront/API Gateway 有 buffering，屬 **dev-only**（見 DESIGN 附錄 J）。

### CI/CD（GitHub Actions，`.github/workflows/deploy-quake.yml`）

push 到 `Earthquake notification system/**` → OIDC assume role → `docker buildx --platform linux/arm64` → push ECR → `ssm put-parameter /quake/IMAGE_URI` → `ssm send-command tag:app=quake` → EC2 `deploy-app.sh`。前置 repo variable `AWS_DEPLOY_ROLE_ARN`（= `terraform output gha_deploy_role_arn`）。

### 部署 / 銷毀

```bash
cd infra && export PATH="$HOME/bin:$PATH"
terraform init && terraform apply       # 會計費
terraform output cloudfront_url         # 對外網址
terraform destroy                       # 不用時收掉
```

完整步驟見 [`./infra/README.md`](./infra/README.md)。
