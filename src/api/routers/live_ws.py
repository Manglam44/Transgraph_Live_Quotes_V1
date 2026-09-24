from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.api.live_feed import hub
from src.common.settings import API_KEYS
from src.common.tables import TABLES

log = logging.getLogger(__name__)

router = APIRouter(tags=['live'])


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Datetime objects aren't JSON-serializable by default -- convert the
    same two timestamp fields questdb_api.py treats specially."""
    out = dict(row)
    for field in ('quote_time', 'ticker_time'):
        value = out.get(field)
        if value is not None and hasattr(value, 'isoformat'):
            out[field] = value.isoformat()
    return out


@router.websocket('/ws/{asset_group}')
async def live_ws(
    websocket: WebSocket,
    asset_group: str,
    mode: str = Query('live'),
    api_key: str | None = Query(default=None, alias='api_key'),
):
    """Push-based live feed -- connect once, then just receive.

    Browsers cannot send custom headers on a WebSocket handshake, so unlike
    the REST routes in questdb_api.py (which use the X-API-Key header), the
    key here is passed as a query parameter on the connection URL:

        wss://yourapi.com/ws/commodity?mode=live&api_key=YOUR_KEY

    This is otherwise the same auth check as require_api_key() in auth.py,
    just adapted to where a WS client can actually put the key.
    """
    if not API_KEYS or api_key not in API_KEYS:
        await websocket.close(code=4401)  # custom close code = unauthorized
        return

    if (asset_group, mode) not in TABLES:
        await websocket.close(code=4404)  # custom close code = unknown table
        return

    await websocket.accept()
    queue = hub.subscribe(asset_group, mode)

    try:
        while True:
            row = await queue.get()
            await websocket.send_json(_serialize(row))
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(asset_group, mode, queue)