#!/usr/bin/env bash
# start_ibgateway.sh -- launches a virtual display then IB Gateway via IBC.
#
# Prerequisites (one-time, interactive -- see DEPLOYMENT.md step 4):
#   - IB Gateway installed at $IBGATEWAY_HOME
#   - IBC installed at $IBC_HOME
#   - $IBC_HOME/config.ini filled in with login + trading mode
#   - First login completed once by hand over a VNC session
#
# Authentication recovery:
#   - 2FA timeout -> IBC exits with code 87
#   - launcher waits 5 minutes
#   - launcher starts a new IBC authentication attempt
#
# Important:
#   - Do not change the six Python streamer configurations here.
#   - Do not change IBKR market-data subscriptions here.

set -euo pipefail


# ---------------------------------------------------------------------------
# Authentication state
# ---------------------------------------------------------------------------

AUTH_STATE_FILE="/run/ibkr-pipeline/auth.state"

mkdir -p "$(dirname "$AUTH_STATE_FILE")"

set_auth_state() {
    local state="$1"

    printf 'state=%s\ntimestamp=%s\n' \
        "$state" \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        > "$AUTH_STATE_FILE"
}

clear_auth_state() {
    rm -f "$AUTH_STATE_FILE"
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

export DISPLAY=:99

IBGATEWAY_HOME="/opt/ibkr-pipeline/ibgateway/Jts/ibgateway"
IBC_HOME="/opt/ibkr-pipeline/ibgateway/ibc"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECOVERY_CONFIG="${SCRIPT_DIR}/ibgateway-recovery.conf"


# ---------------------------------------------------------------------------
# Recovery configuration
# ---------------------------------------------------------------------------

if [[ -f "$RECOVERY_CONFIG" ]]; then
    # shellcheck disable=SC1090
    source "$RECOVERY_CONFIG"
fi

TWOFA_TIMEOUT_EXIT_CODE=87

# Default: 5 minutes.
TWOFA_RETRY_WAIT_SECONDS="${IBKR_2FA_NO_RESPONSE_WAIT_SECONDS:-300}"


# ---------------------------------------------------------------------------
# Xvfb cleanup
# ---------------------------------------------------------------------------

XVFB_PID=""

cleanup() {
    clear_auth_state

    if [[ -n "${XVFB_PID}" ]]; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM


# ---------------------------------------------------------------------------
# Start virtual display
# ---------------------------------------------------------------------------

Xvfb "$DISPLAY" -screen 0 1024x768x16 &
XVFB_PID=$!

sleep 2


# ---------------------------------------------------------------------------
# IB Gateway / IBC authentication loop
# ---------------------------------------------------------------------------
#
# IBC handles:
#   - IB Gateway login
#   - 2FA dialog
#   - login/session management
#
# When 2FA times out:
#
#   IBC exit code 1111
#          |
#          v
#   1111 % 256 = 87
#          |
#          v
#   wait 5 minutes
#          |
#          v
#   start IBC again
#
# Successful login does NOT require any special handling here.
# IBC remains running and systemd keeps this service alive.
#

while true; do

    set_auth_state "AUTH_STARTED"

    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] starting IBC authentication"

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


    # -----------------------------------------------------------------------
    # 2FA timeout
    # -----------------------------------------------------------------------

    if [[ "$EXIT_CODE" -eq "$TWOFA_TIMEOUT_EXIT_CODE" ]]; then

        set_auth_state "AUTH_TIMEOUT"

        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] 2FA timed out"

        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] waiting ${TWOFA_RETRY_WAIT_SECONDS}s before retry"

        sleep "$TWOFA_RETRY_WAIT_SECONDS"

        set_auth_state "AUTH_RETRY"

        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] retrying IBC authentication"

        continue
    fi


    # -----------------------------------------------------------------------
    # Any other IBC exit
    # -----------------------------------------------------------------------

    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') [ibgateway] IBC exited with code ${EXIT_CODE}"

    exit "$EXIT_CODE"

done
