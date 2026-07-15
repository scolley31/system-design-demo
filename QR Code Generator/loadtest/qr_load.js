// QR Code Generator 壓測（k6）。場景由 env SCENARIO 切換。
// 重點:maxRedirects=0 → 量到 302 本身,不追到目標站。
//
//   BASE_URL  對外網址（雲端=CloudFront URL；本機=http://localhost:8000）
//   JWT       Cognito id token（雲端 create 需要；本機 AUTH 關閉時可留空）
//   SCENARIO  redirect | create | mixed
//   SEED      setup 先建幾筆 token（預設 50）
//   RATE      create 場景的 req/s（預設 50）
//   DURATION  create 場景時長（預設 2m）
//   SMOKE     設 1 → 用極小階梯（本機冒煙）
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const JWT = __ENV.JWT || "";
const SEED = parseInt(__ENV.SEED || "50");
const SCENARIO = __ENV.SCENARIO || "redirect";
const SMOKE = !!__ENV.SMOKE;

const jsonHeaders = JWT
  ? { Authorization: `Bearer ${JWT}`, "Content-Type": "application/json" }
  : { "Content-Type": "application/json" };

// redirect 階梯（找 knee）；SMOKE 用極小量
const redirectStages = SMOKE
  ? [{ target: 50, duration: "10s" }]
  : [
      { target: 200, duration: "30s" },
      { target: 1000, duration: "1m" },
      { target: 2000, duration: "1m" },
      { target: 5000, duration: "1m" },
      { target: 10000, duration: "1m" },
    ];

const scenarios = {};
if (SCENARIO === "redirect" || SCENARIO === "mixed") {
  scenarios[SCENARIO] = {
    executor: "ramping-arrival-rate",
    exec: SCENARIO,
    startRate: SMOKE ? 10 : 200,
    timeUnit: "1s",
    preAllocatedVUs: 100,
    maxVUs: SMOKE ? 100 : 3000,
    stages: redirectStages,
    tags: { scenario: SCENARIO },
  };
}
if (SCENARIO === "create") {
  scenarios.create = {
    executor: "constant-arrival-rate",
    exec: "createQR",
    rate: parseInt(__ENV.RATE || (SMOKE ? "5" : "50")),
    timeUnit: "1s",
    duration: __ENV.DURATION || (SMOKE ? "10s" : "2m"),
    preAllocatedVUs: 50,
    maxVUs: 500,
    tags: { scenario: "create" },
  };
}

export const options = {
  scenarios,
  maxRedirects: 0, // 量 302 本身,不追到目標站
  thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    "http_req_duration{scenario:redirect}": ["p(95)<100", "p(99)<200"],
  },
};

export function setup() {
  const tokens = [];
  for (let i = 0; i < SEED; i++) {
    const res = http.post(
      `${BASE}/api/v1/qr/create`,
      JSON.stringify({ url: `https://example.com/seed/${i}` }),
      { headers: jsonHeaders }
    );
    if (res.status === 200) tokens.push(res.json("token"));
  }
  if (tokens.length === 0) {
    throw new Error(`seed 失敗(0 tokens)。檢查 BASE_URL/JWT。狀態碼可能 401(需 JWT)或 403(WAF)`);
  }
  console.log(`seeded ${tokens.length} tokens`);
  return { tokens };
}

function pick(tokens) {
  return tokens[Math.floor(Math.random() * tokens.length)];
}

export function redirect(data) {
  const res = http.get(`${BASE}/r/${pick(data.tokens)}`, {
    redirects: 0,
    tags: { scenario: "redirect" },
  });
  check(res, { "redirect 302": (r) => r.status === 302 });
}

export function createQR() {
  const res = http.post(
    `${BASE}/api/v1/qr/create`,
    JSON.stringify({ url: `https://example.com/load/${__VU}-${__ITER}` }),
    { headers: jsonHeaders, tags: { scenario: "create" } }
  );
  check(res, { "create 200": (r) => r.status === 200 });
}

export function mixed(data) {
  if (Math.random() < 0.05) createQR();
  else redirect(data);
}
