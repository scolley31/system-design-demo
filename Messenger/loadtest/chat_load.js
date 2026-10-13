// Messenger WebSocket 壓測（k6）。場景由 env SCENARIO 切換。
//
//   BASE_URL  ws(s):// 對外網址（本機=ws://localhost:8030（nginx）；雲端=wss://<cloudfront>）
//   SCENARIO  chat | failover
//             - chat：每個 VU 一條長連線、固定節奏在自己的群聊發言；驗 NFR 送達 p95 < 500ms、順序無缺無重
//             - failover：同 chat 的流量，但 threshold 放寬，跑的同時手動 kill / stop 一台 pubsub（見 README）
//   VUS       同時在線人數（預設 200；= 連線數）
//   CHATS     群聊數（預設 20；user i 屬於 chat i % CHATS，每群 ≤ 99 人 + admin）
//   INTERVAL  每個 VU 兩次發言間隔秒數（預設 2 → 總送訊率 ≈ VUS / INTERVAL 則/s，fan-out ×（VUS/CHATS））
//   DURATION  每條連線保持多久（預設 1m）
//   SMOKE     設 1 → VUS 10、CHATS 2、DURATION 20s
//   RUN_ID    user id 前綴（預設隨機），每次跑都是全新的 user / chat：lt-<run>-u<i>、lt-<run>-admin
//
// 正確性檢查（每個 VU 對自己的 chat）：message_id 是 per-chat 連續序號 1,2,3…，每個成員（含發送者 echo）都會收到每一則。
//   id == lastSeen+1 → 前進；id > lastSeen+1 → seq_gaps +1，fetchMessages(after lastSeen) 補洞；
//   id <= lastSeen（且不是剛 fetch 補到的 / gateway repair 重送的）→ dup_or_out_of_order +1。
//
// k6 版本：k6 ≥ v1.0 是 k6/websockets，v0.x 是 k6/experimental/websockets（API 相同）→ 用 require 依序嘗試。
import ws from "k6/ws";
import { check } from "k6";
import { setTimeout, setInterval, clearInterval } from "k6/timers";
import { Trend, Counter } from "k6/metrics";

let wsmod;
try {
  wsmod = require("k6/websockets");
} catch (e) {
  wsmod = require("k6/experimental/websockets");
}
const WebSocket = wsmod.WebSocket;

const BASE = (__ENV.BASE_URL || "ws://localhost:8030").replace(/\/+$/, "");
const SCENARIO = __ENV.SCENARIO || "chat";
const SMOKE = !!__ENV.SMOKE;
const VUS = parseInt(__ENV.VUS || (SMOKE ? "10" : "200"));
const CHATS = parseInt(__ENV.CHATS || (SMOKE ? "2" : "20"));
const INTERVAL = parseFloat(__ENV.INTERVAL || "2");
const DURATION = __ENV.DURATION || (SMOKE ? "20s" : "1m");
// 注意：init code 每個 VU 各跑一次，隨機 RUN_ID 必須在 setup() 產生再經 data 傳給 VU，否則每個 VU 前綴都不同
const RUN_ID_ENV = __ENV.RUN_ID || "";
const DRAIN_MS = 3000; // 停止發言後再等多久收尾（讓最後幾則的 result / echo 回來）

if (!["chat", "failover"].includes(SCENARIO)) throw new Error(`SCENARIO 必須是 chat | failover（收到 ${SCENARIO}）`);
if (Math.ceil(VUS / CHATS) > 99) throw new Error(`每群 ${Math.ceil(VUS / CHATS)} 人 > 99（+ admin 超過 100 人上限），請加大 CHATS`);

const userOf = (run, i) => `lt-${run}-u${i}`;

function parseDuration(s) {
  let ms = 0;
  const re = /(\d+(?:\.\d+)?)(ms|s|m|h)/g;
  let m;
  while ((m = re.exec(s)) !== null) ms += parseFloat(m[1]) * { ms: 1, s: 1000, m: 60000, h: 3600000 }[m[2]];
  if (!ms) throw new Error(`DURATION 格式錯誤：${s}`);
  return ms;
}
const DURATION_MS = parseDuration(DURATION);

const deliveryMs = new Trend("delivery_ms", true); // tag kind=echo（自己發的回聲）| other（別人發的）
const ackMs = new Trend("ack_ms", true);           // sendMessage → result（含 owner 寫 DynamoDB）
const seqGaps = new Counter("seq_gaps");
const dupOrOoo = new Counter("dup_or_out_of_order");
const dupAfterRecovery = new Counter("dup_after_recovery"); // 已 fetch 補到 / gateway repair 重送的重複，client 去重即可，不算錯
const sendFailures = new Counter("send_failures");
const messagesSent = new Counter("messages_sent");
const messagesReceived = new Counter("messages_received");
const connectFailures = new Counter("connect_failures");

const thresholds =
  SCENARIO === "chat"
    ? {
        delivery_ms: ["p(95)<500"], // NFR：在線送達 p95 < 500ms
        ack_ms: ["p(95)<300"],
        seq_gaps: ["count==0"],
        dup_or_out_of_order: ["count==0"],
        send_failures: ["count==0"],
        connect_failures: ["count==0"],
      }
    : {
        // failover：owner 換手期間（hard kill 要等 lease TTL 5s）延遲會尖峰，只看 p95；
        // 缺號允許少量（gateway repair / client fetch 會補回），但不能重複亂序、不能送訊失敗
        delivery_ms: ["p(95)<2000"],
        ack_ms: ["p(95)<2000"],
        seq_gaps: ["count<10"],
        dup_or_out_of_order: ["count==0"],
        send_failures: ["count==0"],
        connect_failures: ["count==0"],
      };

export const options = {
  scenarios: {
    [SCENARIO]: {
      executor: "per-vu-iterations", // 每個 VU 只跑 1 次 iteration = 一條長連線
      vus: VUS,
      iterations: 1,
      maxDuration: `${Math.ceil((DURATION_MS + DRAIN_MS) / 1000) + 30}s`,
      gracefulStop: "10s",
      tags: { scenario: SCENARIO },
    },
  },
  thresholds,
  setupTimeout: "120s",
};

let seq = 0;
const rid = (p) => `${p}-${__VU}-${++seq}`;

// setup：admin 用阻塞式 k6/ws 建 CHATS 個群，chat j 成員 = { user i | i % CHATS == j }
export function setup() {
  const run = RUN_ID_ENV || Math.random().toString(36).slice(2, 8);
  const members = Array.from({ length: CHATS }, () => []);
  for (let i = 0; i < VUS; i++) members[i % CHATS].push(userOf(run, i));
  const chats = new Array(CHATS).fill(null);
  const pending = {};
  const res = ws.connect(`${BASE}/ws?user_id=lt-${run}-admin`, null, (socket) => {
    socket.on("message", (raw) => {
      const f = JSON.parse(raw);
      if (f.type === "welcome") {
        for (let j = 0; j < CHATS; j++) {
          const req_id = `create-${j}`;
          pending[req_id] = j;
          socket.send(JSON.stringify({ type: "createChat", req_id, name: `lt-${run}-chat${j}`, participants: members[j] }));
        }
      } else if (f.type === "result" && f.op === "createChat" && f.req_id in pending) {
        const j = pending[f.req_id];
        delete pending[f.req_id];
        if (f.status === "SUCCESS") chats[j] = f.chat_id;
        else console.error(`createChat ${j} 失敗：${f.error} ${f.detail || ""}`);
        if (Object.keys(pending).length === 0) socket.close();
      }
    });
    socket.setTimeout(() => socket.close(), 60000);
  });
  check(res, { "setup ws 101": (r) => r && r.status === 101 });
  const ok = chats.filter((c) => c).length;
  console.log(`setup: run=${run} vus=${VUS} chats=${ok}/${CHATS} (~${Math.ceil(VUS / CHATS)} 人/群) interval=${INTERVAL}s duration=${DURATION}`);
  if (ok !== CHATS) throw new Error(`setup 只建出 ${ok}/${CHATS} 個 chat`);
  return { run, chats };
}

export default function (data) {
  const idx = __VU - 1;
  const me = userOf(data.run, idx);
  const chatId = data.chats[idx % CHATS];
  const sock = new WebSocket(`${BASE}/ws?user_id=${me}`);

  const sentAt = {};     // req_id → 送出時間（算 ack_ms）
  let lastSeen = -1;     // 已連續收到的最大 message_id（-1 = 還沒初始化）
  let startupMax = 0;    // catchupDone 之前看到的最大 id（含 catch-up 與先到的 live）
  let ready = false;     // catchupDone 之後才開始順序檢查
  const ahead = new Set(); // > lastSeen+1 先到的 id（等補洞）
  const recovered = new Set(); // fetch 補到的 id：之後 live / repair 再送一次屬預期重複
  let fetching = false;
  let welcomed = false;
  let stopped = false;
  let sendTimer = null;
  let ackTimer = null;

  const send = (f) => sock.send(JSON.stringify(f));

  function advance() {
    while (ahead.has(lastSeen + 1)) {
      ahead.delete(lastSeen + 1);
      lastSeen++;
    }
    if (ahead.size > 0 && !fetching && sock.readyState === 1) {
      fetching = true;
      send({ type: "fetchMessages", req_id: rid("fetch"), chat_id: chatId, after_id: lastSeen, limit: 200 });
    }
  }

  function observe(id, { fromFetch = false, repaired = false } = {}) {
    if (id <= lastSeen || ahead.has(id)) {
      if (fromFetch) return;
      if (repaired || recovered.has(id)) dupAfterRecovery.add(1);
      else {
        dupOrOoo.add(1, { kind: id <= lastSeen ? "dup_or_old" : "dup_ahead", vu_user: me, chat: chatId, mid: String(id) });
        console.warn(`DUP ${me} chat=${chatId} id=${id} lastSeen=${lastSeen} ahead=${[...ahead].join(",")}`);
      }
      return;
    }
    if (fromFetch) recovered.add(id);
    if (id > lastSeen + 1 && ahead.size === 0 && !fromFetch) seqGaps.add(1); // 每個缺口只記一次
    ahead.add(id);
    advance();
  }

  function sendOne() {
    if (stopped || sock.readyState !== 1) return;
    const req_id = rid("send");
    const now = Date.now();
    sentAt[req_id] = now;
    send({
      type: "sendMessage",
      req_id,
      chat_id: chatId,
      client_msg_id: `${me}-${seq}-${now}`,
      text: JSON.stringify({ t: now, vu: __VU }),
      client_ts: now,
    });
    messagesSent.add(1);
  }

  sock.onopen = () => {};
  sock.onerror = (e) => {
    console.warn(`${me} ws error: ${e && e.error}`);
  };

  sock.onmessage = (ev) => {
    const now = Date.now();
    const f = JSON.parse(ev.data);
    switch (f.type) {
      case "welcome": {
        welcomed = true;
        const c = (f.chats || []).find((x) => x.chat_id === chatId);
        if (!c) {
          connectFailures.add(1, { reason: "chat_not_in_welcome" });
          console.error(`${me}: welcome 裡沒有 ${chatId}`);
        }
        startupMax = c ? c.head || 0 : 0;
        // 錯開第一次發言，避免所有 VU 同一毫秒齊發
        setTimeout(() => {
          sendOne();
          sendTimer = setInterval(sendOne, INTERVAL * 1000);
        }, Math.random() * INTERVAL * 1000);
        ackTimer = setInterval(() => {
          if (ready && lastSeen > 0 && sock.readyState === 1) send({ type: "ack", chat_id: chatId, message_id: lastSeen });
        }, 1000);
        break;
      }
      case "catchupDone":
        if (f.chat_id === chatId && !ready) {
          lastSeen = startupMax;
          ready = true;
        }
        break;
      case "newMessage": {
        if (f.chat_id !== chatId) break;
        if (!ready) {
          startupMax = Math.max(startupMax, f.message_id);
          recovered.add(f.message_id); // 補收階段看過的 id：之後 live 再到屬「先訂閱再補收」的預期重疊
          break;
        }
        if (f.catchup) break;
        messagesReceived.add(1);
        if (f.kind === "user" && f.text) {
          try {
            const p = JSON.parse(f.text);
            if (p.t) deliveryMs.add(now - p.t, { kind: f.created_by === me ? "echo" : "other" });
          } catch (e) {
            // 非壓測格式的訊息（例如手動在 UI 發的），不計延遲
          }
        }
        observe(f.message_id, { repaired: !!f.repaired });
        break;
      }
      case "messages":
        if (f.chat_id !== chatId) break;
        {
          const before = lastSeen;
          for (const m of f.items || []) observe(m.message_id, { fromFetch: true });
          fetching = false;
          if (lastSeen > before) advance(); // 有進展但還有洞（has_more）→ 接著補；沒進展就等下一則 live 再試
        }
        break;
      case "result":
        if (f.op !== "sendMessage" || !(f.req_id in sentAt)) break;
        ackMs.add(now - sentAt[f.req_id]);
        delete sentAt[f.req_id];
        if (f.status !== "SUCCESS") {
          sendFailures.add(1, { error: String(f.error) });
          console.warn(`${me} send FAILURE: ${f.error} ${f.detail || ""}`);
        }
        break;
      case "error":
        console.warn(`${me} server error: ${f.code} ${f.detail || ""}`);
        if (f.req_id && f.req_id.startsWith("fetch")) fetching = false;
        break;
      default:
        break; // chatAdded / chatRemoved / pong
    }
  };

  sock.onclose = () => {
    if (sendTimer) clearInterval(sendTimer);
    if (ackTimer) clearInterval(ackTimer);
    if (!stopped && welcomed) console.warn(`${me}: 連線被提前關閉`);
    if (!welcomed) connectFailures.add(1);
    // 收尾時還沒回 result 的送訊 = 逾時，算失敗
    const lost = Object.keys(sentAt).length;
    if (lost) sendFailures.add(lost, { error: "NO_RESULT" });
  };

  setTimeout(() => {
    stopped = true;
    if (sendTimer) clearInterval(sendTimer);
    setTimeout(() => {
      if (sock.readyState === 1 && lastSeen > 0) send({ type: "ack", chat_id: chatId, message_id: lastSeen });
      sock.close();
    }, DRAIN_MS);
  }, DURATION_MS);
}
