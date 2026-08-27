#!/usr/bin/env bash
# =============================================================
# LAMPOSE Voice AI — one-shot VPS setup (Ubuntu 22.04 / 24.04)
# Run as root from inside the project directory:
#   sudo bash deploy/setup_vps.sh
# Prereq: DNS A-record  voice.lampose.in -> this VPS IP
# =============================================================
set -euo pipefail

DOMAIN="${DOMAIN:-voice.lampose.in}"
APP_DIR=/opt/lampose-voice
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Installing system packages"
apt-get update -y
apt-get install -y curl gnupg nginx certbot python3-certbot-nginx \
  build-essential software-properties-common ufw

# ---------- Python >= 3.11 ----------
PYBIN=""
for v in 3.12 3.11; do
  if command -v python$v >/dev/null 2>&1; then PYBIN=python$v; break; fi
done
if [ -z "$PYBIN" ]; then
  echo "==> Installing Python 3.11 (deadsnakes)"
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -y
  apt-get install -y python3.11 python3.11-venv python3.11-dev
  PYBIN=python3.11
else
  apt-get install -y ${PYBIN}-venv ${PYBIN}-dev || true
fi
echo "==> Using $PYBIN"

# ---------- MongoDB 7.0 ----------
if ! systemctl is-active --quiet mongod; then
  echo "==> Installing MongoDB 7.0"
  . /etc/os-release
  curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
    gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg --yes
  echo "deb [signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] \
https://repo.mongodb.org/apt/ubuntu ${VERSION_CODENAME}/mongodb-org/7.0 multiverse" \
    > /etc/apt/sources.list.d/mongodb-org-7.0.list
  apt-get update -y
  apt-get install -y mongodb-org
  systemctl enable --now mongod
fi

# ---------- App user + files ----------
id -u lampose >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin lampose
mkdir -p "$APP_DIR"
rsync -a --delete --exclude venv --exclude .git "$SRC_DIR"/ "$APP_DIR"/ 2>/dev/null || \
  cp -r "$SRC_DIR"/. "$APP_DIR"/
chown -R lampose:lampose "$APP_DIR"
chmod 600 "$APP_DIR/.env" || true

echo "==> Python venv + dependencies (takes a few minutes)"
sudo -u lampose $PYBIN -m venv "$APP_DIR/venv"
sudo -u lampose "$APP_DIR/venv/bin/pip" install --upgrade pip wheel
sudo -u lampose "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ---------- systemd ----------
cp "$APP_DIR/deploy/lampose-voice.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable lampose-voice

# ---------- nginx + TLS ----------
cp "$APP_DIR/deploy/nginx-lampose-voice.conf" /etc/nginx/sites-available/lampose-voice
sed -i "s/voice.lampose.in/$DOMAIN/g" /etc/nginx/sites-available/lampose-voice
ln -sf /etc/nginx/sites-available/lampose-voice /etc/nginx/sites-enabled/
# NOTE: existing sites (the LAMPOSE Node/Express app) are left untouched —
# this only ADDS a server block for the voice subdomain.
nginx -t && systemctl reload nginx

echo "==> Requesting TLS certificate for $DOMAIN"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
  -m dev.hosanna@lampose.in --redirect || {
  echo "!! certbot failed — check that DNS $DOMAIN points to this server, then run:";
  echo "   certbot --nginx -d $DOMAIN"; }

# ---------- firewall ----------
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 'Nginx Full' >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

systemctl restart lampose-voice
sleep 3
systemctl --no-pager status lampose-voice | head -12

echo ""
echo "============================================================"
echo " DONE. Dashboard:  https://$DOMAIN   (login from .env)"
echo " Set Twilio number Voice webhook to:"
echo "   https://$DOMAIN/twiml/inbound   (HTTP POST)"
echo " Logs:  journalctl -u lampose-voice -f"
echo "============================================================"
