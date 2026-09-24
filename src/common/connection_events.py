from __future__ import annotations

import logging
from datetime import datetime

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
    backfill_status SYMBOL
) TIMESTAMP(disconnected_at) PARTITION BY MONTH WAL
"""


def ensure_connection_events_table() -> None:
    """Call once at streamer startup. Cheap / idempotent."""
    conn = connect_questdb()
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        log.exception('Could not ensure %s table', TABLE_NAME)
        raise
    finally:
        conn.close()


def log_connection_gap(
    asset_class: str,
    flow: str,
    mode: str,
    disconnected_at: datetime,
    reconnected_at: datetime,
) -> None:
    """Record one completed outage.

    Called ONLY after a successful reconnect, i.e. once both timestamps are
    known. This deliberately avoids an "open row that gets UPDATEd later"
    design: if the process is killed mid-outage there is simply no row for
    that boundary (rather than a permanently-open one lying around), and the
    next reconnect after restart will log whatever gap it actually observes.
    """
    gap_seconds = (reconnected_at - disconnected_at).total_seconds()
    conn = connect_questdb()
    try:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO {TABLE_NAME} "
            f"(disconnected_at, reconnected_at, asset_class, flow, mode, gap_seconds, backfill_status) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (disconnected_at, reconnected_at, asset_class, flow, mode, gap_seconds, 'pending'),
        )
        conn.commit()
        cur.close()
        log.warning(
            'Connection gap logged for %s/%s/%s: %.1fs (%s -> %s)',
            asset_class, flow, mode, gap_seconds, disconnected_at, reconnected_at,
        )
    except Exception:
        conn.rollback()
        log.exception('Failed to record connection gap for %s/%s/%s', asset_class, flow, mode)
    finally:
        conn.close()


def mark_backfill_status(
    disconnected_at: datetime,
    asset_class: str,
    flow: str,
    mode: str,
    status: str,
) -> None:
    """status: 'done' | 'failed' | 'partial'. QuestDB supports UPDATE on WAL
    tables; this table is low-volume so the cost is irrelevant."""
    conn = connect_questdb()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {TABLE_NAME} SET backfill_status = %s "
            f"WHERE disconnected_at = %s AND asset_class = %s AND flow = %s AND mode = %s",
            (status, disconnected_at, asset_class, flow, mode),
        )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        log.exception('Failed to update backfill_status for gap at %s', disconnected_at)
    finally:
        conn.close()