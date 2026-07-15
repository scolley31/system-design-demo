#!/usr/bin/env bash
# k6 壓測包裝。用法:
#   BASE_URL=https://<cloudfront> JWT=<id_token> ./run.sh redirect
#   BASE_URL=... JWT=... ./run.sh create
#   BASE_URL=... JWT=... ./run.sh mixed
#   SMOKE=1 ./run.sh redirect            # 本機冒煙（對 localhost:8000,AUTH 關閉免 JWT）
set -euo pipefail

SCENARIO="${1:-redirect}"
: "${BASE_URL:=http://localhost:8000}"

echo "== load test: scenario=$SCENARIO base=$BASE_URL smoke=${SMOKE:-0} =="
SCENARIO="$SCENARIO" k6 run "$(dirname "$0")/qr_load.js"
