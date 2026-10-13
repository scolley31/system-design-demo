# Messenger — 設計討論文件（含選項與優劣）

## Context

讀完 PDF（系統設計題「Design Messenger」，buildmoat.org）後，討論如何實作一個群聊訊息系統：使用者建立群聊（≤100 人）、收發訊息、離線後補收；在線送達 < 500ms、訊息不可遺失、同一個 chat 所有人看到完全一致的順序、支援 1 億使用者。

本專案是依此設計實作的**可跑原型**（比照同 repo 的 QR Code Generator / Earthquake / Amazon Price Tracking）。和前三題最大的不同：**這是第一個真多 process、真 failover 的 demo** —— `docker compose up` 會起 2 台 gateway、2 台 pub/sub shard owner、etcd、DynamoDB Local、nginx，可以在 demo 頁上直接 kill 一台 pub/sub，看 shard 轉移、訊息照樣連號無缺無重。另有單 process dev 模式（SQLite + 記憶體 registry）方便改 code。技術棧 **Python + FastAPI + WebSocket**。

---

## 三大關鍵架構決策（決策地圖）

| 關鍵決策 | 選擇 | 為什麼 / 連鎖反應 |
|---|---|---|
| **① 怎麼把訊息推到 client** | **WebSocket + stateless gateway** | polling 大量空回應、long polling 每次都要重建請求；聊天是高頻雙向 → WebSocket。gateway 只管連線、不擁有任何 chat，使用者可以連任意一台 → 水平擴展容易。代價：連線是長期狀態，要處理斷線重連 + 補收。 |
| **② 發送者和接收者在不同 host 時怎麼找到對方** | **以 chat 分片的 pub/sub + etcd registry**（淘汰以 user 分片的 consistent hashing） | 以 user 分片：每則訊息要對 N 個收件人各發一次 RPC、scale 時要搬 user ownership、同一個 chat 的訊息散在多台 → 無法定序。改成 `shard = hash(chat_id) % S`，每個 shard 同一時間只有一個 owner；gateway 只向 owner 訂閱一次 → fan-out 是對 **M 個 gateway** 而不是 N 個使用者。連鎖：需要 service discovery 決定誰是 owner（etcd lease + CAS）。 |
| **③ 怎麼保證順序與不遺失** | **owner 指派 per-chat 連續序號 + 先持久化再 ack / fan-out** | 唯一 owner = 順序的唯一真實來源；在 per-chat lock 內 `message_id = head + 1`，**寫進 DB 成功才回 SUCCESS、才推送**。序號是連續的 → client 看到跳號就知道漏了、主動補拉。連鎖：owner 會掛 → 需要 fencing（DB conditional put）+ 去重（client_msg_id），failover 才不會重號、重送才不會重複。 |

→ ①決定「怎麼連」、②決定「怎麼找到對的人」、③決定「怎麼不亂、不丟」。下面逐項展開。

---

## 需求摘要

**FR**：① 建群聊（≤100 人）；② 收發訊息；③ 離線使用者回來後收到期間的訊息。
**Out of scope**：附件、視訊、線上狀態。
**NFR**：在線送達 < 500ms；耐久（不遺失）；同 chat 順序一致；1 億使用者、高吞吐。

**容量與數學**：
- 同時在線：1 億條 WebSocket。WhatsApp（Erlang）單機 100–200 萬 → 50–100 台 gateway；**Python 實際約每 process 5 萬**連線 → 這份實作要 2,000 台。設計成立，實作語言不成立；正式版 gateway 用 Go / Erlang / Rust（附錄 L）。
- 訊息量：假設每人每天 50 則 → 50 億則/天 ≈ **58K msg/s 平均、~175K msg/s 峰值**。
- fan-out：平均群組 10 人 → 峰值約 175 萬次投遞/s，但 owner 端只需推給訂閱的 gateway 數 M ≤ min(N, gateway 數)。
- 儲存：每則 ~200B × 50 億 ≈ **1TB/天**（DynamoDB 按量計費，冷資料可轉 S3）。
- DynamoDB 寫入：每則訊息是一個 2-item transaction（message + client_msg 去重）= 2× WCU ×2（交易加倍）→ 峰值約 70 萬 WCU，需事先申請 table quota（或把 client_msg 改成只在 failover 窗口檢查，見附錄 B）。
- shard 數：正式版 S = 4096。單一 owner 約 2 萬 msg/s（瓶頸在 DB 延遲）→ 峰值需 ~9 台，留餘裕 ~20 台。

---

## 技術選型定案（與討論結論）

| 面向 | 定案 | 為什麼（淘汰誰） |
|---|---|---|
| Client 協定 | **WebSocket**（JSON frame） | 見下方協定比較表。SSE 只能單向；gRPC-Web 瀏覽器要 proxy。 |
| 分片 / 路由 | **chat 分片，固定 S 個 shard，etcd 記 shard → owner** | 淘汰 user 分片（PDF 方案 A 的三個問題）；淘汰 consistent hashing ring（附錄 A：固定 shard + 明確轉移更好推理）。 |
| Service discovery | **etcd**（lease + txn + watch） | Raft 強一致、lease 過期自動刪 key（= heartbeat）、txn 做 CAS claim、watch 推變更，一個靜態 binary。淘汰 Consul（health check 模型偏服務，不是 lock）、ZooKeeper（JVM、ops 重）、Redis key+TTL（不是共識系統，腦裂時會雙主）。 |
| etcd client | **httpx 打 etcd v3 JSON gateway** | 不裝 grpcio（aetcd 需 grpc；python-etcd3 已停更、與 protobuf 4 不相容）。 |
| Pub/sub 層 | **自建 pub/sub server（= shard owner）** | 淘汰 Redis Pub/Sub：沒有持久化也不負責定序，仍要另做 sequencer；cluster 模式 7.0 前是全叢集廣播。淘汰 Kafka（partition = shard）：順序與耐久都好，但延遲與營運較重、gateway 要消費大量 partition；一個 chat 一個 topic 不可行。 |
| 內部通訊 | **gateway ↔ pub/sub 內部 WebSocket** | 與對外同一套技術、雙向、長連線；gRPC bidi 是正式版升級路徑（型別安全，但要 codegen）。 |
| message_id | **per-chat 連續序號（owner 指派）** | 淘汰 client timestamp（clock skew、不唯一）。淘汰 Snowflake / UUIDv1：有序但**不連續**，client 無法用跳號偵測漏訊息。 |
| 訊息儲存 | **DynamoDB**（PK=chat_id, SK=message_id） | 存取模式就是「依 chat 分區、依序號排序、只 append」；conditional put 做 fencing。淘汰 Cassandra（LWT 慢、ops 重）、Postgres（單 primary 寫入上限）。dev 模式 SQLite。 |
| 去重 | **client_msg 表 + owner 記憶體 LRU** | LRU 快但 shard 搬家就沒了；DB 表跨 owner 有效（PDF 沒有，見附錄 B）。 |
| 本地部署 | **docker compose 全套 + DynamoDB Local** | 本地 = 正式版 code path；另留單 process dev 模式。 |
| AWS 入口 | **CloudFront → ALB（無 API Gateway）** | HTTP API 不能代理 WebSocket；API Gateway WebSocket API 的 `@connections` fan-out 是每個連線一個 POST = PDF 淘汰的 N-RPC，且有 2 小時連線上限、按訊息計費（附錄 D）。 |

---

## API（定案，對齊 PDF；全部走 WebSocket，JSON，`type` 欄位）

### 協定比較（PDF Network Protocols）

| 方式 | 機制 | 優點 | 缺點 | 適用 |
|---|---|---|---|---|
| Simple polling | 固定間隔 GET | 簡單 | 大量空回應、高延遲 | 偶爾檢查通知 |
| Long polling | server hold 住直到有資料 | 比 polling 即時 | 每次回應後要重建請求；頻繁更新時仍有延遲 | 中低頻 |
| SSE | server → client 單向串流 | 簡單、自動重連 | 單向；client 送訊仍要另一條 HTTP | 通知、價格推播（上一題用） |
| **WebSocket** | HTTP Upgrade 成持久雙向連線 | 真雙向、低延遲 | 連線管理與錯誤恢復較複雜 | **聊天**、遊戲、協作 |

### Client → Gateway（`/ws?user_id=` ；正式版 `?token=` Cognito JWT）

| frame | 欄位 | 回應 |
|---|---|---|
| （連上） | | `welcome {gateway_id, chats:[{chat_id, name, participants, last_read, head}]}` → 每個 chat 補收 `newMessage{catchup}` + `catchupDone` |
| `createChat`（PDF） | `req_id, name, participants[]` | `result {status SUCCESS\|FAILURE, chat_id}` |
| `sendMessage`（PDF） | `req_id, chat_id, client_msg_id, text, client_ts` | `result {status, message_id, dup}` |
| `modifyChatParticipants`（PDF） | `req_id, chat_id, user_id, operation ADD\|REMOVE` | `result {status, message_id(系統訊息)}` |
| `fetchMessages` | `req_id, chat_id, after_id, limit` | `messages {items[], has_more}`（跳號補拉 / 往回看歷史） |
| `listChats` | `req_id` | `chats {chats[]}` |
| `ack`（PDF） | `chat_id, message_id`（累積式） | — |
| `ping` | `t` | `pong` |

Gateway → Client 推送：`newMessage {chat_id, message_id, kind user|system, text, created_by, created_at, client_msg_id, client_ts, catchup}`、`chatAdded`、`chatRemoved`、`catchupDone`、`error`。

錯誤碼：`NOT_MEMBER` `CHAT_NOT_FOUND` `TOO_MANY_PARTICIPANTS` `PAYLOAD_TOO_LARGE`（>4KB）`STORE_ERROR` `UNAVAILABLE`（重試 8 秒仍無 owner）`BAD_FRAME`。

### Gateway ↔ Pub/sub（內部，`/internal/ws?gateway_id=`）

| frame | 回應 |
|---|---|
| `subscribe {topics}` | `subscribed {ok:[{topic, head}], rejected:[{topic, code}]}` |
| `unsubscribe {topics}` | — |
| `publish {chat_id, client_msg_id, sender, text, client_ts}` | `published {message_id, created_at, dup}` / `publish_failed {code}` |
| `modify {chat_id, actor, user_id, operation}` | `modified {message_id, chat}` / `publish_failed` |
| `notify {topic: u:..., event}` | `notified` |
| （owner 推） | `deliver {topic, event}`、`shard_moved {shards}` |

topic 兩種：`c:{chat_id}`（訊息流，有序持久）、`u:{user_id}`（chatAdded/Removed 控制事件，不持久）。兩者都用同一個 `crc32(topic) % S` 分片。

---

## 資料模型

| 表 | 鍵 | 欄位 / 用途 |
|---|---|---|
| `chat` | `chat_id` | name, created_by, created_at。也是 createChat 的 **commit marker** |
| `membership` | `chat_id` PK + `user_id` SK | 查一個 chat 的所有成員（PDF） |
| `message` | `chat_id` PK + `message_id` SK | text, created_by, kind, client_msg_id, client_ts, created_at |
| `chat_state` | `user_id` PK + `chat_id` SK | `last_read_message_id`。PK=user_id 兼當「我有哪些 chat」的索引 |
| `client_msg` | `chat_id` PK + `client_msg_id` SK | → message_id；TTL 1 天；跨 owner 的持久去重 |

- **PDF 的不一致**：ack 流程一處寫更新 Membership 的 `last_read_message_id`、一處寫 ChatState。本專案統一放 ChatState（membership 只表達「誰在群裡」）。
- **為什麼不用 GSI**：在 membership 上建 `user_id` 的 GSI 也能查「我的 chat」，但 GSI 只有最終一致；chat_state 以 user_id 為 PK 可以強一致讀。代價是 createChat 要多寫 N 筆。
- **createChat 的交易上限**：PDF 說 chat + membership 包在同一個交易。DynamoDB TransactWriteItems 上限 100 items，而 1 chat + 100 membership + 100 chat_state = 201 → 改用 commit-marker（附錄 C）。SQLite 路徑仍是真交易。
- **新成員看不到加入前的歷史**：ADD 時 chat_state 從目前 head 開始（PDF 未規定；這是多數聊天 app 的行為）。

---

## 深入探討 1：如何處理數億使用者同時在線？

**問題**：數百台 server，發送者連在 gateway 1、接收者連在 gateway 2，訊息怎麼過去？

1. **（淘汰）以 user ownership 做 consistent hashing**：registry 記 `user_id → node`，發訊時找每個收件人的 node 發 RPC。問題：每則訊息 N 次 RPC（百人群聊 = 百次）；scale 時要搬 user ownership；**同一個 chat 的訊息由不同 owner 處理 → 無法強制單一順序**（這是致命傷）。
2. **（本專案）以 chat 為核心的 pub/sub**：
   - key space 是 chat：`shard = crc32(topic) % S`。**不能用 Python `hash()`**：字串 hash 每個 process 隨機化，兩台 gateway 會算出不同 shard。
   - etcd 記 `shards/{i} → {node_id, ws}`；pub/sub 節點用 lease 註冊 + heartbeat；gateway 啟動時 range 載入、之後 watch 變更、快取在本地 ShardMap。
   - 使用者連上任一 gateway → 對他的每個 chat，gateway 向該 chat 的 owner `subscribe(c:{chat})` 一次（引用計數：本台第一個在看的人才訂、最後一個離開才退）。
   - 訊息流：`sender → gateway → owner（定序 + 持久化）→ 訂閱的 gateway → receiver`。
   - 好處：fan-out 對 M 個 gateway；gateway stateless；**每個 chat 唯一 owner → 定序可行**（深入探討 2）。

**shard 擁有權（`app/registry.py`、`app/pubsub.py`）**：
- 每個 pub/sub 節點：`lease grant(TTL 5s)` → `put nodes/{id}`（附 lease）→ 每 TTL/3 keepalive。
- 搶 shard：etcd txn `compare create_revision == 0 → put shards/{i}`（附自己的 lease）= CAS，只有一個人會成功。
- 公平份額 `ceil(S / 活著的節點數)`；不足就搶沒人持有的；新節點加入時持有過多者 graceful 交出（drain → release；2 秒遲滯避免來回搶）。
- 節點死掉 → lease 過期 → 它的 shard key 全部被 etcd 刪掉 → 其他節點 watch 到、jitter 後搶。graceful 下線（SIGTERM）則主動 revoke lease → **立即**轉移。

**實測（compose，本機）**：開機 16 shard 自動分成 8/8；hard kill（SIGKILL）約 **5.3 秒**後存活節點接手全部 16 個；重新加入後自動 rebalance 回 8/8。

---

## 深入探討 2：如何處理訊息亂序？

**原則**：讓「接收並持久化訊息的 server」成為順序的唯一真實來源。chat 分片的 pub/sub 正好有這個性質。

**owner 端（`PubSubNode._append_and_fanout`）**：
```
async with chat.lock:                     # 同一個 chat 序列化
    mid = chat.head + 1                   # per-chat 連續序號
    store.append_message(chat, mid, ...)  # DynamoDB TransactWrite：
                                          #   message: attribute_not_exists(message_id)   ← fencing
                                          #   client_msg: attribute_not_exists(client_msg_id) ← 去重
    chat.head = mid
    fanout(deliver)                       # 仍在鎖內 → 推送順序 = message_id 順序
return published(mid)                     # gateway 這時才回 sender SUCCESS
```

**為什麼是連續序號，不是 Snowflake / UUIDv1**：三者都「有序且唯一」，但只有連續序號能讓 client **偵測漏掉的訊息**：已看到 #7、收到 #9 → 一定漏了 #8 → 先緩衝 #9、`fetchMessages(after=7)` 補拉 → 依序顯示。收到 ≤ 已看到的 id → 重複，丟掉。Snowflake 的缺號是常態，無法判斷。代價：序號由 owner 單點指派 → 每個 chat 的寫入受單一 lock 與 DB 延遲限制（~100 msg/s/chat，百人上限下足夠）。

**client 端（`index.html` 的 `onNewMessage`）**：`id == lastSeen+1` → 顯示並 drain 緩衝；`id > lastSeen+1` → 緩衝 + 補拉；`id <= lastSeen` → 去重。demo 頁的「順序與延遲」面板即時顯示補拉 / 重排 / 重複次數。

**實測**：兩個分頁分別連 gw-1 / gw-2、**同時**連發 20 則到同一個 chat → 雙方都看到 #1–#44 完全相同的順序、零重排零重複；跨 gateway ack p50 約 5ms。

---

## 深入探討 3：離線補收與耐久性

- **persist-before-ack**：sender 收到 SUCCESS 時訊息一定已經在 DynamoDB；DB 寫失敗 → 不 fan-out、回 `FAILURE STORE_ERROR`，client 保留在「送出失敗、可重送」狀態。
- **重送不重複**：client 每則訊息帶 `client_msg_id`；逾時重送、gateway 重試、跨 failover 重試都用同一個 → owner LRU 或 `client_msg` 表回同一個 message_id、`dup=true`。
- **離線補收**（PDF 的 offline flow）：連上 → `welcome` → **先訂閱、再補收**（`messages_after(chat_state.last_read)`）。順序反過來會有空窗：補收完、訂閱前送出的訊息會漏掉；先訂閱則最壞是收到重複，client 依 id 去重。
- **ack 合併**：client 累積式 ack（只回報最大連號），gateway 每 500ms 批次寫 chat_state（只前進不後退）。代價：gateway 掛掉時最多重送 500ms 內的訊息 → at-least-once + client 去重，符合「不遺失」。
- **一次補收上限 200 則**，`catchupDone.has_more` 時 client 自己 `fetchMessages` 往下拉。

**實測**：bob 斷線期間 alice 送 5 則 → bob 重連收到 #21–#25（`catchup=true`）；重送同 `client_msg_id` → `dup=true`、同一個 message_id。

---

## 原型 vs Production（env-gated）

| 面向 | dev（單 process） | 本地部署 / 正式版 | 切換 env |
|---|---|---|---|
| 程序角色 | `ROLE=all`：gateway + pub/sub 同一個 uvicorn | `ROLE=gateway` / `ROLE=pubsub` 分開部署 | `ROLE` |
| 內部連線 | LocalLink（直接呼叫） | WsLink（內部 WebSocket） | 依 ROLE |
| Registry | 記憶體（本節點持有全部 shard） | etcd | `ETCD_URL` |
| 儲存 | SQLite | DynamoDB Local（compose）/ DynamoDB（AWS） | `DYNAMO_TABLE_PREFIX`、`DYNAMO_ENDPOINT` |
| shard 數 | 16 | 16（compose）/ 64（AWS demo）/ 4096（正式） | `NUM_SHARDS` |
| 入口 | uvicorn :8030 | nginx :8030（compose）/ CloudFront → ALB（AWS） | 部署形態 |
| Auth | `?user_id=` | Cognito JWT `?token=` | `AUTH_ENABLED` |
| debug kill | 開 | compose 開 / AWS 關 | `ALLOW_DEBUG` |

---

## 附錄

### A. 固定 S 個 shard vs consistent hashing ring

consistent hashing 的價值是「節點增減時只搬 1/N 的 key」。但這裡的 key 是 chat，而 owner 的狀態（序號、訂閱表）都能從 DB lazy 重建，搬動成本低；更重要的是**每個 shard 在任一時刻只能有一個 owner**，需要的是「明確的擁有權轉移」而不是「hash 環上的鄰近關係」。所以用固定 S 個 shard（同 Kafka partition、Redis Cluster 16384 slots）+ etcd 明確記錄 owner：搬家是一個 CAS，容易推理、容易觀察（demo 頁的 shard grid）。S 要一開始就給夠（正式 4096），之後改 S 需要遷移。

### B. Failover 與 fencing 時序

```
t=0      pubsub-1 被 SIGKILL（持有 shard 5，chat X 在 shard 5）
t=0~5s   gateway → pubsub-1 的內部 WS 斷線 → gateway 清掉這些 topic 的訂閱
         alice 送訊：gateway call_owner() 發現 link down → 等 shardmap 變更（重試，同 client_msg_id）
t≈5s     etcd：pubsub-1 的 lease 過期 → nodes/pubsub-1、shards/* 被刪 → watch 事件
t≈5.1s   pubsub-2 搶下 shard（CAS）；狀態一律從 DB 重建（head = max(message_id)，consistent read）
         gateway watch 到新 owner → 對 pubsub-2 重訂閱 → subscribed 回 head → 跟本台 last_forwarded 比對，缺的從 DB 補送
         alice 那則訊息重試到 pubsub-2 → 寫入 → SUCCESS（實測最慢一則 4.3s）
graceful：SIGTERM → fence（拒絕新 publish）→ revoke lease → key 立刻消失 → 實測最慢一則 42ms
```
邊界情況：
- **逾時但其實寫成功了**：重試命中 `client_msg` 條件 → 同 id、`dup=true`。
- **殭屍舊 owner**（GC 停頓、網路分割）：keepalive 超過 TTL−1s 沒成功就自我 fencing、拒絕 publish。萬一它在 fencing 前寫了 N+1，而新 owner 已讀到 head=N：新 owner 寫 N+1 撞 `attribute_not_exists` → `IdConflict` → 重讀 head、向 etcd 確認自己仍是 owner → 寫 N+2。殭屍寫的 N+1 在 DB 裡，接收端靠跳號補拉拿到。**結果：無遺失、無重號，DB 順序就是唯一真相。**
- **etcd 整個掛掉**：既有 owner 繼續服務（沒人能搶）；但這段時間無法 failover。正式版 etcd 3 節點跨 3 AZ。
- **500ms SLA 在 failover 窗口內不成立**（hard ≈ TTL）。降 TTL 可縮短，但誤判（GC 停頓就被踢）機率上升。

### C. DynamoDB 交易上限與 createChat

TransactWriteItems 上限 100 items / 4MB。百人群聊建群需 201 筆 → commit-marker：① BatchWrite membership + chat_state（key 固定 → 可無限重試、冪等）② 最後 conditional put `chat` 列 = commit 點。讀「我的 chat」時 BatchGet chat 表過濾掉 ① 成功 ② 失敗的孤兒；孤兒無害，可由背景 job 清掉。訊息寫入（message + client_msg）是 2-item 交易，在上限內。

### D. WebSocket 經過 CloudFront / ALB / nginx

- API Gateway HTTP API 不支援 WebSocket；API Gateway WebSocket API 可以，但 fan-out 要對每個 connectionId 呼叫 `@connections` POST（= N 次 RPC）、連線 2 小時上限、10 分鐘 idle、按訊息計費 → 淘汰。
- CloudFront 原生轉送 WebSocket；origin = 公開 ALB，SG 只放 CloudFront origin-facing prefix list，且 listener 只有帶正確 `X-Origin-Verify` header 才轉發（防直連 ALB）。
- idle timeout：ALB 300s、nginx 3600s；app 每 20s 送 WS ping（uvicorn `--ws-ping-interval 20`），client 每 25s 送 app 層 ping。
- 部署時長連線會被切斷 → client 指數退避重連 + 補收，使用者只看到短暫「重連中」。

### E. Backpressure / 慢消費者

每條連線（client 與內部 peer）都有上限 1000 的送出佇列。滿了 = 慢消費者 → 直接斷線（1013），而不是讓 owner 的鎖或記憶體被拖住；client 重連走補收、gateway 重連走 head 比對補送。

### F. 100 人上限

createChat（含建立者，去重後）與 modify ADD 都檢查；ADD 在 owner 的 per-chat 狀態上檢查，與其他成員變更序列化。

### G. Rebalance

新節點加入 → 公平份額下降 → 持有過多者逐一 drain（新 publish 回 NOT_OWNER、等進行中的鎖釋放）→ release。gateway 的 publish 收到 NOT_OWNER 會刷新 shardmap、重試到新 owner。遲滯 2 秒避免開機競爭時來回搶。

### H. WebSocket 上的 Auth

瀏覽器的 WebSocket API 不能自訂 header → Cognito id token 放 `?token=`；gateway 在 handshake 時驗簽、取 sub 當 user_id。access log 要遮掉 query string。demo 用 `?user_id=`（`AUTH_ENABLED=0`）。

### I. Monitoring

CloudWatch：ALB 健康主機 / 5xx、兩組 ASG CPU、etcd instance status、DynamoDB throttle、CloudFront 5xx；log metric filter：`shard_takeover`、`id_conflict`、`publish_failed`。app 內 `/api/cluster`（gateway）與 `/debug/state`（pub/sub）提供 shard 分佈、retry / repair / dup / slow_peer_drop 計數。

### J. 清理

`client_msg` 用 DynamoDB 原生 TTL（1 天）；訊息永久保留；createChat 孤兒列可由排程清理（非必要）。

### K. 錯誤處理與降級

- owner 暫時不在：gateway 最多重試 8 秒（涵蓋一次 hard failover），之後 `FAILURE UNAVAILABLE`，client 可用同 client_msg_id 重送。
- DB 錯誤：不 fan-out、回 `STORE_ERROR`。
- 單一 gateway 掛：client 重連到另一台（nginx / ALB），補收。
- malformed frame：回 `error`，不斷線。

### L. Python 規模限制

asyncio + uvicorn 單 process 約 5 萬 WebSocket；per-chat lock 讓單一 chat 的吞吐受 DB 延遲限制。架構（stateless gateway、chat 分片、序號、fencing）可原封不動搬到 Go / Erlang；這份原型的目的是把行為與失敗模式跑給你看。
