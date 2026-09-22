from __future__ import annotations

import logging
from datetime import timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from src.api.auth import require_api_key
from src.common.questdb import connect_questdb
from src.common.tables import TABLES

log = logging.getLogger(__name__)

Mode = Literal['live', 'delayed']

# ============================================================
# Timezone configuration
# ============================================================

IST = ZoneInfo('Asia/Kolkata')

_TS_FIELDS = (
    'quote_time',
    'ticker_time',
)


class SymbolRequest(BaseModel):
    symbol: str
    mode: Mode


router = APIRouter(
    prefix='/questdb',
    tags=['questdb'],
    dependencies=[Depends(require_api_key)],
)


# ============================================================
# Timestamp conversion
# ============================================================

def _to_ist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    QuestDB stores TIMESTAMP values without timezone information.

    Our application convention is:
        - QuestDB TIMESTAMP = UTC
        - API response = Asia/Kolkata (+05:30)

    Example:

        QuestDB:
            2026-09-21 10:58:56.599616

        API:
            2026-09-21T16:28:56.599616+05:30
    """

    for row in rows:
        for field in _TS_FIELDS:
            value = row.get(field)

            if value is None:
                continue

            # QuestDB returns a naive datetime.
            # We explicitly interpret it as UTC.
            utc_value = value.replace(tzinfo=timezone.utc)

            # Convert UTC -> Asia/Kolkata
            ist_value = utc_value.astimezone(IST)

            # Return an explicit ISO-8601 timestamp containing +05:30
            row[field] = ist_value.isoformat()

    return rows


# ============================================================
# QuestDB query helper
# ============================================================

def _run_query(
    sql: str,
    params: tuple,
) -> list[dict[str, Any]]:
    conn = None

    try:
        conn = connect_questdb()

        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        return [dict(row) for row in rows]

    except Exception:
        log.exception(
            'QuestDB query failed: %s',
            sql,
        )

        raise HTTPException(
            status_code=502,
            detail='Query against QuestDB failed.',
        )

    finally:
        if conn is not None:
            conn.close()


# ============================================================
# Table resolver
# ============================================================

def _resolve_table(
    asset_group: str,
    mode: str,
) -> str:
    table_name = TABLES.get(
        (asset_group, mode)
    )

    if table_name is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No table for asset_group={asset_group!r}, "
                f"mode={mode!r}. "
                f"Available combinations: "
                f"{sorted(TABLES.keys())}"
            ),
        )

    return table_name


# ============================================================
# List available QuestDB tables
# ============================================================

@router.get('/tables')
def list_tables() -> dict[str, str]:
    return {
        f'{asset_group}:{mode}': table
        for (asset_group, mode), table in TABLES.items()
    }


# ============================================================
# Generic QuestDB endpoint
# ============================================================

@router.get('/{asset_group}')
def get_questdb_table(
    asset_group: str,
    mode: Mode = Query(
        ...,
        description="Which table to read: 'live' or 'delayed'.",
    ),
    limit: int = Query(
        1000,
        ge=1,
        le=10000,
        description='Maximum number of rows to return.',
    ),
    start_time: str | None = Query(
        None,
        description='ISO timestamp to filter quote_time >= start_time.',
    ),
    end_time: str | None = Query(
        None,
        description='ISO timestamp to filter quote_time <= end_time.',
    ),
    symbol: str | None = Query(
        None,
        description='Filter by symbol/pair.',
    ),
) -> list[dict[str, Any]]:

    table_name = _resolve_table(
        asset_group,
        mode,
    )

    sql = f'SELECT * FROM {table_name}'

    params: list[Any] = []
    filters: list[str] = []

    # --------------------------------------------------------
    # Time filters
    # --------------------------------------------------------

    if start_time is not None:
        filters.append(
            'quote_time >= %s'
        )
        params.append(start_time)

    if end_time is not None:
        filters.append(
            'quote_time <= %s'
        )
        params.append(end_time)

    # --------------------------------------------------------
    # Symbol filter
    # --------------------------------------------------------

    if symbol is not None:
        filters.append(
            '(symbol = %s OR pair = %s)'
        )
        params.extend([
            symbol,
            symbol,
        ])

    # --------------------------------------------------------
    # WHERE clause
    # --------------------------------------------------------

    if filters:
        sql += ' WHERE ' + ' AND '.join(filters)

    # --------------------------------------------------------
    # Latest rows first
    # --------------------------------------------------------

    sql += ' ORDER BY quote_time DESC LIMIT %s'

    params.append(limit)

    # --------------------------------------------------------
    # Execute + convert UTC -> IST
    # --------------------------------------------------------

    rows = _run_query(
        sql,
        tuple(params),
    )

    return _to_ist(rows)


# ============================================================
# Commodity by symbol
# ============================================================

@router.post('/commodity')
def get_commodity_by_symbol(
    request: SymbolRequest,
    limit: int = Query(
        1000,
        ge=1,
        le=10000,
        description='Maximum number of rows to return.',
    ),
    start_time: str | None = Query(
        None,
        description='ISO timestamp to filter quote_time >= start_time.',
    ),
    end_time: str | None = Query(
        None,
        description='ISO timestamp to filter quote_time <= end_time.',
    ),
) -> list[dict[str, Any]]:

    table_name = _resolve_table(
        'commodity',
        request.mode,
    )

    sql = (
        f'SELECT * FROM {table_name} '
        f'WHERE symbol = %s'
    )

    params: list[Any] = [
        request.symbol,
    ]

    filters: list[str] = []

    # --------------------------------------------------------
    # Time filters
    # --------------------------------------------------------

    if start_time is not None:
        filters.append(
            'quote_time >= %s'
        )
        params.append(start_time)

    if end_time is not None:
        filters.append(
            'quote_time <= %s'
        )
        params.append(end_time)

    # --------------------------------------------------------
    # Additional filters
    # --------------------------------------------------------

    if filters:
        sql += ' AND ' + ' AND '.join(filters)

    # --------------------------------------------------------
    # Latest rows first
    # --------------------------------------------------------

    sql += ' ORDER BY quote_time DESC LIMIT %s'

    params.append(limit)

    # --------------------------------------------------------
    # Execute + convert UTC -> IST
    # --------------------------------------------------------

    rows = _run_query(
        sql,
        tuple(params),
    )

    return _to_ist(rows)