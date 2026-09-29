from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.common.questdb import connect_questdb

log = logging.getLogger(__name__)

TABLE_NAME = 'connection_events'

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    disconnected_at TIMESTAMP,
    reconnected_at TIMESTAMP,
    asset_class SYMBOL,
    flow SYMBOL,
    mode SYMBOL,
    gap_seconds DOUBLE,
    backfill_status SYMBOL,
    status SYMBOL
) TIMESTAMP(disconnected_at) PARTITION BY MONTH WAL
"""


def _naive(v):
    """QuestDB PG wire rejects tz-aware datetimes; send naive UTC instead."""
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


def _execute(sql: str, params: tuple, what: str):
    """Run one statement. Returns rowcount, or None if it failed."""
    conn = connect_questdb()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(_naive(p) for p in params))
        conn.commit()
        rowcount = cur.rowcount
        cur.close()
        return rowcount
    except Exception:
        conn.rollback()
        log.exception('Failed to %s', what)
        return None
    finally:
        conn.close()


def ensure_connection_events_table() -> None:
    """Call once at streamer startup. Idempotent. Adds `status` to older tables."""
    conn = connect_questdb()
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        cur.execute(f"select \"column\" from table_columns('{TABLE_NAME}')")
        existing = {row[0] for row in cur.fetchall()}
        if 'status' not in existing:
            cur.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN status SYMBOL")
            conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        log.exception('Could not ensure %s table', TABLE_NAME)
        raise
    finally:
        conn.close()


def log_connection_open(asset_class: str, flow: str, mode: str, disconnected_at: datetime) -> None:
    """Insert an OPEN row the moment a disconnect is detected."""
    ok = _execute(
        f"INSERT INTO {TABLE_NAME} "
        f"(disconnected_at, reconnected_at, asset_class, flow, mode, gap_seconds, backfill_status, status) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (disconnected_at, None, asset_class, flow, mode, None, 'none', 'OPEN'),
        f'insert OPEN connection event for {asset_class}/{flow}/{mode}',
    )
    if ok is not None:
        log.warning('Connection OPEN event logged for %s/%s/%s at %s', asset_class, flow, mode, disconnected_at)


def log_connection_gap(
    asset_class: str,
    flow: str,
    mode: str,
    disconnected_at: datetime,
    reconnected_at: datetime,
) -> None:
    """Close the OPEN row (status RECOVERED). If no OPEN row exists, insert a full one."""
    gap_seconds = (reconnected_at - disconnected_at).total_seconds()
    rowcount = _execute(
        f"UPDATE {TABLE_NAME} SET reconnected_at = %s, gap_seconds = %s, "
        f"status = 'RECOVERED', backfill_status = 'pending' "
        f"WHERE disconnected_at = %s AND asset_class = %s AND flow = %s AND mode = %s",
        (reconnected_at, gap_seconds, disconnected_at, asset_class, flow, mode),
        f'update connection event for {asset_class}/{flow}/{mode}',
    )
    if rowcount == 0:  # no OPEN row was found -> record the whole outage now
        _execute(
            f"INSERT INTO {TABLE_NAME} "
            f"(disconnected_at, reconnected_at, asset_class, flow, mode, gap_seconds, backfill_status, status) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (disconnected_at, reconnected_at, asset_class, flow, mode, gap_seconds, 'pending', 'RECOVERED'),
            f'insert connection gap for {asset_class}/{flow}/{mode}',
        )
    log.warning(
        'Connection RECOVERED for %s/%s/%s: %.1fs (%s -> %s)',
        asset_class, flow, mode, gap_seconds, disconnected_at, reconnected_at,
    )


def close_stale_open_events(asset_class: str, flow: str, mode: str) -> None:
    """At streamer startup: any OPEN row left by a killed process becomes ABANDONED."""
    _execute(
        f"UPDATE {TABLE_NAME} SET status = 'ABANDONED' "
        f"WHERE status = 'OPEN' AND asset_class = %s AND flow = %s AND mode = %s",
        (asset_class, flow, mode),
        'close stale OPEN connection events',
    )


def mark_backfill_status(
    disconnected_at: datetime,
    asset_class: str,
    flow: str,
    mode: str,
    status: str,
) -> None:
    """status: 'done' | 'failed' | 'partial'."""
    _execute(
        f"UPDATE {TABLE_NAME} SET backfill_status = %s "
        f"WHERE disconnected_at = %s AND asset_class = %s AND flow = %s AND mode = %s",
        (status, disconnected_at, asset_class, flow, mode),
        f'update backfill_status for gap at {disconnected_at}',
    )
