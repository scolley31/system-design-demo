// Amazon Price Tracking Service 壓測（k6）。場景由 env SCENARIO 切換。
//
//   BASE_URL  對外網址（雲端=CloudFront URL；本機=http://localhost:8020）
//   SCENARIO  history | report | subscribe
//             - history：GET /price/{asin}?period=…（NFR 讀路徑，p95 < 500ms，應命中 pre-aggregation）
//             - report：extension 眾包回報 write path（POST /price-reports，永遠不觸發可疑判定）
//             - subscribe：訂閱寫入（POST /subscriptions）
//   SEED      setup 先 track 幾個商品（預設 20；前 5 個回填 2 年 × 每小時，其餘 30 天）
//   RATE      requests/s（預設 history 200 / report 100 / subscribe 50）
//   DURATION  場景時長（預設 1m）
//   SMOKE     設 1 → 用極小量（本機冒煙：rate 5、15s、SEED 3）
//
// 註：server 必須以 MOCK_AMAZON=1 啟動——setup 用 /debug/seed 回填假歷史、track 的是合成 ASIN（LOADTST000…），
//     真 Amazon 上不存在。回填不會更新 products.last_price，last_price 來自 track 觸發的 mock 爬蟲（crawl_rps=1），
//     所以 setup 會輪詢 GET /products 直到每個 ASIN 都拿到價格，report 場景才有 baseline 可對。
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8020";
const API = `${BASE}/api/v1`;
const SCENARIO = __ENV.SCENARIO || "history";
const SMOKE = !!__ENV.SMOKE;
const SEED = parseInt(__ENV.SEED || (SMOKE ? "3" : "20"));
const DEFAULT_RATE = { history: 200, report: 100, subscribe: 50 };
const RATE = parseInt(__ENV.RATE || (SMOKE ? "5" : String(DEFAULT_RATE[SCENARIO] || 50)));
const DURATION = __ENV.DURATION || (SMOKE ? "15s" : "1m");
const FULL_HISTORY = 5; // 前幾個商品回填 2 年（其餘 30 天），讓 1y/2y 查詢也走 monthly/weekly 彙總

const jsonHeaders = { "Content-Type": "application/json" };
const PERIODS = ["7d", "30d", "90d", "1y", "2y"];
const pick = (a) => a[Math.floor(Math.random() * a.length)];
const asinOf = (i) => `LOADTST${String(i).padStart(3, "0")}`; // 恰好 10 碼大寫英數，符合 ASIN 格式

// reporter_token 限流：server 每個 token 30/min。constant-arrival-rate 下 k6 只用少數 VU 就能打滿 RATE，
// 若 token 只綁 __VU，單一 token 每分鐘會收到 ≈ RATE*60/活躍VU 筆（RATE=100 時輕易破千）。
// 因此 token 再帶 floor(__ITER / ROTATE_EVERY)：每個 token 一生最多只收 ROTATE_EVERY 筆，
// 取 25 < 30 → 不論 RATE 多大都不會 429（代價：server 每分鐘看到 ≈ RATE*60/25 個新 token，RATE=100 → 240 個）。
const ROTATE_EVERY = 25;

// preAllocatedVUs 依 rate 估：本機 ~10ms 一筆，rate/20 已夠；留 maxVUs 給雲端高延遲時擴充。
const vus = (rate) => ({ preAllocatedVUs: Math.max(5, Math.ceil(rate / 20)), maxVUs: Math.max(50, rate) });
const scenarios = {};
scenarios[SCENARIO] = {
  executor: "constant-arrival-rate",
  exec: { history: "getHistory", report: "postReport", subscribe: "postSubscribe" }[SCENARIO],
  rate: RATE,
  timeUnit: "1s",
  duration: DURATION,
  ...vus(RATE),
  tags: { scenario: SCENARIO },
};

const thresholds = { http_req_failed: ["rate<0.01"], checks: ["rate>0.99"] };
if (SCENARIO === "history") thresholds["http_req_duration{scenario:history}"] = ["p(95)<500"]; // NFR：價格歷史 p95 < 500ms

export const options = { scenarios, thresholds };

export function setup() {
  const asins = [];
  for (let i = 0; i < SEED; i++) asins.push(asinOf(i));

  // 1) track：註冊 + 排一次 mock 爬蟲（給 last_price）
  let tracked = 0;
  for (const a of asins) {
    const r = http.post(`${API}/products/track`, JSON.stringify({ product_id: a }), { headers: jsonHeaders });
    if (r.status === 200) tracked++;
  }
  // 2) 回填歷史 + 跑彙總（history 場景才會命中 source=aggregation 而非 raw_fallback）
  let seeded = 0, aggregated = 0;
  asins.forEach((a, i) => {
    const days = i < FULL_HISTORY ? 730 : 30;
    const s = http.post(`${API}/debug/seed`, JSON.stringify({ product_id: a, days, per_day: 24 }),
      { headers: jsonHeaders, timeout: "60s" });
    if (s.status === 200) seeded++;
    else if (i === 0) console.error(`seed 失敗 ${s.status}：${s.body}（server 需 MOCK_AMAZON=1 或 ALLOW_SEED=1）`);
    const g = http.post(`${API}/aggregations/run?product_id=${a}`, null, { timeout: "60s" });
    if (g.status === 200) aggregated++;
  });
  // 3) 等 mock 爬蟲把 last_price 補齊（crawl_rps=1 → 最多約 SEED 秒），最多等 60s
  const prices = {};
  for (let t = 0; t < 60; t++) {
    const list = http.get(`${API}/products`).json() || [];
    for (const p of list) if (asins.includes(p.asin) && p.last_price) prices[p.asin] = p.last_price;
    if (Object.keys(prices).length >= asins.length) break;
    sleep(1);
  }
  for (const a of asins) if (!prices[a]) { prices[a] = 100.0; console.warn(`${a} 沒拿到 last_price，report 會落 pending_verify`); }
  console.log(`setup: tracked ${tracked}/${SEED}, seeded ${seeded}, aggregated ${aggregated}, priced ${Object.keys(prices).length}`);
  return { asins, prices };
}

export function getHistory(data) {
  const asin = pick(data.asins);
  const res = http.get(`${API}/price/${asin}?period=${pick(PERIODS)}`, { tags: { scenario: "history" } });
  const j = res.status === 200 ? res.json() : {};
  check(res, {
    "history 200": (r) => r.status === 200,
    "history source=aggregation": () => j.source === "aggregation", // 掃彙總表而非 raw 逐筆
    "history has points": () => Array.isArray(j.points) && j.points.length > 0,
  });
}

export function postReport(data) {
  const asin = pick(data.asins);
  const price = Math.round(data.prices[asin] * (1 + (Math.random() * 0.04 - 0.02)) * 100) / 100; // ±2%，遠低於 30% 可疑門檻
  const token = `k6-${__VU}-${Math.floor(__ITER / ROTATE_EVERY)}`;
  const res = http.post(`${API}/price-reports`,
    JSON.stringify({ product_id: asin, price, reporter_token: token }),
    { headers: jsonHeaders, tags: { scenario: "report" } });
  check(res, {
    "report 200": (r) => r.status === 200,
    "report accepted": (r) => r.status === 200 && r.json().status === "accepted",
  });
}

export function postSubscribe(data) {
  const asin = pick(data.asins);
  const res = http.post(`${API}/subscriptions`,
    JSON.stringify({ user_id: `load-${__VU}-${__ITER}`, product_id: asin,
      price_threshold: Math.round(data.prices[asin] * (0.5 + Math.random() * 0.7) * 100) / 100, notification_type: "sse" }),
    { headers: jsonHeaders, tags: { scenario: "subscribe" } });
  check(res, { "subscribe 200": (r) => r.status === 200 });
}
