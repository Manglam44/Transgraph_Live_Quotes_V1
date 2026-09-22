#!/usr/bin/env bash
# start_ibgateway.sh -- launches a virtual display then IB Gateway via IBC.
#
# Prerequisites (one-time, interactive -- see DEPLOYMENT.md step 4):
#   - IB Gateway installed at $IBGATEWAY_HOME (IBKR's official Linux
#     offline installer)
#   - IBC installed at $IBC_HOME (https://github.com/IbcAlpha/IBC)
#   - $IBC_HOME/config.ini filled in with your login + trading mode
#   - First login done once by hand over a VNC session, to clear any
#     one-time prompts (and to approve 2FA if your account uses it)
#
# Adjust the paths below to match your actual install locations/versions.
set -euo pipefail

export DISPLAY=:99
IBGATEWAY_HOME="/opt/ibkr-pipeline/ibgateway/Jts/ibgateway"   # contains a version-numbered subdir
IBC_HOME="/opt/ibkr-pipeline/ibgateway/ibc"

# Xvfb: virtual framebuffer so the GUI app has a "screen" to draw to
Xvfb "$DISPLAY" -screen 0 1024x768x16 &
XVFB_PID=$!
trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT
sleep 2

# IBC drives IB Gateway's login/2FA/dialog-dismissal automatically using
# config.ini, and restarts Gateway itself on the ~23:45 ET daily reset.
"$IBC_HOME/scripts/ibcstart.sh" \
    --gateway \
    --mode=live \
    --tws-path="$IBGATEWAY_HOME" \
    --ibc-path="$IBC_HOME" \
    --ibc-ini="$IBC_HOME/config.ini"

# ibcstart.sh blocks until Gateway exits; when it does, let systemd decide
# whether to restart (see ibgateway.service's Restart=).
