from __future__ import annotations

import logging

from ib_insync import IB

from src.common.settings import (
    IB_CONNECT_TIMEOUT_SECONDS,
    IB_HOST,
    IB_PORT,
    IB_RECONNECT_MAX_BACKOFF_SECONDS,
    IB_RECONNECT_MIN_BACKOFF_SECONDS,
)

log = logging.getLogger(__name__)

MARKET_DATA_TYPES = {
    'live': 1,
    'delayed': 3,
}


class IBConnectionError(RuntimeError):
    """Raised when a connection attempt to IB Gateway/TWS fails."""


def connect_ib(client_id: int) -> IB:
    """Single connection attempt. Raises IBConnectionError on failure.

    Callers that need resilience should use connect_ib_with_retry instead --
    this is kept simple/explicit for callers (e.g. tests) that want to handle
    failure themselves.
    """
    ib = IB()
    try:
        ib.connect(IB_HOST, IB_PORT, clientId=client_id, timeout=IB_CONNECT_TIMEOUT_SECONDS)
    except Exception as exc:
        raise IBConnectionError(
            f'Could not connect to IB Gateway/TWS at {IB_HOST}:{IB_PORT} '
            f'(clientId={client_id}): {exc}. '
            f'Confirm TWS/Gateway is running, logged in, and API access is '
            f'enabled for this host.'
        ) from exc
    return ib


def connect_ib_with_retry(client_id: int, max_attempts: int | None = None) -> IB:
    """Retries with exponential backoff until connected (or max_attempts hit).

    max_attempts=None retries forever -- appropriate for a long-running
    streamer process where the only alternative is a dead container.
    """
    attempt = 0
    backoff = IB_RECONNECT_MIN_BACKOFF_SECONDS
    while True:
        attempt += 1
        try:
            ib = connect_ib(client_id)
            log.info('Connected to IB Gateway (clientId=%s) on attempt %s', client_id, attempt)
            return ib
        except IBConnectionError as exc:
            if max_attempts is not None and attempt >= max_attempts:
                raise
            log.warning(
                'IB connect attempt %s failed: %s. Retrying in %.1fs',
                attempt, exc, backoff,
            )
            ib_sleep_blocking(backoff)
            backoff = min(backoff * 2, IB_RECONNECT_MAX_BACKOFF_SECONDS)


def ib_sleep_blocking(seconds: float) -> None:
    """Plain time.sleep, kept as its own function so it's obvious this is
    used only *before* an IB event loop exists (i.e. pre-connection), never
    once ib.sleep() is available."""
    import time
    time.sleep(seconds)
