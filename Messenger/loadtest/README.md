# Load Test（k6 WebSocket）

對 Messenger 做 WebSocket 壓力測試：每個 VU = 一個在線使用者 = 一條長連線，固定節奏在自己的群聊發言，同時收全群的訊息、檢查序號。

腳本 `chat_load.js`（單檔兩場景），包裝 `run.sh`。

## 目的

驗 NFR：

| NFR | 怎麼量 | threshold（chat） |
|---|---|---|
| **在線送達 p95 < 500ms** | 發送端把 `Date.now()` 塞進 text，每個收到 live `newMessage` 的成員算 `now - t` → `delivery_ms`（tag `kind=echo` 自己的回聲 / `kind=other` 別人發的） | `delivery_ms p(95)<500` |
| 送訊確認快 | `sendMessage` → `result` 的時間（含 owner 指派序號 + 寫 DynamoDB）→ `ack_ms` | `ack_ms p(95)<300` |
| **同 chat 順序一致、無缺無重** | `message_id` 是 per-chat 連續序號 1,2,3…；每個 VU 追 `lastSeen`：`== lastSeen+1` 前進、`> lastSeen+1` 記 `seq_gaps` 並 `fetchMessages(after lastSeen)` 補洞、`<= lastSeen` 記 `dup_or_out_of_order` | `seq_gaps count==0`、`dup_or_out_of_order count==0` |
| **送訊不失敗** | `result.status != SUCCESS`，或收尾時還沒回 `result` 的送訊（`error=NO_RESULT`）→ `send_failures` | `send_failures count==0` |

其他 metric：`messages_sent`、`messages_received`（live、非 catch-up 的 `newMessage`；正常應 = sent × 每群人數）、`connect_failures`（沒拿到 welcome / welcome 裡沒有自己的 chat）、`dup_after_recovery`（fetch 補到之後 live / gateway repair 又送一次的重複——client 依 id 去重即可，**不算錯**，只在 failover 時會出現）。

## 前置

```bash
brew install k6        # 需要 k6 ≥ v0.43（k6/timers）；本機驗證用 v0.54.0
cd Messenger && docker compose up --build      # nginx → ws://localhost:8030/ws（/gw1/ws、/gw2/ws 指定 gateway）
curl -s localhost:8030/health                  # {"status":"ok",...}
```

**k6 版本與 WebSocket 模組**：k6 ≥ v1.0 是 `k6/websockets`，v0.x 是 `k6/experimental/websockets`（API 相同）。腳本用 `require` 先試 `k6/websockets`、失敗再退 `k6/experimental/websockets`，兩種版本都不用改；`run.sh` 開頭會印出 k6 版本。setup 用的是阻塞式 `k6/ws`（兩版都有）。

setup 做的事（每次跑都會做）：
1. 產生 `RUN_ID`（或用 env 指定），以 `lt-<run>-admin` 連上，`createChat` 建 `CHATS` 個群。
2. chat `j` 的成員 = `{ lt-<run>-u<i> | i % CHATS == j }`，加上 admin；每群 ≤ 99 人 + admin = 100 上限（`VUS/CHATS > 99` 直接報錯）。
3. VU `n` 以 `lt-<run>-u<n-1>` 連線、等 welcome、等自己 chat 的 `catchupDone`（之後才開始順序檢查；`lastSeen` 從 welcome 的 `head` / catch-up 初始化），隨機錯開 0~INTERVAL 秒後每 `INTERVAL` 秒發一則；每秒送一次累積 `ack`；`DURATION` 到了停發，再等 3s 收尾後關線。

每次跑都是全新的 user / chat，所以不會收到上一輪的訊息。

## 場景

| SCENARIO | 做什麼 | threshold |
|---|---|---|
| `chat` | `VUS` 條長連線、`CHATS` 個群，每 VU 每 `INTERVAL` 秒一則 | `delivery_ms p95<500`、`ack_ms p95<300`、`seq_gaps==0`、`dup_or_out_of_order==0`、`send_failures==0` |
| `failover` | 同上的流量；跑的同時手動停一台 pub/sub | `delivery_ms p95<2000`、`ack_ms p95<2000`（max 不看——hard kill 會有 ~5s 尖峰）、`seq_gaps<10`（允許，靠 repair / fetch 補回）、`dup_or_out_of_order==0`、`send_failures==0` |

executor：`per-vu-iterations`、每 VU 1 次 iteration（= 一條連線撐完整個 DURATION）。

env：`BASE_URL`（預設 `ws://localhost:8030`）、`SCENARIO`（由 run.sh 第 1 參數帶）、`VUS`（200）、`CHATS`（20）、`INTERVAL`（2 秒）、`DURATION`（1m）、`SMOKE`（VUS 10、CHATS 2、DURATION 20s）、`RUN_ID`（預設隨機）。

壓力算法：送訊率 ≈ `VUS / INTERVAL` 則/s；server 推播（fan-out）≈ 送訊率 × `VUS / CHATS`。預設 200 VU / 20 群 / 2s → 100 則/s、每則推給 10 人 = 1,000 推播/s。

## 本機冒煙

```bash
cd Messenger/loadtest
SMOKE=1 ./run.sh chat
```
確認 console 印 `setup: run=… vus=10 chats=2/2 (~5 人/群)`、所有 threshold 打勾、`messages_received` = `messages_sent` × 5。

參考（本機 docker compose，MacBook，v0.54.0）：

| 參數 | sent | received | delivery_ms p95 | ack_ms p95 | gaps / dup / failures |
|---|---|---|---|---|---|
| `SMOKE=1` | 100 | 500 | 25ms | 26ms | 0 / 0 / 0 |
| `VUS=60 CHATS=6 DURATION=30s` | 896 | 8,960 | 26ms | 26ms | 0 / 0 / 0 |

（本機單機 + DynamoDB Local，絕對數字不代表 prod。）

## 正式壓測

```bash
./run.sh chat                                        # 200 VU / 20 群 / 2s / 1m
VUS=1000 CHATS=100 DURATION=3m ./run.sh chat         # 加連線數
VUS=500 CHATS=5 INTERVAL=1 ./run.sh chat             # 大群（每群 100 人）→ fan-out 壓力
INTERVAL=0.2 VUS=200 ./run.sh chat                   # 加送訊率（1,000 則/s）
./run.sh chat --summary-export=chat.json             # 第 2 參數起原樣給 k6
```
p95 超過 500ms、`send_failures` 開始出現（`PUBLISH_DEADLINE_S` 8s 逾時）或 gateway 因慢 client 斷線（`SEND_QUEUE_MAX`，看 k6 console 的「連線被提前關閉」）的那一階 = **knee**。單機 k6 開幾千條連線前先 `ulimit -n 65535`。

## failover 壓測

```bash
cd Messenger/loadtest
DURATION=2m ./run.sh failover          # terminal A
```
跑起來 ~20s 後在 terminal B（`Messenger/` 底下）二選一：
```bash
docker compose kill pubsub-1           # hard：SIGKILL，等 etcd lease TTL（5s）過期 shard 才轉給 pubsub-2
docker compose stop pubsub-1           # graceful：SIGTERM → revoke lease → shard 立刻轉移
```
再等 ~20s：
```bash
docker compose start pubsub-1          # 重新加入，rebalance 拿回一半 shard（又一次搬家）
```
預期：
- `send_failures == 0`：gateway 用同一個 `client_msg_id` 重試到新 owner（owner 端去重），在 `PUBLISH_DEADLINE_S`（8s）內都算成功。
- `dup_or_out_of_order == 0`：新 owner 從 DynamoDB 讀 head 接續序號，不會重複指派。
- `seq_gaps` 少量：搬家空窗內舊 owner 已送出但本 gateway 沒收到的訊息，gateway `_repair` 用 subscribed 回的 head 從 DB 補送；client 先看到後面的 id 就自己 `fetchMessages` 補。補回後再收到的重複記在 `dup_after_recovery`，不算錯。
- `delivery_ms` / `ack_ms` 在 kill 那幾秒尖峰（hard ≈ lease TTL + 重試 backoff），p95 仍應 < 2s。

## 雲端

```bash
export BASE_URL=wss://<cloudfront>
./run.sh chat
```
- **WAF rate limit 要調**：單一壓測 IP 連幾百條 WebSocket + 每秒幾百個 frame，會被 per-IP rate limit / 連線數限制擋。壓測前把 WAF / 節流拉高（壓完還原）。
- **k6 機器開在同 region**（c-class EC2），否則跨太平洋 RTT 直接灌進 `delivery_ms` / `ack_ms`：
  ```bash
  sudo dnf install -y https://dl.k6.io/rpm/repo.rpm && sudo dnf install -y k6
  ```
- `delivery_ms` 用的是**發送端 VU 的 `Date.now()` 與接收端 VU 的 `Date.now()` 相減**——兩者在同一台 k6 機器上所以時鐘一致；k6 分散到多台跑（k6 cloud / 多機）時這個值會混入機器間時鐘差，要嘛單機跑，要嘛只看 `kind=echo`（發送與接收同一個 VU）。
- CloudFront / ALB 的 idle timeout 要大於 `INTERVAL`（預設 2s 發一次，遠低於 60s，通常沒問題）。

## 觀察什麼

- **k6 summary**：`delivery_ms`（`{kind:other}` / `{kind:echo}`）、`ack_ms` p50/p95/max、`seq_gaps`、`dup_or_out_of_order`、`send_failures`、`messages_received` 是否 = sent × 每群人數。
- **gateway**：`curl -s localhost:8030/gw1/api/cluster | jq`（`/gw2/` 同）——`connections`、`users`、`topics_subscribed`、`stats.delivered`（推播數）、`stats.publish_retry` / `publish_deadline`（owner 掛掉時重試 / 逾時）、`stats.repaired`（搬家後補送）、`stats.catchup_msgs` / `fetch_msgs`、`stats.shard_changes`。
- **pub/sub**：`curl -s localhost:8041/debug/state | jq`（pubsub-2 是 `8042`）——`owned` / `draining` shard、`lease_healthy`、`fenced`、`chats_loaded`（每個 chat 目前的 head 序號）、訂閱的 gateway。
- **DynamoDB Local**（`localhost:8101`，表名 prefix `messenger-local-`）：
  ```bash
  aws dynamodb list-tables --endpoint-url http://localhost:8101 --region us-east-1
  aws dynamodb scan --table-name messenger-local-message --select COUNT --endpoint-url http://localhost:8101 --region us-east-1
  ```
  訊息總數應 = 歷次 `messages_sent` 加總（+ 建群等系統訊息）。
- **docker stats**：gateway / pubsub / dynamodb 的 CPU、記憶體——通常 DynamoDB Local（JVM）或單一 pubsub owner 先吃滿。

## 善後

`lt-*` 的 user / chat / message 都會留在 DynamoDB。DynamoDB Local 是 `-inMemory`，重啟就清空：
```bash
cd Messenger
docker compose restart dynamodb && docker compose up init-tables   # 只清資料、重建表（gateway / pubsub 的 in-memory 狀態也建議一起 restart）
docker compose down                                                # 或整套關掉
```
雲端 DynamoDB：用 `RUN_ID` 前綴（`lt-<run>-`）篩掉，或壓測專用環境整個 `terraform destroy`。壓完記得把 WAF 設定還原。
