# Messenger

依「Design Messenger」PDF 實作的可跑原型：建立群聊（≤100 人）→ 透過 WebSocket 即時收發訊息 → 離線回來補收；同一個 chat 所有人看到**完全一致的順序**、訊息**不遺失**。

設計依據見 [`DESIGN.md`](./DESIGN.md)（三大架構決策 + 深入探討 + 附錄，含 failover / fencing 時序）。本程式是依該設計實作、**真的多 process** 的原型：`docker compose up` 起 2 台 gateway + 2 台 pub/sub shard owner + etcd + DynamoDB Local，可以在 demo 頁上 kill 掉一台 pub/sub，看 shard 轉移、訊息照樣連號無缺無重。

## 技術棧

FastAPI + **WebSocket**（uvicorn / websockets）+ **etcd**（service discovery，httpx 打 v3 JSON API，不裝 grpcio）+ **DynamoDB**（DynamoDB Local / AWS；dev 模式 SQLite）+ nginx。Python 3.10+。

三大技術核心（見 DESIGN）：**WebSocket + stateless gateway** · **以 chat 分片的 pub/sub + etcd（shard → owner）** · **owner 指派 per-chat 連續 message_id + 先持久化再 ack / fan-out**。

```
client ──WS──► nginx / ALB ──► gateway-1 ─┐                         ┌─► pubsub-1（shard 0,2,5…）─┐
client ──WS──►                 gateway-2 ─┼── 內部 WS（subscribe /   ├─► pubsub-2（shard 1,3,4…）─┼─► DynamoDB
                                          │   publish / deliver）    │                             │
                                          └────── watch ── etcd ◄────┴── lease + CAS claim ────────┘
```

## 快速開始

### A. 本地部署（建議；= 正式版 code path）

```bash
cd Messenger
docker compose up --build        # etcd + DynamoDB Local + init-tables + pubsub-1/2 + gateway-1/2 + nginx
open http://localhost:8030/      # 開兩個分頁：user_id 不同即可（例如 alice / bob），gateway 可選 auto / gw-1 / gw-2
```

| 服務 | port |
|---|---|
| nginx（入口 + demo 頁；`/ws` least_conn、`/gw1/` `/gw2/` 指定 gateway） | **8030** |
| gateway-1 / gateway-2 | 8031 / 8032 |
| pubsub-1 / pubsub-2（`/debug/state`） | 8041 / 8042 |
| etcd | 23790 |
| DynamoDB Local | 8101 |

port 避開同 repo 其他 demo（8000/8010/8020、5432-5434、6379-6381）。

### B. 單 process dev 模式（改 code 用；零外部依賴）

```bash
cd Messenger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8030 --timeout-graceful-shutdown 5
```

`ROLE=all`：gateway + pub/sub 同一個 process（LocalLink 直接呼叫）、SQLite、記憶體 registry 持有全部 shard。

> 清 SQLite 前先停 uvicorn（WAL 模式下刪掉 `-wal` 檔會損毀 DB）。`--timeout-graceful-shutdown`：有 WebSocket 長連線時 SIGTERM 才不會卡住。

## Demo 頁（`app/static/index.html`，單檔零建置）

- **連線**：user_id（每個分頁一個，存 sessionStorage）、選 gateway（auto / gw-1 / gw-2）、顯示實際連到哪台。
- **建立群聊**：名稱 + 參與者（逗號分隔），超過 100 人前端先警告、server 拒絕。
- **訊息**：每則顯示 `#message_id`、發送者、由哪台 owner 定序；送出中 / 已持久化 / 失敗（可用**同一個 client_msg_id 重送**）；「連發 20 則」；成員 ADD / REMOVE（產生系統訊息）。
- **順序與延遲**：連號狀態、跳號補拉 / 亂序重排 / 重複丟棄 / 離線補收 / 搬家補送次數；`ack_ms`（送出 → 已持久化）、`echo_ms`（送出 → fan-out 回到自己）的 p50 / p95。
- **Cluster**：shard grid（依 owner 上色，白框 = 目前聊天室的 shard）、各 pub/sub 節點與 link 狀態、**graceful 下線 / hard kill 按鈕**、shard 搬家紀錄。
- **Protocol log**：最近 60 個收發 frame。

> 推薦玩法：兩個分頁分連 gw-1 / gw-2 → 建群互傳 → 按「連發 20 則」的同時對**白框 shard 的 owner** 按 hard kill → 看 shard grid 約 5 秒後變色、訊息照樣 #1…#N 連號。之後 `docker compose start pubsub-1` 讓它重新加入並 rebalance。

## WebSocket API

連線：`ws://HOST/ws?user_id=alice`（正式版 `?token=<Cognito id token>`）→ `welcome` → 離線補收。

| client → server | 回應 |
|---|---|
| `createChat {req_id, name, participants[]}` | `result {status, chat_id}` |
| `sendMessage {req_id, chat_id, client_msg_id, text, client_ts}` | `result {status, message_id, dup}` |
| `modifyChatParticipants {req_id, chat_id, user_id, operation: ADD\|REMOVE}` | `result {status, message_id}` |
| `fetchMessages {req_id, chat_id, after_id, limit}` | `messages {items[], has_more}` |
| `listChats {req_id}` | `chats {chats[]}` |
| `ack {chat_id, message_id}` | — |
| `ping {t}` | `pong` |

server 推送：`newMessage`、`chatAdded`、`chatRemoved`、`catchupDone`、`error`。完整欄位與內部 gateway ↔ pub/sub 協定見 DESIGN「API」。

HTTP：`GET /health`、`GET /api/config/meta`、`GET /api/cluster`、`POST /api/debug/kill {node_id, mode: graceful|hard}`（`ALLOW_DEBUG=1`）；pub/sub：`GET /debug/state`。

## 與設計決策的對應

| 決策 | 實作位置 |
|------|---------|
| 角色組裝（all / gateway / pubsub） | `app/main.py` |
| client 協定、topic 引用計數訂閱、先訂閱再補收、owner 重試、搬家補送、ack 合併 | `app/gateway.py` |
| shard owner：per-chat lock 內指派連續 message_id、先持久化再 fan-out、去重、fencing、rebalance | `app/pubsub.py` |
| etcd：lease / keepalive / CAS claim / watch → ShardMap | `app/registry.py`、`app/shardmap.py` |
| 內部 WebSocket（重連、req_id 對應）/ 同 process LocalLink | `app/links.py`、`app/routes_pubsub.py` |
| DynamoDB / SQLite（TransactWrite fencing + 去重、createChat commit-marker） | `app/store.py`、`app/tools/ensure_tables.py` |
| client 排序 / 跳號補拉 / 去重 | `app/static/index.html`（`onNewMessage`） |
| WS 反向代理（Upgrade、動態 DNS） | `nginx/nginx.conf` |

## 原型 vs Production（env-gated）

| 面向 | dev | 本地部署 / 正式版 | env |
|---|---|---|---|
| 角色 | `ROLE=all` | `ROLE=gateway` / `pubsub` | `ROLE` |
| Registry | 記憶體 | etcd | `ETCD_URL` |
| 儲存 | SQLite | DynamoDB Local / DynamoDB | `DYNAMO_TABLE_PREFIX`、`DYNAMO_ENDPOINT` |
| shard 數 | 16 | 16 / 64 / 4096 | `NUM_SHARDS` |
| Auth | `?user_id` | Cognito | `AUTH_ENABLED` |
| debug kill | 開 | compose 開、AWS 關 | `ALLOW_DEBUG` |

其他：`LEASE_TTL_S`（5）、`MAX_PARTICIPANTS`（100）、`CATCHUP_LIMIT`（200）、`ACK_FLUSH_MS`（500）、`PUBLISH_DEADLINE_S`（8）。

## 驗證

`scripts/smoke_ws.py`（dev 與 compose 共用）：建群 → chatAdded → 連發 N 則（收端 id 必須 1..N 連號、無重複）→ 離線補收 → 重送同 client_msg_id 回 dup → ADD 成員系統訊息 → 101 人建群被拒。

```bash
python scripts/smoke_ws.py ws://localhost:8030/ws                                   # dev
python scripts/smoke_ws.py ws://localhost:8030/gw1/ws ws://localhost:8030/gw2/ws    # 跨 gateway
python scripts/smoke_ws.py ws://localhost:8030/gw1/ws ws://localhost:8030/gw2/ws \
       --burst 60 --interval 0.1 --kill-owner hard                                  # 送到 1/3 時殺掉這個 chat 的 owner
```

已實測（compose，本機）：

| 情境 | 結果 |
|---|---|
| 跨 gateway 連發 50 則 | 全部 SUCCESS、1..50 連號、ack p50 5ms |
| 兩個分頁同時各連發 20 則到同一個 chat | 雙方 #1–#44 順序完全一致、零重排零重複 |
| hard kill chat owner（連發中） | ~5.3s 後存活節點接手 16 shard；60 則全 SUCCESS、連號無缺無重；最慢一則 4.3s |
| graceful 下線 chat owner（連發中） | 60 則全 SUCCESS、最慢一則 42ms |
| owner 重新加入 | 自動 rebalance 回 8/8 |
| 離線 5 則後重連 | 收到 #21–#25（catchup） |

看 DynamoDB Local 裡的資料：

```bash
export AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local AWS_DEFAULT_REGION=us-east-1
aws dynamodb list-tables --endpoint-url http://localhost:8101
aws dynamodb scan --table-name messenger-local-chat_state --endpoint-url http://localhost:8101
```

## 壓測

`loadtest/`（k6 WebSocket）：`chat`（驗送達 p95 < 500ms、序號無缺無重、送訊零失敗）、`failover`（壓測中 kill pub/sub）。見 [`loadtest/README.md`](./loadtest/README.md)。

## 簡報

```bash
pip install python-pptx && python build_ppt.py          # → Messenger.pptx
# 本機預覽（無 Keynote/PowerPoint）：LibreOffice 轉 PDF，再 pdftoppm 逐頁看
FONTCONFIG_FILE=/opt/homebrew/etc/fonts/fonts.conf /Applications/LibreOffice.app/Contents/MacOS/soffice --headless --convert-to pdf Messenger.pptx
```

匯入 Google Slides：python-pptx 直接產生的 pptx Slides 可能打不開，先用 LibreOffice `--convert-to pptx` 轉一輪再上傳 Drive、用 Slides 開啟 →「檔案 → 儲存為 Google 簡報」。

## AWS 部署（production）

Terraform 在 [`./infra/`](./infra/)（`ap-northeast-1`）。架構：

```
Client ─wss─► CloudFront (+WAF) ─► ALB（只收 CloudFront + X-Origin-Verify）─► gateway ASG ─內部WS─► pubsub ASG
                                                                                   │                  │
                                                                                 etcd（t4g.micro）◄───┘
                                                                                   DynamoDB（5 張表）
```

沒有 API Gateway：HTTP API 不能代理 WebSocket；WebSocket API 的 `@connections` fan-out 等於 PDF 淘汰的「每個收件人一次 RPC」（DESIGN 附錄 D）。etcd 在 demo 是單台（SPOF），正式版 3 節點跨 3 AZ。

CI/CD：`.github/workflows/deploy-messenger.yml`（workflow_dispatch）。先逐台重啟 pub/sub（`max-concurrency 1`，每台 graceful 轉移 shard），再重啟 gateway。

```bash
cd infra && export PATH="$HOME/bin:$PATH"
terraform init && terraform apply     # 會計費
terraform output ws_url               # wss://xxxx.cloudfront.net/ws
terraform destroy
```
