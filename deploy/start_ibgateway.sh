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

# IBC drives IB Gateway's login/2FA/dialog-dismissal automatically.
#
# IBC returns exit code 87 when the second-factor authentication dialog
# times out (1111 % 256). We wait 5 minutes before starting a new
# authentication attempt.

TWOFA_TIMEOUT_EXIT_CODE=87

RECOVERY_CONFIG="$(dirname "$0")/ibgateway-recovery.conf"

if [[ -f "$RECOVERY_CONFIG" ]]; then
    # shellcheck disable=SC1090
    source "$RECOVERY_CONFIG"
fi

TWOFA_RETRY_WAIT_SECONDS="${IBKR_2FA_NO_RESPONSE_WAIT_SECONDS:-300}"

while true; do
    set +e

    "$IBC_HOME/scripts/ibcstart.sh" \
        1045 \
        --gateway \
        --mode=live \
        --tws-path="$IBGATEWAY_HOME" \
        --ibc-path="$IBC_HOME" \
        --ibc-ini="$IBC_HOME/config.ini"

    EXIT_CODE=$?

    set -e

    if [[ "$EXIT_CODE" -eq "$TWOFA_TIMEOUT_EXIT_CODE" ]]; then
        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] 2FA timed out; waiting ${TWOFA_RETRY_WAIT_SECONDS}s before retry"
        sleep "$TWOFA_RETRY_WAIT_SECONDS"
        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] retrying IBC authentication"
        continue
    fi

    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] IBC exited with code ${EXIT_CODE}; returning to systemd"
    exit "$EXIT_CODE"
done
