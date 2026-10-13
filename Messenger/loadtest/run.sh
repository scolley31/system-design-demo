#!/usr/bin/env bash
# k6 WebSocket 壓測包裝。用法:
#   ./run.sh chat                                  # 200 VU / 20 群 / 每 2s 發一則 / 1m（NFR 送達 p95 < 500ms）
#   SMOKE=1 ./run.sh chat                          # 本機冒煙（10 VU、2 群、20s）
#   VUS=60 CHATS=6 DURATION=30s ./run.sh chat      # 自訂壓力
#   DURATION=2m ./run.sh failover                  # 跑的同時 docker compose kill/stop pubsub-1（見 README）
#   BASE_URL=wss://<cloudfront> ./run.sh chat
# 第 2 個參數起原樣傳給 k6（例如 --summary-export=out.json）。
set -euo pipefail

SCENARIO="${1:-chat}"
shift || true
case "$SCENARIO" in chat|failover) ;; *)
  echo "usage: $0 <chat|failover> [extra k6 args]" >&2; exit 2;; esac
: "${BASE_URL:=ws://localhost:8030}"
export BASE_URL

echo "== $(k6 version | head -1) =="
echo "== load test: scenario=$SCENARIO base=$BASE_URL smoke=${SMOKE:-0} vus=${VUS:-default} chats=${CHATS:-default} interval=${INTERVAL:-default} duration=${DURATION:-default} =="
# SMOKE / VUS / CHATS / INTERVAL / DURATION / RUN_ID 走環境變數,k6 由 __ENV 讀取(未設就用腳本預設)
SCENARIO="$SCENARIO" k6 run "$@" "$(dirname "$0")/chat_load.js"
