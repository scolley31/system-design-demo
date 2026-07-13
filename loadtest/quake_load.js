// Earthquake Notification System 壓測（k6）。場景由 env SCENARIO 切換。
//
//   BASE_URL  對外網址（雲端=CloudFront URL；本機=http://localhost:8010）
//   SCENARIO  location | quake
//             - location：位置回報 write path（對齊需求 ≈ 5.5K writes/s）
//             - quake：觸發地震 fan-out（少量高扇出，量 broadcast 延遲）
//   RES       H3 解析度（需與 server 一致，預設 5）
//   SEED      quake 場景先註冊幾台裝置（預設 500）
//   RATE      location 場景的 writes/s（預設 5500）
//   DURATION  場景時長（預設 1m）
//   SMOKE     設 1 → 用極小量（本機冒煙）
//
// 註：k6 無 H3 函式庫，改用「預先算好的 cell 池」餵給 user_location（模擬前端已算好 cell 直送）。
//     cell 池由 setup 用一組台灣座標近似格點產生（純字串，非真 H3；write path 壓測夠用）。
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8010";
const SCENARIO = __ENV.SCENARIO || "location";
const SMOKE = !!__ENV.SMOKE;
const SEED = parseInt(__ENV.SEED || "500");

const jsonHeaders = { "Content-Type": "application/json" };

// 一組假 cell 池（台灣範圍，字串前綴相近以模擬空間聚集）。真實前端送的是 h3-js 算的 cell。
const CELLS = [];
for (let i = 0; i < 200; i++) CELLS.push(`85${(0x4ba0 + i).toString(16)}fffffff`);
const pick = (a) => a[Math.floor(Math.random() * a.length)];

const scenarios = {};
if (SCENARIO === "location") {
  scenarios.location = {
    executor: "constant-arrival-rate",
    exec: "reportLocation",
    rate: parseInt(__ENV.RATE || (SMOKE ? "50" : "5500")), // 對齊 5.5K writes/s
    timeUnit: "1s",
    duration: __ENV.DURATION || (SMOKE ? "10s" : "1m"),
    preAllocatedVUs: SMOKE ? 50 : 800,
    maxVUs: SMOKE ? 100 : 3000,
    tags: { scenario: "location" },
  };
}
if (SCENARIO === "quake") {
  scenarios.quake = {
    executor: "constant-arrival-rate",
    exec: "triggerQuake",
    rate: parseInt(__ENV.RATE || "5"),
    timeUnit: "1s",
    duration: __ENV.DURATION || (SMOKE ? "10s" : "30s"),
    preAllocatedVUs: 20,
    maxVUs: 100,
    tags: { scenario: "quake" },
  };
}

export const options = {
  scenarios,
  thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    "http_req_duration{scenario:location}": ["p(95)<50", "p(99)<100"],
  },
};

export function setup() {
  if (SCENARIO !== "quake") return {};
  // 註冊一批裝置（config + location）供 quake fan-out 命中
  let ok = 0;
  for (let i = 0; i < SEED; i++) {
    const did = `load-${i}`;
    const c1 = http.post(`${BASE}/api/v1/alerts/configuration`,
      JSON.stringify({ device_id: did, magnitude_min: 3, distance_km: 500 }), { headers: jsonHeaders });
    const c2 = http.post(`${BASE}/api/v1/alerts/user_location`,
      JSON.stringify({ device_id: did, cell: pick(CELLS) }), { headers: jsonHeaders });
    if (c1.status === 200 && c2.status === 200) ok++;
  }
  console.log(`seeded ${ok}/${SEED} devices`);
  return {};
}

export function reportLocation() {
  const did = `dev-${__VU}-${__ITER}`;
  const res = http.post(`${BASE}/api/v1/alerts/user_location`,
    JSON.stringify({ device_id: did, cell: pick(CELLS) }),
    { headers: jsonHeaders, tags: { scenario: "location" } });
  check(res, { "location 200": (r) => r.status === 200 });
}

export function triggerQuake() {
  const res = http.post(`${BASE}/api/v1/earthquakes`,
    JSON.stringify({ lat: 25.03, long: 121.56, magnitude: 6.5, alert_id: `load-${__VU}-${__ITER}`, version: 1 }),
    { headers: jsonHeaders, tags: { scenario: "quake" } });
  check(res, { "quake 200": (r) => r.status === 200 });
}
