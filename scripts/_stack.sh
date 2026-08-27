#!/usr/bin/env bash
# background local stack: tunnel -> .env.local -> Twilio webhook -> server
set -uo pipefail
cd "$(dirname "$0")/.."
pkill -f "cloudflared tunnel --url http://localhost:7860" 2>/dev/null || true
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1

cloudflared tunnel --url http://localhost:7860 --protocol http2 --edge-ip-version 4 --no-autoupdate > logs/tunnel.log 2>&1 &
echo "[STACK] cloudflared started (pid $!)"

URL=""
for i in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
if [ -z "$URL" ]; then echo "[STACK-ERROR] tunnel gave no URL"; exit 1; fi
echo "[STACK] tunnel URL: $URL"

sed -i '' "s|^SERVER_URL=.*|SERVER_URL=$URL|" .env.local
echo "[STACK] .env.local updated"

./venv/bin/python - <<PY
from dotenv import load_dotenv; load_dotenv(".env"); load_dotenv(".env.local", override=True)
import os
from twilio.rest import Client
c = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
nums = c.incoming_phone_numbers.list(phone_number=os.environ["TWILIO_NUMBER"])
if nums:
    nums[0].update(voice_url=os.environ["SERVER_URL"] + "/twiml/inbound", voice_method="POST")
    print("[STACK] Twilio inbound webhook ->", os.environ["SERVER_URL"] + "/twiml/inbound")
else:
    print("[STACK-ERROR] Twilio number not found on account")
PY

echo "[STACK] READY $URL — starting server"
exec ./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 7860 >> logs/server.log 2>&1
