#!/usr/bin/env bash
# 00_ec2_bootstrap.sh -- run ONCE as root (sudo) on a fresh EC2 instance
# (tested assumptions: Ubuntu 22.04/24.04 LTS). Installs everything except
# IB Gateway itself, which needs one interactive step -- see DEPLOYMENT.md
# step 4.
#
# Usage:
#   sudo bash 00_ec2_bootstrap.sh
set -euo pipefail

QUESTDB_VERSION="8.1.4"     # matches the image tag in the old docker-compose.yml
BASE_DIR="/opt/ibkr-pipeline"
SERVICE_USER="ibkr"
LOG_DIR="/var/log/ibkr-pipeline"

echo "== 1/6: system packages =="
apt-get update -y
apt-get install -y \
    wget curl unzip git \
    python3-venv python3-pip \
    openjdk-17-jre-headless \
    xvfb x11vnc \
    net-tools

echo "== 2/6: service user + directories =="
id -u "$SERVICE_USER" &>/dev/null || useradd -m -s /bin/bash "$SERVICE_USER"
mkdir -p "$BASE_DIR"/{app,questdb,questdb-data,ibgateway,venv}
mkdir -p "$LOG_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR" "$LOG_DIR"

echo "== 3/6: QuestDB (native binary, no Docker) =="
if [ ! -f "$BASE_DIR/questdb/questdb.sh" ]; then
    cd /tmp
    wget -q "https://github.com/questdb/questdb/releases/download/${QUESTDB_VERSION}/questdb-${QUESTDB_VERSION}-no-jre-bin.tar.gz" \
        -O questdb.tar.gz
    tar -xzf questdb.tar.gz -C "$BASE_DIR/questdb" --strip-components=1
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$BASE_DIR/questdb"
    chmod +x "$BASE_DIR/questdb/questdb.sh"
else
    echo "   already installed, skipping"
fi

echo "== 4/6: Python venv =="
sudo -u "$SERVICE_USER" python3 -m venv "$BASE_DIR/venv"
echo "   (requirements.txt will be installed in step 5 after you copy the app code)"

echo "== 5/6: systemd unit files =="
cp questdb.service /etc/systemd/system/questdb.service
cp ibgateway.service /etc/systemd/system/ibgateway.service
cp ibkr-orchestrator.service /etc/systemd/system/ibkr-orchestrator.service
systemctl daemon-reload

echo "== 6/6: done =="
cat <<'EOF'

Bootstrap finished. Remaining steps are in DEPLOYMENT.md, in order:

  1. Copy the whole app/ tree (src/, orchestrator.py, runner.py,
     requirements.txt, scripts/, deploy/) into /opt/ibkr-pipeline/app
     (as the 'ibkr' user, or chown afterward)
  2. sudo -u ibkr /opt/ibkr-pipeline/venv/bin/pip install -r \
       /opt/ibkr-pipeline/app/requirements.txt
  3. Copy _env.example to /opt/ibkr-pipeline/app/.env and edit:
       - set real QDB_PASSWORD / API_KEYS
     (IB_HOST/QDB_HOST are already 127.0.0.1 in the template)
  4. Install IB Gateway + IBC (ONE interactive step, needs a VNC session
     for the first login / 2FA approval) -- see DEPLOYMENT.md.
  5. systemctl enable --now questdb
     systemctl enable --now ibgateway
     systemctl enable --now ibkr-orchestrator
EOF
