# QR Code Generator

Dynamic QR code generator：提交 URL → 取得短碼 token + QR 圖片；掃描短碼經 302 導向目標 URL，可改、可軟刪、可設過期、可看掃描分析。

設計依據見 [`DESIGN.md`](./DESIGN.md)（16 題決策 + 優劣分析）。本程式是依該設計實作的可跑原型。

## 技術棧

FastAPI + SQLAlchemy + SQLite（原型）+ `qrcode[pil]`。Python 3.10+。

## 安裝與啟動

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

可用環境變數覆寫：`BASE_URL`（預設 `http://localhost:8000`）、`DATABASE_URL`（預設 SQLite）。

啟動後開瀏覽器到 **http://localhost:8000/** 即是管理頁前端。

## 前端（管理頁）

單一檔 `app/static/index.html`（原生 HTML/JS，零建置），由 FastAPI 在 `/` 直接 serve：

- 輸入 URL（可選過期時間）→ 產生 QR，顯示圖片 + 短網址 + 正規化後的 URL
- 清單列出所有 QR：QR 縮圖、短網址、目標 URL、掃描次數、建立/過期時間
- 每筆可：複製短網址、改目標 URL、看每日掃描分析、刪除

> 註：目前無使用者/登入概念，清單為**全域**。正式版應加 auth 並以 `user_id` 過濾（對應 PDF FR「用戶可以管理自己創建的 QR Code」）。對應後端端點 `GET /api/v1/qr`。

## API

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/` | 管理頁前端 |
| GET | `/api/v1/qr` | 列出所有未刪除 QR（含掃描總數，全域） |
| POST | `/api/v1/qr/create` | 建立，body `{url, expires_at?}` → `{token, short_url, qr_code_url, original_url}` |
| GET | `/r/{token}` | 掃描入口：302 導向 / 404（不存在）/ 410（已刪或過期） |
| GET | `/api/v1/qr/{token}` | 取得 metadata |
| PATCH | `/api/v1/qr/{token}` | 改 `url` / `expires_at`（部分更新） |
| DELETE | `/api/v1/qr/{token}` | 軟刪除 |
| GET | `/api/v1/qr/{token}/image?dimension=&color=&border=` | QR PNG |
| GET | `/api/v1/qr/{token}/analytics` | `{token, total_scans, scans_by_day[]}` |

## 與設計決策的對應

| 決策 | 實作位置 |
|------|---------|
| 8 碼 SHA-256+nonce+Base62 token | `app/token_gen.py` |
| 直接插入 + UNIQUE 例外重試（避免 race） | `app/routes.py` `create_qr` |
| 保守 URL 正規化（只 lower-case host）+ SSRF/黑名單 | `app/url_validator.py` |
| 302 redirect、404/410 區分、惰性過期 | `app/routes.py` `redirect` |
| 軟刪除 | `app/models.py`、`delete_qr` |
| Redis 快取（原型用含 TTL 的記憶體 dict 模擬） | `app/cache.py` |
| 非同步 scan 寫入（原型用 BackgroundTasks） | `app/routes.py` `_record_scan` |

## 原型 vs Production

原型為求零依賴上手，以下用輕量替代，並在程式註解標明替換點：

- **Cache**：記憶體 dict（含 TTL）→ 正式版 Redis（stateless 多台共享、全域 invalidation）
- **Scan 寫入**：`BackgroundTasks` → 正式版訊息佇列 + worker
- **QR 圖片**：即時生成 → 正式版預生成存 object store + CDN
- **分析**：同庫 `scan_events` 明細 + 即時 GROUP BY → 正式版獨立分析庫 + 預聚合每日計數表
- **DB**：SQLite → 正式版 PostgreSQL（單 primary + read replica + failover）

## 驗證

啟動後跑：

```bash
# 建立
curl -X POST http://localhost:8000/api/v1/qr/create -H "Content-Type: application/json" -d '{"url":"https://example.com"}'
# 掃描（302）
curl -o /dev/null -w "%{http_code}\n" http://localhost:8000/r/{token}
# 刪除後（410）、不存在（404）
curl -o /dev/null -w "%{http_code}\n" http://localhost:8000/r/INVALID
# 分析
curl http://localhost:8000/api/v1/qr/{token}/analytics
```

## AWS 部署（production）

已用 **Terraform** 部署到 AWS（`ap-northeast-1`）。程式改動全 **env-gated**：本機不設環境變數 → 維持 SQLite + 記憶體 cache + 即時生圖，**雲端與本機互不影響**。Runbook 與 IaC 在 [`../infra/`](../infra/)。

### 架構

```
           ┌──────── CloudFront (CDN) ────────┐
  Client ──┤  /qr-img/*       → S3 (QR PNG)    │ 長快取
           │  /, /r/*, /api/* → API Gateway    │ redirect 不快取
           └─────────────┬────────────────────┘
                         │ VPC Link
                         ▼
                內部 ALB (/health)
                         ▼
            EC2 Auto Scaling Group (Docker/gunicorn)
                 ├── RDS PostgreSQL
                 └── ElastiCache Redis

  SSM Parameter Store(設定) · Secrets Manager(DB 密碼) · ECR(映像) · NAT Gateway
```

env（雲端由 EC2 容器注入，本機留空即走原型路徑）：`DATABASE_URL`、`REDIS_URL`、`S3_BUCKET`、`CDN_BASE`、`BASE_URL`。
- `app/cache.py`：有 `REDIS_URL` → Redis，否則記憶體。
- `app/storage.py`：有 `S3_BUCKET` → 上傳 S3 並回 CDN URL，否則 `/image` 即時生圖。
- `app/database.py`：`DATABASE_URL` 預設 SQLite，可換 PostgreSQL。

### CI/CD（GitHub Actions，`.github/workflows/deploy.yml`）

```
git push (QR Code Generator/**)
   └─ GitHub Actions ── OIDC assume role（無長期金鑰）
        ├─ docker buildx --platform linux/arm64   # EC2 是 t4g/arm64
        ├─ push → ECR (tag = git SHA + latest)
        ├─ SSM put-parameter /qrcode/IMAGE_URI
        └─ SSM send-command (tag:app=qrcode) → EC2 deploy-app.sh
                                                 └─ ECR login → docker pull → docker run
   └─ ALB /health 通過 → 新版上線
```

前置：repo variable `AWS_DEPLOY_ROLE_ARN`（= `terraform output gha_deploy_role_arn`）。

### 部署 / 銷毀

```bash
cd ../infra && export PATH="$HOME/bin:$PATH"
terraform init && terraform apply        # 約 58 個資源（會計費）
terraform output cloudfront_url          # 對外網址
terraform destroy                        # 不用時收掉、停止計費
```

完整步驟（首次推映像、驗證、成本）見 [`../infra/README.md`](../infra/README.md)。
