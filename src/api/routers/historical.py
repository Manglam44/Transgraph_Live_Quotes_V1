from __future__ import annotations

import logging
from datetime import timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from src.api.auth import require_api_key
from src.common.questdb import connect_questdb
from src.common.tables import TABLES

log = logging.getLogger(__name__)

Mode = Literal["live", "delayed"]

IST = ZoneInfo("Asia/Kolkata")

_TS_FIELDS = (
    "quote_time",
    "ticker_time",
)

router = APIRouter(
    prefix="/historical",
    tags=["historical"],
    dependencies=[Depends(require_api_key)],
)


# ============================================================
# Timestamp conversion
# ============================================================

def _to_ist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    QuestDB timestamps are treated as UTC.
    API response timestamps are converted to Asia/Kolkata.
    """

    for row in rows:
        for field in _TS_FIELDS:
            value = row.get(field)

            if value is None:
                continue

            utc_value = value.replace(tzinfo=timezone.utc)
            ist_value = utc_value.astimezone(IST)

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
            "Historical QuestDB query failed: %s",
            sql,
        )

        raise HTTPException(
            status_code=502,
            detail="Query against QuestDB failed.",
        )

    finally:
        if conn is not None:
            conn.close()


# ============================================================
# Table resolver
# ============================================================

def _resolve_table(
    mode: str,
) -> str:

    table_name = TABLES.get(
        ("commodity", mode)
    )

    if table_name is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No commodity table available for mode={mode!r}."
            ),
        )

    return table_name


# ============================================================
# Historical Commodity API
# ============================================================

@router.get("/commodity")
def get_commodity_historical(

    # Example: GOLD
    name: str = Query(
        ...,
        description="Commodity name. Example: GOLD",
    ),

    mode: Mode = Query(
        "live",
        description="Data mode: live or delayed.",
    ),

    # future / spot / option
    instrument_type: str | None = Query(
        None,
        description=(
            "Optional instrument type. "
            "Examples: future, spot, option."
        ),
    ),

    # Example: HG / CL / GC
    symbol: str | None = Query(
        None,
        description="Optional market symbol.",
    ),

    # Example: COMEX / NYMEX
    exchange: str | None = Query(
        None,
        description="Optional exchange.",
    ),

    start_time: str | None = Query(
        None,
        description="ISO timestamp. quote_time >= start_time.",
    ),

    end_time: str | None = Query(
        None,
        description="ISO timestamp. quote_time <= end_time.",
    ),

    limit: int = Query(
        5000,
        ge=1,
        le=10000,
        description="Maximum number of rows.",
    ),
) -> list[dict[str, Any]]:

    table_name = _resolve_table(mode)

    # --------------------------------------------------------
    # Base query
    # --------------------------------------------------------

    sql = f"""
        SELECT *
        FROM {table_name}
        WHERE name = %s
        AND symbol = %s
        AND instrument_type = %s
    """

    params: list[Any] = [
        name.upper(),
        symbol.upper() if symbol is not None else None,
        instrument_type.lower() if instrument_type is not None else None,
    ]

    # --------------------------------------------------------
    # Instrument type
    # --------------------------------------------------------

    if instrument_type is not None:
        sql += """
            AND instrument_type = %s
        """

        params.append(
            instrument_type.lower()
        )

    # --------------------------------------------------------
    # Symbol
    # --------------------------------------------------------

    if symbol is not None:
        sql += """
            AND symbol = %s
        """

        params.append(
            symbol.upper()
        )

    # --------------------------------------------------------
    # Exchange
    # --------------------------------------------------------

    if exchange is not None:
        sql += """
            AND exchange = %s
        """

        params.append(
            exchange.upper()
        )

    # --------------------------------------------------------
    # Start time
    # --------------------------------------------------------

    if start_time is not None:
        sql += """
            AND quote_time >= %s
        """

        params.append(
            start_time
        )

    # --------------------------------------------------------
    # End time
    # --------------------------------------------------------

    if end_time is not None:
        sql += """
            AND quote_time <= %s
        """

        params.append(
            end_time
        )

    # --------------------------------------------------------
    # Sort + limit
    # --------------------------------------------------------

    sql += """
        ORDER BY quote_time DESC
        LIMIT %s
    """

    params.append(limit)

    # --------------------------------------------------------
    # Execute
    # --------------------------------------------------------

    rows = _run_query(
        sql,
        tuple(params),
    )

    # --------------------------------------------------------
    # UTC -> IST
    # --------------------------------------------------------

    return _to_ist(rows)