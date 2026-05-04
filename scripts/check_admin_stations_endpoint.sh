#!/usr/bin/env bash
set -euo pipefail

API_BASE="${1:-https://snow-explorer-api.onrender.com}"

for path in /api/admin/stations/ /api/admin/resorts/ /api/resorts/; do
  echo "=== ${API_BASE}${path} ==="
  curl -sS -D - -o /tmp/resp_body.txt "${API_BASE}${path}" | sed -n '1,20p'
  echo "--- body (first line) ---"
  sed -n '1p' /tmp/resp_body.txt
  echo
 done

echo "Hint: if body contains 'Cannot GET ...', the target is very likely an Express/Node app, not this Flask API."
