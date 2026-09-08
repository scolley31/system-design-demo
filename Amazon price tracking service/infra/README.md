# Infra — AWS 部署 (Terraform)

Amazon Price Tracking Service 的 production 雲端部署。架構:CloudFront → API Gateway (VPC Link) → 內部 ALB → EC2 ASG (Docker) → RDS PostgreSQL + ElastiCache Redis + DynamoDB（+ DynamoDB Streams 作 CDC）+ SES（選用）。

app 原始碼在 `../`（`Amazon price tracking service/`，FastAPI，port 8000，`/health`）。

## 資料層與角色

- **RDS PostgreSQL**：`products`（商品 + 排程狀態）、`subscriptions`（使用者訂閱門檻）、`price_reports`（extension 眾包回報）、`price_aggregations`（讀路徑預先彙總 daily/weekly/monthly）、`notification_outbox`（冪等關卡 + 狀態帳本）、`cdc_checkpoint`（CDC 消費位置）、`crawl_log`（每次爬取一筆觀測）。
- **DynamoDB `${project}-price`**：partition key `product_id`、sort key `ts`（epoch ms），append-only 價格歷史，PAY_PER_REQUEST，無 TTL。**開啟 DynamoDB Streams（`NEW_IMAGE`）作為 CDC 來源**：每筆新價格寫入即成為 stream record，下游據此判斷是否觸發通知。
- **ElastiCache Redis**（單一組）：Redis Streams 通道 `crawl`（爬取任務）/ `price_changed`（CDC 產出的變價事件）/ `notify:sse`、`notify:email`（送達佇列）。
- **SES**（選用）：`ses_from_email` 有值才建 email identity 並給 EC2 role `ses:SendEmail` 權限；留空則 email channel 停用（`/api/v1/config/meta` 的 `email_backend` 會顯示 `disabled`）。
- **兩組 ASG（同一映像，靠 `ROLE` env 區分）**：
  - `api`：對外 web，掛在 ALB 後（API Gateway VPC Link 只打這組）。
  - `worker`：跑 crawler + CDC tailer（DynamoDB Streams）+ notify workers，不掛 ALB。
  - 註：app 端 `ROLE` env 已 plumbing（`../app/config.py`），但目前原型兩者跑同一份映像的
    完整程序（worker 在 API 程序內以 asyncio task 跑，見 `../app/main.py`）。日後 container
    entrypoint 可依 `ROLE` 分流成「只跑 web」/「只跑 worker fleet」。

### Stream 消費方式（CDC）

worker 程序內的 `DynamoStreamTailer`（`../app/cdc.py`）用 boto3 `dynamodbstreams`：
`describe_table` 取 `LatestStreamArn` → `describe_stream` 列 shard → `get_shard_iterator` →
`get_records` 輪詢；每個 shard 的位置存在 Postgres `cdc_checkpoint`（`consumer = ddb:{shard_id}`），
重啟後從 checkpoint 續讀。優點是單一 codebase、不需 Lambda；EC2 role 因此需要
`dynamodb:DescribeStream / GetShardIterator / GetRecords / ListStreams`（compute 模組已授權）。

替代方案（正式版可換）：
- Lambda event source mapping 吃 DynamoDB Streams → 轉發到 Redis Streams `price_changed`（AWS 代管 shard / checkpoint / 重試）。
- KCL 的 DynamoDB Streams adapter：多 worker 之間做 shard lease，避免同一 shard 被重複消費。

注入容器的環境變數（由 SSM + user_data）：`DATABASE_URL`、`REDIS_URL`、`PRICE_TABLE`、`AMAZON_DOMAIN`、`CRAWL_RPS`、`SES_FROM_EMAIL`、`SUSPICIOUS_DROP_PCT`、`MIN_CHANGE_PCT`、`ROLE`、`BASE_URL` 及 Cognito 相關（auth 模組寫入）。此 app 無 S3 / 圖片儲存。

> 爬取來源 IP：EC2 走 NAT Gateway 出去，固定少數 IP 對 Amazon 高頻請求容易被回 captcha 頁。
> app 端遇到 captcha 會把商品標成 `captcha` 並退避（見 `../app/scheduler.py`）；正式版需加
> proxy pool / 住宅 IP 輪替，並把 `crawl_rps` 壓在每 IP 1 req/s 以下。

> 即時送達：`/api/v1/stream` 是長連線 SSE，原型讓它穿過 CloudFront/APIGW 方便 demo；
> 正式版走 email（SES）/ push，SSE 僅開發用。

## 前置

- `terraform` ≥ 1.5(本機已放 `~/bin/terraform`)
- AWS 認證已設定(`aws configure`,region ap-northeast-1)
- IAM user 需有建立 VPC/EC2/RDS/ElastiCache/DynamoDB/SES/CloudFront/APIGW/IAM/ECR/SSM/SecretsManager 的權限
- `python3` + `pip`（cleanup 模組 apply 時用 `local-exec` 打包 Lambda：sqlalchemy + pg8000）
- (選)遠端 state:先手動建 S3 bucket + DynamoDB table,再取消 `versions.tf` 的 backend 註解

## 部署步驟

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # 視需要調整規格/成本、ses_from_email
export PATH="$HOME/bin:$PATH"
terraform init
terraform plan      # 檢視
terraform apply     # 建立資源（會計費！）
```

apply 後取得輸出:

```bash
terraform output cloudfront_url        # 對外服務網址
terraform output gha_deploy_role_arn   # 填到 GitHub repo variable
terraform output ecr_repository_url
terraform output price_table_name      # DynamoDB price 表名
terraform output price_stream_arn      # DynamoDB Streams ARN（CDC 來源）
terraform output ses_identity_arn      # ses_from_email 留空時為空字串
```

SES 設定 `ses_from_email` 後：到該信箱點驗證信；SES sandbox 內收件人（訂閱者信箱）也要逐一驗證，
正式對外寄信需在 SES console 申請移出 sandbox。

## 首次部署 app(讓 EC2 真的跑起來)

EC2 開機時若 `IMAGE_URI` 還沒有對應映像,會跳過(無 app 可跑)。要讓服務上線,需先推一個映像:

**方式 A — 透過 CI/CD(建議)**
1. 到 GitHub repo → Settings → Secrets and variables → Actions → Variables,新增
   `AWS_DEPLOY_ROLE_ARN` = `terraform output -raw gha_deploy_role_arn`。
2. 手動觸發 `Deploy Price Tracker to AWS` workflow（`.github/workflows/deploy-price.yml`；
   infra apply 後可把 `push:` 區塊取消註解改為自動）。
3. workflow 會 build → 推 ECR → 更新 `/price/IMAGE_URI` → SSM run-command 部署到 EC2
   （依 tag `app=price` 目標;api + worker 兩組 ASG 都會重新部署）。

**方式 B — 手動推一次**
```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker build -t "$REPO:latest" "../"        # app 在上一層目錄（Amazon price tracking service/）
docker push "$REPO:latest"
aws ssm put-parameter --name /price/IMAGE_URI --type String --value "$REPO:latest" --overwrite
# 觸發部署（對 api + worker 兩組 ASG 實例執行 deploy 腳本）
aws ssm send-command --document-name AWS-RunShellScript \
  --targets Key=tag:app,Values=price \
  --parameters 'commands=["/usr/local/bin/deploy-app.sh"]'
```

## 驗證

```bash
URL=$(terraform output -raw cloudfront_url)
curl -s "$URL/health"                                   # {"status":"ok"}
curl -s "$URL/api/v1/config/meta"                       # price_backend=dynamodb, queue_backend=redis_streams
curl -s "$URL/api/v1/products"
curl -s "$URL/api/v1/cdc/status"                        # 各 shard checkpoint
curl -s -N "$URL/api/v1/stream?user_id=demo" &          # SSE 長連線（開發用）
```

## 資料清理

EventBridge Scheduler 每日觸發 cleanup Lambda（`modules/cleanup/src/cleanup.py`）：
- `notification_outbox`：`updated_at` 超過 `outbox_retention_days`（預設 30）物理刪除。
- `crawl_log`：`created_at` 超過 `crawl_log_retention_days`（預設 7）物理刪除。
- `price_reports`：`status IN ('confirmed','rejected','accepted')` 且超過同一保留窗物理刪除（`pending_verify` 不動）。
- **不清** DynamoDB `prices`（append-only 歷史）與 `price_aggregations`（讀路徑彙總）。

## 銷毀(避免持續計費)

```bash
terraform destroy
```

## 成本注意

主要持續成本:NAT Gateway、RDS、ElastiCache、ALB、CloudFront、EC2（api + worker 兩組 ASG）。DynamoDB 與 DynamoDB Streams 為 on-demand（用量計費）；SES 依寄信量計費。
`terraform.tfvars` 預設已走省錢設定(單一 NAT、single-AZ RDS、t4g.micro、各 ASG desired=1)。production 再升級 `db_multi_az=true`、多節點 Redis、`single_nat_gateway=false`、拉高 worker 容量、加 proxy pool。
