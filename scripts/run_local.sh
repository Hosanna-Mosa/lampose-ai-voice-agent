#!/usr/bin/env bash
# =====================================================================
# LAMPOSE Voice AI — local test runner (macOS)
#  1. starts MongoDB (brew service)
#  2. starts a Cloudflare quick tunnel -> gets a public https URL
#  3. writes that URL into .env.local
#  4. points the Twilio number's inbound webhook at it
#  5. starts the server (logs stream in this terminal)
# Re-run any time; safe to Ctrl-C (tunnel is cleaned up).
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3.12; command -v $PY >/dev/null || PY=python3.11
echo "[LOCAL] using $($PY --version)"

# --- venv ---
if [ ! -d venv ]; then
  echo "[LOCAL] creating venv + installing dependencies (first run only)…"
  $PY -m venv venv
  ./venv/bin/pip install -q --upgrade pip
  ./venv/bin/pip install -q -r requirements.txt
fi

# --- MongoDB ---
if ! pgrep -x mongod >/dev/null; then
  echo "[LOCAL] starting MongoDB…"
  MONGO_FORMULA=$(brew list --formula | grep -m1 '^mongodb-community' || true)
  brew services start "${MONGO_FORMULA:-mongodb-community}" >/dev/null
  sleep 2
fi
echo "[LOCAL] MongoDB running"

# --- Cloudflare quick tunnel ---
# kill any leftover tunnel/server first — duplicates destabilize the edge
pkill -f "cloudflared tunnel" 2>/dev/null || true
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
TUNNEL_LOG=$(mktemp /tmp/cf-tunnel.XXXX.log)
cloudflared tunnel --url http://localhost:7860 --protocol http2 --edge-ip-version 4 --no-autoupdate >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
trap 'kill $TUNNEL_PID 2>/dev/null || true' EXIT
echo "[LOCAL] waiting for tunnel URL…"
URL=""
for i in $(seq 1 30); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "!! tunnel failed — see $TUNNEL_LOG"; exit 1; }
echo "[LOCAL] tunnel up: $URL"

# --- write .env.local ---
sed -i '' "s|^SERVER_URL=.*|SERVER_URL=$URL|" .env.local
echo "[LOCAL] SERVER_URL written to .env.local"

# --- point Twilio inbound webhook here ---
./venv/bin/python - <<PYEOF2
from dotenv import load_dotenv; load_dotenv(".env"); load_dotenv(".env.local", override=True)
import os
from twilio.rest import Client
c = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
nums = c.incoming_phone_numbers.list(phone_number=os.environ["TWILIO_NUMBER"])
if nums:
    nums[0].update(voice_url=os.environ["SERVER_URL"] + "/twiml/inbound", voice_method="POST")
    print(f"[LOCAL] Twilio inbound webhook -> {os.environ['SERVER_URL']}/twiml/inbound")
else:
    print("[LOCAL] !! Twilio number not found on this account")
PYEOF2

echo "[LOCAL] starting server — dashboard: $URL  (also http://localhost:7860)"
echo "===================================================================="
exec ./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 7860
