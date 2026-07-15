# Load Test（k6）

對 QR Code Generator 做壓力測試:找 knee、驗證附錄 J「DB scan 寫入先飽和」。

承 DESIGN 附錄 J。腳本 `qr_load.js`(單檔多場景),包裝 `run.sh`。

## 場景

| SCENARIO | 內容 | executor |
|---|---|---|
| `redirect` | 純壓 `GET /r/{token}`(熱路徑),階梯加壓找 knee | ramping-arrival-rate |
| `create` | 壓 `POST /api/v1/qr/create`(寫入,需 JWT) | constant-arrival-rate |
| `mixed` | 95% redirect + 5% create(read-heavy 真實比例) | ramping-arrival-rate |

**關鍵**:`maxRedirects=0` → 量到 **302 本身**,不追到目標站(否則測到 example.com)。

**Thresholds(pass/fail)**:redirect `p95<100ms`、`p99<200ms`;整體 `error rate<1%`、`checks>99%`。

## 本機冒煙（免雲端、免 JWT）

本機 `uvicorn`(AUTH 關閉)跑著時:
```bash
SMOKE=1 BASE_URL=http://localhost:8000 ./run.sh redirect
```
確認 setup 建得了 token、redirect 量到 302、thresholds 生效。(本機單行程 + SQLite,絕對數字不代表 prod。)

## 雲端完整鏈路壓測

### 1) 起雲端（含壓測前置）
壓測前把 WAF/節流調高,否則**單一壓測 IP 會被 WAF per-IP 擋(全域 2000/5分)**。在 `infra/terraform.tfvars`:
```hcl
waf_rate_limit_global = 100000000
waf_rate_limit_api    = 100000000
apigw_throttle_rate   = 100000
apigw_throttle_burst  = 200000
```
然後:
```bash
cd infra && export PATH="$HOME/bin:$PATH"
terraform apply                 # 起整套(計費)
# 推映像（CI 推一個 commit,或手動,見 infra/README.md）
terraform output cloudfront_url
```
確認 `curl $CF/health` → 200、ASG healthy。

### 2) 取 JWT（create 場景需要）
開 CloudFront URL → Cognito 登入 → devtools console:`sessionStorage.id_token`,或用 CLI:
```bash
aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
  --client-id <client_id> --auth-parameters USERNAME=<u>,PASSWORD=<p> \
  --query 'AuthenticationResult.IdToken' --output text
```
> CLI 法需 app client 開 `ALLOW_USER_PASSWORD_AUTH`(預設只開 SRP);最簡單是從 Hosted UI 登入後複製 id_token。
```bash
export JWT=<id_token>
```

### 3) 開壓測機（建議,避免跨太平洋灌水 p95）
在 **ap-northeast-1** 開一台 c-class EC2,裝 k6:
```bash
# Amazon Linux 2023
sudo dnf install -y https://dl.k6.io/rpm/repo.rpm && sudo dnf install -y k6
# 或直接下載 k6 binary
```
把 `loadtest/` 複製上去。

### 4) 跑
```bash
export BASE_URL=https://<cloudfront>
export JWT=<id_token>
./run.sh redirect     # 階梯加壓找 knee
./run.sh create       # 寫入路徑
./run.sh mixed        # 95:5
```

## 觀察什麼

- **k6 summary**:每階 RPS、`http_req_duration` p50/p95/p99、`http_req_failed`。p95 超過 100ms 或 error 上升的那一階 = **knee**。
- **CloudWatch**(用我們建的 `qrcode-overview` dashboard + 附錄 H alarms):
  - **RDS** `DatabaseConnections` / `WriteLatency` / CPU —— 高 redirect QPS 下每次掃描一筆 INSERT,**預期 RDS 寫入先飽和** → 驗證附錄 J「DB 寫入先崩」。
  - **EC2** CPU、**ALB** TargetResponseTime/5xx、**Redis** CPU。
- 記錄 knee 出現時「哪個資源先到極限」,對照附錄 J 崩潰順序。

## 善後（停止計費）
```bash
# 還原 terraform.tfvars 的 WAF/節流 → terraform apply（或直接 destroy）
cd infra && terraform destroy
# 關掉壓測機 EC2
```
