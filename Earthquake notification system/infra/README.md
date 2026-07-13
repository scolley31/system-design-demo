# Infra — AWS 部署 (Terraform)

Earthquake Notification System 的 production 雲端部署。架構:CloudFront → API Gateway (VPC Link) → 內部 ALB → EC2 ASG (Docker) → RDS PostgreSQL + ElastiCache Redis + DynamoDB。

app 原始碼在 `../`（`Earthquake notification system/`，FastAPI，port 8000，`/health`）。

## 資料層與角色

- **RDS PostgreSQL**：`alert_config`（使用者訂閱設定）+ `notification_outbox`（冪等關卡 + 狀態帳本）。
- **ElastiCache Redis**（單一組）：geo-index（cell→devices）+ per-channel Redis Streams（送達佇列）+ supersession cache。
- **DynamoDB `user_location`**：partition key `device_id`，PAY_PER_REQUEST；位置是 high-write（≈ 5.5K writes/s）。啟用 TTL 屬性 `ttl_epoch` 讓舊位置自動過期（app 端寫 `ttl_epoch` 即納管）。
- **兩組 ASG（同一映像，靠 `ROLE` env 區分）**：
  - `api`：對外 web / orchestrator，掛在 ALB 後（API Gateway VPC Link 只打這組）。
  - `worker`：消費 Redis Streams 送達 channel，不掛 ALB。
  - 註：目前 app 的 realtime hub 需單一 process、worker 在 API 程序內以 asyncio task 跑
    （見 `../app/main.py`、`../app/worker.py`、`Dockerfile` 註解），因此兩種 `ROLE` 目前
    跑同一份映像的完整程序。`ROLE` 已 plumbing 完成，日後 container entrypoint 可依 `ROLE`
    分流成「只跑 web」/「只跑獨立 worker fleet」（正式版 per-channel 各自擴縮）。

注入容器的環境變數（由 SSM + user_data）：`DATABASE_URL`、`REDIS_URL`、`LOCATION_TABLE`、`H3_RES`（預設 5）、`ROLE`、`BASE_URL` 及 Cognito 相關（auth 模組寫入）。此 app 無 S3 / 圖片儲存。

> 即時送達：`/api/v1/stream` 是長連線 SSE，原型讓它穿過 CloudFront/APIGW 方便 demo；
> 正式版即時送達走 APNs/FCM（server push），SSE 僅開發用。

## 前置

- `terraform` ≥ 1.5(本機已放 `~/bin/terraform`)
- AWS 認證已設定(`aws configure`,region ap-northeast-1)
- IAM user 需有建立 VPC/EC2/RDS/ElastiCache/DynamoDB/CloudFront/APIGW/IAM/ECR/SSM/SecretsManager 的權限
- (選)遠端 state:先手動建 S3 bucket + DynamoDB table,再取消 `versions.tf` 的 backend 註解

## 部署步驟

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # 視需要調整規格/成本
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
terraform output location_table_name   # DynamoDB user_location 表名
```

## 首次部署 app(讓 EC2 真的跑起來)

EC2 開機時若 `IMAGE_URI` 還沒有對應映像,會跳過(無 app 可跑)。要讓服務上線,需先推一個映像:

**方式 A — 透過 CI/CD(建議)**
1. 到 GitHub repo → Settings → Secrets and variables → Actions → Variables,新增
   `AWS_DEPLOY_ROLE_ARN` = `terraform output -raw gha_deploy_role_arn`。
2. push 到 `main` 或手動觸發 `Deploy to AWS` workflow。
3. workflow 會 build → 推 ECR → 更新 `/quake/IMAGE_URI` → SSM run-command 部署到 EC2
   （依 tag `app=quake` 目標;api + worker 兩組 ASG 都會重新部署）。

**方式 B — 手動推一次**
```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker build -t "$REPO:latest" "../"        # app 在上一層目錄（Earthquake notification system/）
docker push "$REPO:latest"
aws ssm put-parameter --name /quake/IMAGE_URI --type String --value "$REPO:latest" --overwrite
# 觸發部署（對 api + worker 兩組 ASG 實例執行 deploy 腳本）
aws ssm send-command --document-name AWS-RunShellScript \
  --targets Key=tag:app,Values=quake \
  --parameters 'commands=["/usr/local/bin/deploy-app.sh"]'
```

## 驗證

```bash
URL=$(terraform output -raw cloudfront_url)
curl -s "$URL/health"                                   # {"status":"ok"}
curl -s "$URL/api/v1/config/meta"
curl -s -N "$URL/api/v1/stream?device_id=demo" &        # SSE 長連線（開發用）
```

## 銷毀(避免持續計費)

```bash
terraform destroy
```

## 成本注意

主要持續成本:NAT Gateway、RDS、ElastiCache、ALB、CloudFront、EC2（api + worker 兩組 ASG）。DynamoDB 為 on-demand（用量計費）。
`terraform.tfvars` 預設已走省錢設定(單一 NAT、single-AZ RDS、t4g.micro、各 ASG desired=1)。production 再升級 `db_multi_az=true`、多節點 Redis、`single_nat_gateway=false`、拉高 worker 容量。
