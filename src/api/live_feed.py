from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from psycopg2.extras import RealDictCursor

from src.common.questdb import connect_questdb
from src.common.tables import TABLES

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0
SUBSCRIBER_QUEUE_MAXSIZE = 100


class _FeedChannel:
    """One channel = one (asset_group, mode) pair, e.g. ('commodity', 'live').

    Owns exactly one polling task and a set of subscriber queues. The first
    subscriber starts the poll loop; the last one leaving stops it -- so
    asset classes nobody is currently watching cost nothing.

    Subscribers are plain asyncio.Queue objects. The WebSocket route is one
    kind of subscriber. Anything else that wants the same live feed later
    (another internal service, a future Kafka/Redis bridge that feeds a
    second database, etc.) subscribes the exact same way via
    LiveFeedHub.subscribe() below -- this class doesn't know or care what's
    on the other end of the queue.
    """

    def __init__(self, asset_group: str, mode: str, table_name: str):
        self.asset_group = asset_group
        self.mode = mode
        self.table_name = table_name
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._last_seen: dict[str, Any] = {}  # symbol -> last quote_time broadcast

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
        self._subscribers.add(q)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
            log.info('Started poll loop for %s/%s', self.asset_group, self.mode)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
        if not self._subscribers and self._task is not None:
            self._task.cancel()
            self._task = None
            log.info('Stopped poll loop for %s/%s (no subscribers left)', self.asset_group, self.mode)

    def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _fetch_latest(self) -> list[dict[str, Any]]:
        """Blocking QuestDB call -- run via asyncio.to_thread from the poll
        loop so it never blocks the event loop other requests share."""
        conn = connect_questdb()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f'SELECT * FROM {self.table_name} LATEST ON quote_time PARTITION BY symbol'
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    async def _poll_loop(self) -> None:
        try:
            while True:
                try:
                    rows = await asyncio.to_thread(self._fetch_latest)
                except Exception:
                    log.exception('Poll failed for %s/%s', self.asset_group, self.mode)
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                for row in rows:
                    symbol = row.get('symbol')
                    ts = row.get('quote_time')
                    # Skip symbols whose latest tick hasn't changed since we
                    # last broadcast -- avoids resending an unchanged price
                    # every second just because the poll ran again.
                    if symbol is not None and self._last_seen.get(symbol) == ts:
                        continue
                    self._last_seen[symbol] = ts
                    self._broadcast(row)

                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass

    def _broadcast(self, row: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(row)
            except asyncio.QueueFull:
                log.warning(
                    'Subscriber queue full for %s/%s -- dropping a tick for a slow consumer',
                    self.asset_group, self.mode,
                )


class LiveFeedHub:
    """Process-wide registry of _FeedChannel objects, one per (asset_group,
    mode). Create ONE instance at app startup (see app.py) and share it
    across every router that wants live pushes -- the WebSocket route is
    the first consumer, not the only one it's designed for."""

    def __init__(self):
        self._channels: dict[tuple[str, str], _FeedChannel] = {}

    def subscribe(self, asset_group: str, mode: str) -> asyncio.Queue:
        table_name = TABLES.get((asset_group, mode))
        if table_name is None:
            raise ValueError(f'No table for asset_group={asset_group!r}, mode={mode!r}')

        key = (asset_group, mode)
        channel = self._channels.get(key)
        if channel is None:
            channel = _FeedChannel(asset_group, mode, table_name)
            self._channels[key] = channel
        return channel.subscribe()

    def unsubscribe(self, asset_group: str, mode: str, q: asyncio.Queue) -> None:
        channel = self._channels.get((asset_group, mode))
        if channel is not None:
            channel.unsubscribe(q)

    def shutdown(self) -> None:
        """Called on app shutdown so no poll task is left dangling."""
        for channel in self._channels.values():
            channel.shutdown()


# One shared hub for the whole process -- imported by app.py and by any
# router (live_ws.py now, others later) that needs to subscribe/unsubscribe.
hub = LiveFeedHub()