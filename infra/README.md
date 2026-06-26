# Infra — AWS 部署 (Terraform)

QR Code Generator 的 production 雲端部署。架構:CloudFront → API Gateway (VPC Link) → 內部 ALB → EC2 ASG (Docker) → RDS PostgreSQL + ElastiCache Redis;S3 存 QR 圖、CloudFront 服務。

詳見 `../QR Code Generator/DESIGN.md` 的 production 架構。

## 前置

- `terraform` ≥ 1.5(本機已放 `~/bin/terraform`)
- AWS 認證已設定(`aws configure`,region ap-northeast-1)
- IAM user 需有建立 VPC/EC2/RDS/ElastiCache/S3/CloudFront/APIGW/IAM/ECR/SSM/SecretsManager 的權限
- (選)遠端 state:先手動建 S3 bucket + DynamoDB table,再取消 `versions.tf` 的 backend 註解

## 部署步驟

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # 視需要調整規格/成本
export PATH="$HOME/bin:$PATH"
terraform init
terraform plan      # 檢視(約 58 個資源)
terraform apply     # 建立資源（會計費！）
```

apply 後取得輸出:

```bash
terraform output cloudfront_url        # 對外服務網址
terraform output gha_deploy_role_arn   # 填到 GitHub repo variable
terraform output ecr_repository_url
```

## 首次部署 app(讓 EC2 真的跑起來)

EC2 開機時若 `IMAGE_URI` 還沒有對應映像,會跳過(無 app 可跑)。要讓服務上線,需先推一個映像:

**方式 A — 透過 CI/CD(建議)**
1. 到 GitHub repo → Settings → Secrets and variables → Actions → Variables,新增
   `AWS_DEPLOY_ROLE_ARN` = `terraform output -raw gha_deploy_role_arn`。
2. push 到 `main`(改到 `QR Code Generator/**`)或手動觸發 `Deploy to AWS` workflow。
3. workflow 會 build → 推 ECR → 更新 `/qrcode/IMAGE_URI` → SSM run-command 部署到 EC2。

**方式 B — 手動推一次**
```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker build -t "$REPO:latest" "../QR Code Generator"
docker push "$REPO:latest"
aws ssm put-parameter --name /qrcode/IMAGE_URI --type String --value "$REPO:latest" --overwrite
# 觸發部署（對 ASG 實例執行 deploy 腳本）
aws ssm send-command --document-name AWS-RunShellScript \
  --targets Key=tag:app,Values=qrcode \
  --parameters 'commands=["/usr/local/bin/deploy-app.sh"]'
```

## 驗證

```bash
URL=$(terraform output -raw cloudfront_url)
curl -s "$URL/health"                       # {"status":"ok"}
curl -s -X POST "$URL/api/v1/qr/create" -H 'Content-Type: application/json' -d '{"url":"https://example.com"}'
curl -s -o /dev/null -w "%{http_code}\n" "$URL/r/<token>"   # 302
```

## 銷毀(避免持續計費)

```bash
terraform destroy
```

## 成本注意

主要持續成本:NAT Gateway、RDS、ElastiCache、ALB、CloudFront、EC2。
`terraform.tfvars` 預設已走省錢設定(單一 NAT、single-AZ RDS、t4g.micro)。production 再升級 `db_multi_az=true`、多節點 Redis、`single_nat_gateway=false`。
