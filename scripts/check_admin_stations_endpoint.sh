#!/usr/bin/env bash
set -euo pipefail

API_BASE="${1:-https://snow-explorer-api.onrender.com}"

probe() {
  local method="$1" path="$2"
  echo "=== ${method} ${API_BASE}${path} ==="
  curl -sS -X "${method}" -D - -o /tmp/resp_body.txt "${API_BASE}${path}" | sed -n '1,20p'
  echo "--- body (first line) ---"
  sed -n '1p' /tmp/resp_body.txt
  if rg -q "Cannot GET|Cannot PATCH|Cannot OPTIONS" /tmp/resp_body.txt; then
    echo "!!! Signature Node/Express détectée (routage probablement vers le mauvais service)."
  fi
  echo
}

for p in /api/admin/stations /api/admin/stations/ /api/admin/resorts/ /api/resorts/; do
  probe GET "$p"
done

probe OPTIONS /api/admin/stations/

echo "Hint:"
echo "- 404 + body 'Cannot GET /api/admin/stations/' => cible non-Flask (ou route absente sur service Node)."
echo "- 204 sur HEAD /admin/stations (www.snow-explorer.com) => réponse normale du FRONT, pas de l'API."
