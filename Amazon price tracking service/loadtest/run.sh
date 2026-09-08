#!/usr/bin/env bash
# k6 壓測包裝。用法:
#   ./run.sh history                       # 價格歷史讀路徑（NFR p95 < 500ms）
#   ./run.sh report                        # extension 眾包回報寫入
#   ./run.sh subscribe                     # 訂閱寫入
#   SMOKE=1 ./run.sh history               # 本機冒煙（對 localhost:8020,server 需 MOCK_AMAZON=1）
#   RATE=500 DURATION=3m ./run.sh history  # 自訂壓力
#   BASE_URL=https://<cloudfront> ./run.sh history
# 第 2 個參數起原樣傳給 k6（例如 --summary-export=out.json）。
set -euo pipefail

SCENARIO="${1:-history}"
shift || true
case "$SCENARIO" in history|report|subscribe) ;; *)
  echo "usage: $0 <history|report|subscribe> [extra k6 args]" >&2; exit 2;; esac
: "${BASE_URL:=http://localhost:8020}"
export BASE_URL

echo "== load test: scenario=$SCENARIO base=$BASE_URL smoke=${SMOKE:-0} rate=${RATE:-default} duration=${DURATION:-default} seed=${SEED:-default} =="
# SMOKE / RATE / DURATION / SEED 走環境變數,k6 由 __ENV 讀取(未設就用腳本預設)
SCENARIO="$SCENARIO" k6 run "$@" "$(dirname "$0")/price_load.js"
