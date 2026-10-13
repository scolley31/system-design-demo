# Infra — AWS 部署 (Terraform)

Messenger 的 production 雲端部署。架構:CloudFront (WAF) → 對外 ALB (origin-verify header) → gateway ASG ⇄ pubsub ASG (Docker) + etcd (EC2) + DynamoDB × 5。

app 原始碼在 `../`（`Messenger/`，FastAPI + WebSocket，同一個 image 靠 `ROLE` 分 gateway:8031 / pubsub:8041，`/health`）。

```
                    ┌─────────────────────── VPC (ap-northeast-1) ───────────────────────────┐
                    │  public subnets              private subnets                          │
 Client ──wss/https─┼─► ALB :80 ──(X-Origin-Verify)──► gateway ASG :8031 ──/internal/ws──► pubsub ASG :8041
   │                │   SG: 只放 CloudFront          (stateless, 1–4)      ◄── fan-out ───  (shard owner, 2–4)
   ▼                │   prefix list                       │   watch                          │ lease / claim
 CloudFront ────────┘   沒帶 header → 403                  └──────────► etcd :2379 ◄──────────┘
 (+ WAF per-IP)     │                                              (t4g.micro, 固定 IP, demo SPOF)
                    │                                                                         │
                    │        gateway / pubsub ──────────────► DynamoDB（chat / membership / message /
                    │                                          chat_state / client_msg）
                    └─────────────────────────────────────────────────────────────────────────┘
```

## 資料層與角色

- **DynamoDB（5 張表，PAY_PER_REQUEST，表名 `messenger-<name>` → `DYNAMO_TABLE_PREFIX=messenger-`）**，key 與 `../app/store.py` 的 `TABLES` 一致：
  - `chat`（`chat_id`）：群組 metadata；建群最後寫入的 commit marker。
  - `membership`（`chat_id` + `user_id`）：成員列表（fan-out 對象）。
  - `message`（`chat_id` + `message_id` N）：訊息本體，`message_id` 是 owner 指派的 per-chat 連續序號。**開 PITR**（唯一不可重建的資料）。
  - `chat_state`（`user_id` + `chat_id`）：每人每 chat 的已送達 / 已讀位置（重連補收起點）。
  - `client_msg`（`chat_id` + `client_msg_id`）：client 重送去重；**DynamoDB 原生 TTL `expires_at`** 自動清。
  - app 沒有 `CreateTable` 權限：表一律由 Terraform 建（`ensure_tables` 只在 DynamoDB Local 跑）。
- **etcd（單台 EC2，`quay.io/coreos/etcd:v3.5.17`）**：`nodes/{id}`、`shards/{0000..}` 兩組 key，都掛在 pubsub 的 lease 上（`../app/registry.py`）。固定私有 IP = 第一個私有子網的 `.50`，寫進 SSM `/messenger/ETCD_URL`。
- **兩組 ASG（同一映像，靠 `ROLE` env 區分）**：
  - `gateway`：對外 WebSocket / HTTP 入口，掛在 ALB target group（8031），stateless，可任意擴縮。
  - `pubsub`：chat shard owner（per-chat 序號 + 持久化 + fan-out），不掛 ALB；gateway 從 etcd 讀到 `ADVERTISE_WS=ws://<private-ip>:8041/internal/ws` 後直連。
  - 容器用 `--network host`，`NODE_ID = gw-<instance-id>` / `pubsub-<instance-id>`。

注入容器的環境變數（SSM `/messenger/*` + user_data）：`ROLE`、`NODE_ID`、`PORT`、`ADVERTISE_WS`、`ADVERTISE_HTTP`、`ETCD_URL`、`NUM_SHARDS`（64）、`LEASE_TTL_S`（5）、`DYNAMO_TABLE_PREFIX`、`MAX_PARTICIPANTS`（100）、`ALLOW_DEBUG`（0）、`AUTH_ENABLED`、`COGNITO_REGION` / `COGNITO_USER_POOL_ID` / `COGNITO_CLIENT_ID`、`AWS_DEFAULT_REGION`。正式版**不設** `DYNAMO_ENDPOINT`。

### 已移除（相對 price 範本）

- **RDS / ElastiCache / Secrets Manager**：資料全在 DynamoDB，協調在 etcd，沒有密碼要存。
- **cleanup Lambda + 排程**：訊息永久保留；唯一會過期的 `client_msg` 用 DynamoDB 原生 TTL（免費，刪除非即時，約 48 小時內）。
- **API Gateway**（見下節）、synthetic canary。

## 為什麼沒有 API Gateway

- **HTTP API**（price 用的 VPC Link 架構）不能代理 WebSocket Upgrade → `/ws` 連不上。
- **WebSocket API** 也不採用：
  1. 每則訊息 + 連線分鐘計價，聊天量大時遠貴於 ALB；
  2. 單一連線最長 2 小時就被切斷；
  3. 推播要對每條連線各打一次 `@connections` POST → 一則百人群訊 = 100 次 RPC，正是 PDF 否決的 N-RPC fan-out（我們是 pubsub 一次 publish、gateway 本地 fan-out）。
- 改為 **CloudFront（原生支援 WebSocket）→ 對外 ALB**：
  - ALB SG 只放 AWS 代管 prefix list `com.amazonaws.global.cloudfront.origin-facing`；
  - 該 prefix list 涵蓋所有人的 CloudFront，所以 listener 預設回 **403**，只有帶正確 `X-Origin-Verify`（Terraform `random_password`，CloudFront origin custom header 注入）才 forward 到 gateway；
  - ALB `idle_timeout = 300`，app 端 `--ws-ping-interval 20` 保活；
  - WAF（per-IP rate limit）掛在 CloudFront，只計 HTTP request 與 `/ws` 握手，連線內 frame 不計。
- **production 待補**：ALB 掛 ACM 憑證 + CloudFront origin 改 `https-only`（目前 CloudFront → ALB 是明文 HTTP）；或改用 **CloudFront VPC origin** 直連 internal ALB（不需 public ALB，也不需 header 密鑰）。

## Auth

Cognito user pool + SPA client 照建，COGNITO_* 寫入 SSM。沒有 API Gateway authorizer：瀏覽器 WebSocket 不能自訂 `Authorization` header，client 以 `?token=<id token>` 連 `/ws`，由 gateway 驗 JWT（`../app/auth.py`）。

`auth_enabled` 預設 **false**（`scripts/smoke_ws.py` 以 `?user_id=` 連線）；設 true 後 `/ws` 必須帶 token。開啟時記得 access log 要遮掉 query string。

## etcd 單點（demo SPOF）

- 只有 1 台（`etcd_node_count` 的 validation 只允許 1）。它掛掉 → pubsub 無法 keepalive → `LEASE_TTL_S` 後所有 shard 失去 owner → publish 一律 `UNAVAILABLE`，直到 etcd 回來。資料在 root gp3，容器 `--restart always`，重開機不丟 key。
- 不放 ASG：etcd 成員身分綁固定 IP + data dir，換機等於新成員。tag 是 `app=messenger-etcd`，CI/CD 部署永遠打不到它。
- **production**：3 台跨 3 AZ（各自固定 IP，`--initial-cluster` 三台靜態成員，Raft 容忍 1 台故障），`ETCD_URL` 填多個 endpoint；
  或改用 **DynamoDB lock table** 做 lease（conditional put + 過期時間欄位 + 心跳更新），免維運 etcd，但 `registry.py` 要重寫、且沒有 watch（gateway 要改輪詢）。
- 告警：`messenger-etcd-status-check`（EC2 StatusCheckFailed）。

## 前置

- `terraform` ≥ 1.5(本機已放 `~/bin/terraform`)
- AWS 認證已設定(`aws configure`,region ap-northeast-1)
- IAM user 需有建立 VPC/EC2/ELB/DynamoDB/CloudFront/WAF/Cognito/IAM/ECR/SSM/CloudWatch/SNS 的權限
- (選)遠端 state:先手動建 S3 bucket + DynamoDB table,再取消 `versions.tf` 的 backend 註解

## 部署步驟

```bash
cd Messenger/infra
cp terraform.tfvars.example terraform.tfvars   # 視需要調整規格/成本、alert_email
export PATH="$HOME/bin:$PATH"
terraform init
terraform plan      # 檢視
terraform apply     # 建立資源（會計費！CloudFront 建立約 5–10 分鐘）
```

apply 後取得輸出:

```bash
terraform output cloudfront_url        # 對外服務網址
terraform output ws_url                # wss://<cloudfront>/ws
terraform output gha_deploy_role_arn   # 填到 GitHub repo variable
terraform output ecr_repository_url
terraform output dynamo_table_names
terraform output etcd_instance_id
```

## 首次部署 app(讓 EC2 真的跑起來)

EC2 開機時若 `IMAGE_URI` 還沒有對應映像,會跳過(無 app 可跑)。要讓服務上線,需先推一個映像:

**方式 A — 透過 CI/CD(建議)**
1. 到 GitHub repo → Settings → Secrets and variables → Actions → Variables,新增
   `AWS_DEPLOY_ROLE_ARN` = `terraform output -raw gha_deploy_role_arn`。
   （同帳號已有 GitHub OIDC provider 時，cicd 模組設 `create_oidc_provider=false` 並給 `existing_oidc_provider_arn`。）
2. 手動觸發 `Deploy Messenger to AWS` workflow（`.github/workflows/deploy-messenger.yml`；
   infra apply 後可把 `push:` 區塊取消註解改為自動）。
3. workflow 會 build（linux/arm64）→ 推 ECR → 更新 `/messenger/IMAGE_URI` → SSM run-command **分兩段**部署：
   - `tag:role=pubsub` 一次一台（`--max-concurrency 1`）：`docker stop -t 10` 送 SIGTERM → pubsub 先 fence 再
     revoke etcd lease → 另一台立刻接手 shard（不用等 TTL）；
   - 全部成功後 `tag:role=gateway` 一次一半（`--max-concurrency 50%`）：client 斷線後自動重連到另一半。

**方式 B — 手動推一次**
```bash
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker buildx build --platform linux/arm64 -t "$REPO:latest" --push ../    # app 在上一層（Messenger/）
aws ssm put-parameter --name /messenger/IMAGE_URI --type String --value "$REPO:latest" --overwrite
# 先 pubsub（一次一台）再 gateway
aws ssm send-command --document-name AWS-RunShellScript --max-concurrency 1 \
  --targets Key=tag:app,Values=messenger Key=tag:role,Values=pubsub \
  --parameters 'commands=["/usr/local/bin/deploy-app.sh"]'
aws ssm send-command --document-name AWS-RunShellScript --max-concurrency 50% \
  --targets Key=tag:app,Values=messenger Key=tag:role,Values=gateway \
  --parameters 'commands=["/usr/local/bin/deploy-app.sh"]'
```

## 驗證

```bash
URL=$(terraform output -raw cloudfront_url)
WS=$(terraform output -raw ws_url)
curl -s "$URL/health"                                   # {"status":"ok","role":"gateway",...}
curl -s "$URL/api/config/meta"                          # mode=cluster, store_backend=dynamodb, registry_backend=etcd
curl -s "$URL/api/cluster"                              # 各 pubsub 節點與 shard 分配（64 個 shard 分給 2 台）
curl -s -o /dev/null -w '%{http_code}\n' "http://$(terraform output -raw alb_dns_name)/health"   # 403（繞過 CloudFront）
python ../scripts/smoke_ws.py "$WS"                     # 建群 → 連發 → 連號 / 補收 / 去重（auth_enabled=false 時）
```

failover 觀察：用 Session Manager 進一台 pubsub 執行 `docker stop messenger`（graceful，shard 立刻轉移）或
`docker kill messenger`（hard，等 `LEASE_TTL_S` 過期），再看 `/api/cluster` 與 dashboard 的 `ShardTakeover`。
（`/api/debug/kill` 需 `allow_debug=true`，對外環境預設關閉。）

## Monitoring

- SNS email（`alert_email`，建立後需點確認信）。
- 告警：ALB `UnHealthyHostCount` / `HTTPCode_ELB_5XX_Count`、gateway / pubsub ASG CPU、etcd `StatusCheckFailed`、
  DynamoDB `message` 表 `WriteThrottleEvents` / `ReadThrottleEvents`、CloudFront `5xxErrorRate`、app `IdConflict`（>0 即告警）、`PublishFailed`。
- Log metric filter（log group `/messenger/app`，namespace `messenger/app`）：`shard_takeover`、`id_conflict`、`publish_failed`。
  註：目前 app 只把 `publish_failed` 放在回 client 的 frame 裡、沒寫 log，`PublishFailed` 要等 app 補 log 才會有值。
- Dashboard `messenger-overview`：ALB 連線 / 5xx / healthy、app 事件、兩組 ASG CPU、message 表寫入 / throttle。

## 銷毀(避免持續計費)

```bash
terraform destroy
```

`message` 表開了 PITR，但 destroy 會連表一起刪（demo 未設 `deletion_protection_enabled`）；要留資料先做 on-demand backup。

## 成本注意

主要持續成本:NAT Gateway、對外 ALB、EC2（gateway ×2 + pubsub ×2 t4g.small + etcd t4g.micro）、CloudFront + WAF（web ACL 月費 + 每百萬 request）。
DynamoDB 為 on-demand（用量計費），`message` 表 PITR 依表大小計費；CloudFront 的 WebSocket 依連線時間內傳輸量計費，沒有 API Gateway 的每則訊息費。
`terraform.tfvars` 預設已走省錢設定（單一 NAT、單台 etcd）。production 再升級 `single_nat_gateway=false`、etcd 3 台跨 AZ、ALB 加 ACM + https origin、`num_shards` 拉高。
