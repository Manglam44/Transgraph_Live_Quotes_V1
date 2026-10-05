from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from src.api.auth import require_api_key
from src.common.questdb import connect_questdb
from src.common.tables import TABLES

log = logging.getLogger(__name__)

Mode = Literal["live", "delayed"]
Order = Literal["asc", "desc"]
AssetGroup = Literal["commodity", "currency"]

IST = ZoneInfo("Asia/Kolkata")

# ============================================================
# Quote Time Configuration
# ============================================================
#
# The streamers store quote_time as the IST wall clock with no
# timezone information, while ticker_time is real UTC.
#
# 0 (default):
#     quote_time is treated as UTC and converted to IST.
#
# 1:
#     quote_time is treated as IST.
#
# Enable only after confirming the frontend does not compensate
# for the old timezone shift.
#
# ============================================================

QUOTE_TIME_IS_IST = os.getenv(
    "HISTORICAL_QUOTE_TIME_IS_IST",
    "0",
) == "1"

STORE_TZ = IST if QUOTE_TIME_IS_IST else timezone.utc

MAX_LIMIT = 50_000

# QuestDB SAMPLE BY units.
# This is intentionally whitelisted because SQL identifiers/
# clauses cannot be safely parameterized.
_INTERVALS = {
    "1s",
    "10s",
    "1m",
    "5m",
    "15m",
    "1h",
    "1d",
}


# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/historical",
    tags=["historical"],
    dependencies=[Depends(require_api_key)],
)


# ============================================================
# Timestamp Helpers
# ============================================================

def _to_ist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert quote_time and ticker_time values to Asia/Kolkata
    ISO-8601 strings.

    quote_time:
        Uses HISTORICAL_QUOTE_TIME_IS_IST configuration.

    ticker_time:
        Always treated as UTC when stored without timezone.
    """

    for row in rows:
        for field in ("quote_time", "ticker_time"):
            value = row.get(field)

            if not isinstance(value, datetime):
                continue

            if value.tzinfo is None:
                if field == "quote_time" and QUOTE_TIME_IS_IST:
                    value = value.replace(tzinfo=IST)
                else:
                    value = value.replace(tzinfo=timezone.utc)

            row[field] = value.astimezone(IST).isoformat()

    return rows


def _parse_bound(
    value: str,
    *,
    end: bool,
) -> tuple[datetime, str]:
    """
    Convert a query-string timestamp into a naive datetime
    matching the quote_time storage convention.

    Supported examples:

        2026-10-05

        2026-10-05T10:00:00

        2026-10-05T10:00:00Z

        2026-10-05T10:00:00+05:30

    Date-only values are interpreted as an IST calendar day.

    For example:

        start_time=2026-10-05

    means:

        >= 2026-10-05 00:00:00 IST

    and:

        end_time=2026-10-05

    means:

        < 2026-10-06 00:00:00 IST
    """

    raw = value.strip()

    try:
        # ----------------------------------------------------
        # Date-only input
        # ----------------------------------------------------
        if len(raw) == 10:
            day = date.fromisoformat(raw)

            if end:
                day += timedelta(days=1)

            local = datetime.combine(
                day,
                time.min,
                tzinfo=IST,
            )

            return (
                local.astimezone(STORE_TZ).replace(tzinfo=None),
                "<" if end else ">=",
            )

        # ----------------------------------------------------
        # Full ISO timestamp
        # ----------------------------------------------------
        parsed = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid timestamp: {value!r}",
        )

    # Timestamp without timezone:
    # treat it according to the storage convention.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=STORE_TZ)

    return (
        parsed.astimezone(STORE_TZ).replace(tzinfo=None),
        "<=" if end else ">=",
    )


# ============================================================
# QuestDB Helpers
# ============================================================

def _run_query(
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    """
    Execute a QuestDB query and return rows as dictionaries.
    """

    conn = None

    try:
        conn = connect_questdb()

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
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


def _resolve_table(
    asset_group: AssetGroup,
    mode: Mode,
) -> str:
    """
    Resolve the QuestDB table from the server-side TABLES mapping.

    The frontend never provides a table name.

    Examples:

        commodity + live
            -> commodity_live

        commodity + delayed
            -> commodity_delayed

        currency + live
            -> currency_live

        currency + delayed
            -> currency_delayed
    """

    normalized_asset_group = asset_group.strip().lower()
    normalized_mode = mode.strip().lower()

    table_name = TABLES.get(
        (
            normalized_asset_group,
            normalized_mode,
        )
    )

    if table_name is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No table available for "
                f"asset_group={normalized_asset_group!r}, "
                f"mode={normalized_mode!r}."
            ),
        )

    return table_name


def _build_contract_filters(
    symbol: str,
    expiry: str,
    start_time: str | None,
    end_time: str | None,
) -> tuple[str, list[Any]]:
    """
    Build filters for a specific symbol + expiry contract.
    """

    conditions: list[str] = [
        "symbol = %s",
        "expiry = %s",
    ]

    params: list[Any] = [
        symbol.upper(),
        expiry,
    ]

    if start_time is not None:
        start_dt, operator = _parse_bound(
            start_time,
            end=False,
        )

        conditions.append(
            f"quote_time {operator} %s"
        )

        params.append(start_dt)

    if end_time is not None:
        end_dt, operator = _parse_bound(
            end_time,
            end=True,
        )

        conditions.append(
            f"quote_time {operator} %s"
        )

        params.append(end_dt)

    return " AND ".join(conditions), params


# ============================================================
# 1. Available Contracts
# ============================================================

@router.get("/contracts")
def get_available_contracts(
    asset_group: AssetGroup = Query(
        ...,
        description=(
            "Asset group. "
            "Supported values: commodity, currency."
        ),
    ),
    symbol: str = Query(
        ...,
        description=(
            "Market symbol. "
            "Example: GC."
        ),
    ),
    mode: Mode = Query(
        "live",
        description=(
            "Data mode. "
            "Supported values: live, delayed."
        ),
    ),
) -> dict[str, Any]:
    """
    Get all available expiry contracts for a symbol.

    Returns the latest quote for each expiry.

    Example:

        GET /historical/contracts
            ?asset_group=commodity
            &symbol=GC
            &mode=live

    Response contains one row per expiry.
    """

    normalized_symbol = symbol.strip().upper()

    if not normalized_symbol:
        raise HTTPException(
            status_code=422,
            detail="symbol cannot be empty.",
        )

    table_name = _resolve_table(
        asset_group,
        mode,
    )

    sql = f"""
        SELECT
            symbol,
            expiry,
            instrument_type,
            exchange,
            last,
            bid,
            ask,
            quote_time,
            ticker_time
        FROM (
            SELECT
                symbol,
                expiry,
                instrument_type,
                exchange,
                last,
                bid,
                ask,
                quote_time,
                ticker_time,

                ROW_NUMBER() OVER (
                    PARTITION BY expiry
                    ORDER BY quote_time DESC
                ) AS rn

            FROM {table_name}

            WHERE symbol = %s
              AND expiry IS NOT NULL
        )

        WHERE rn = 1

        ORDER BY expiry ASC
    """

    rows = _run_query(
        sql,
        (normalized_symbol,),
    )

    rows = _to_ist(rows)

    return {
        "asset_group": asset_group,
        "mode": mode,
        "symbol": normalized_symbol,
        "count": len(rows),
        "data": rows,
    }


# ============================================================
# 2. Chart Historical Data
# ============================================================

@router.get("/chart")
def get_chart_history(
    asset_group: AssetGroup = Query(
        ...,
        description=(
            "Asset group. "
            "Supported values: commodity, currency."
        ),
    ),
    symbol: str = Query(
        ...,
        description="Market symbol. Example: GC.",
    ),
    expiry: str = Query(
        ...,
        description="Contract expiry. Example: 20261229.",
    ),
    mode: Mode = Query(
        "live",
        description=(
            "Data mode. "
            "Supported values: live, delayed."
        ),
    ),
    start_time: str | None = Query(
        None,
        description="Start date/time.",
    ),
    end_time: str | None = Query(
        None,
        description="End date/time.",
    ),
    order: Order = Query(
        "asc",
        description=(
            "Data order. "
            "asc = oldest first, "
            "desc = newest first."
        ),
    ),
    limit: int = Query(
        MAX_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="Maximum number of rows.",
    ),
) -> dict[str, Any]:
    """
    Get complete raw historical data for one specific
    symbol + expiry contract.

    No aggregation is performed.

    Every database row is returned.

    Example:

        GET /historical/chart
            ?asset_group=commodity
            &symbol=GC
            &expiry=20261229
            &mode=live

    Returns the same raw data structure as the database.
    """

    normalized_symbol = symbol.strip().upper()
    normalized_expiry = expiry.strip()

    if not normalized_symbol:
        raise HTTPException(
            status_code=422,
            detail="symbol cannot be empty.",
        )

    if not normalized_expiry:
        raise HTTPException(
            status_code=422,
            detail="expiry cannot be empty.",
        )

    table_name = _resolve_table(
        asset_group,
        mode,
    )

    conditions: list[str] = [
        "symbol = %s",
        "expiry = %s",
    ]

    params: list[Any] = [
        normalized_symbol,
        normalized_expiry,
    ]

    # --------------------------------------------------------
    # Start time
    # --------------------------------------------------------

    if start_time is not None:
        start_dt, operator = _parse_bound(
            start_time,
            end=False,
        )

        conditions.append(
            f"quote_time {operator} %s"
        )

        params.append(start_dt)

    # --------------------------------------------------------
    # End time
    # --------------------------------------------------------

    if end_time is not None:
        end_dt, operator = _parse_bound(
            end_time,
            end=True,
        )

        conditions.append(
            f"quote_time {operator} %s"
        )

        params.append(end_dt)

    where = " AND ".join(conditions)

    sql = f"""
        SELECT *
        FROM {table_name}
        WHERE {where}
        ORDER BY quote_time {order.upper()}
        LIMIT %s
    """

    params.append(limit)

    rows = _run_query(
        sql,
        tuple(params),
    )

    rows = _to_ist(rows)

    return {
        "asset_group": asset_group,
        "mode": mode,
        "symbol": normalized_symbol,
        "expiry": normalized_expiry,
        "order": order,
        "count": len(rows),
        "data": rows,
    }

# ============================================================
# 3. Individual Contract Historical Data
# ============================================================

@router.get("/instrument")
def get_instrument_history(
    asset_group: AssetGroup = Query(
        ...,
        description="Asset group: commodity or currency.",
    ),
    symbol: str = Query(
        ...,
        description="Market symbol. Example: GC.",
    ),
    expiry: str = Query(
        ...,
        description="Contract expiry. Example: 20261229.",
    ),
    mode: Mode = Query(
        "live",
        description="Data mode: live or delayed.",
    ),
    order: Order = Query(
        "asc",
        description="asc = oldest first, desc = newest first.",
    ),
    limit: int = Query(
        MAX_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="Maximum number of rows.",
    ),
) -> dict[str, Any]:
    """
    Get complete raw historical data for one specific
    symbol + expiry contract.

    No date filtering.
    No interval.
    No aggregation.

    Returns the raw database rows.
    """

    normalized_symbol = symbol.strip().upper()
    normalized_expiry = expiry.strip()

    if not normalized_symbol:
        raise HTTPException(
            status_code=422,
            detail="symbol cannot be empty.",
        )

    if not normalized_expiry:
        raise HTTPException(
            status_code=422,
            detail="expiry cannot be empty.",
        )

    table_name = _resolve_table(
        asset_group,
        mode,
    )

    sql = f"""
        SELECT *
        FROM {table_name}
        WHERE symbol = %s
          AND expiry = %s
        ORDER BY quote_time {order.upper()}
        LIMIT %s
    """

    rows = _run_query(
        sql,
        (
            normalized_symbol,
            normalized_expiry,
            limit,
        ),
    )

    rows = _to_ist(rows)

    return {
        "asset_group": asset_group,
        "mode": mode,
        "symbol": normalized_symbol,
        "expiry": normalized_expiry,
        "order": order,
        "count": len(rows),
        "data": rows,
    }