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

IST = ZoneInfo("Asia/Kolkata")

# The streamers store quote_time as the IST wall clock with no timezone
# (labelled UTC in QuestDB). ticker_time is real UTC.
#   0 (default) -> legacy behaviour: quote_time is treated as UTC, then shifted
#                  to IST (response quote_time is 5.5h later than reality).
#   1           -> quote_time is treated as IST. Responses and filters are
#                  correct. Enable only after checking the frontend does not
#                  compensate for the old shift.
QUOTE_TIME_IS_IST = os.getenv("HISTORICAL_QUOTE_TIME_IS_IST", "0") == "1"
STORE_TZ = IST if QUOTE_TIME_IS_IST else timezone.utc

MAX_LIMIT = 50_000

# SAMPLE BY units: s, m, h, d. Whitelisted because it cannot be parameterised.
_INTERVALS = {"1s", "10s", "1m", "5m", "15m", "1h", "1d"}

router = APIRouter(
    prefix="/historical",
    tags=["historical"],
    dependencies=[Depends(require_api_key)],
)


# ============================================================
# Timestamps
# ============================================================

def _to_ist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _parse_bound(value: str, *, end: bool) -> tuple[datetime, str]:
    """Convert a query-string bound to a naive datetime in quote_time's
    storage convention. Returns (datetime, sql_operator).

    - "2026-10-05"           -> an IST calendar day (start: 00:00 IST,
                                end: next day 00:00 IST, exclusive).
    - "2026-10-05T10:00:00Z" -> exact instant (offset honoured, inclusive).
    - no offset              -> same convention as storage (UTC by default,
                                IST when HISTORICAL_QUOTE_TIME_IS_IST=1).
    """
    raw = value.strip()

    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            if end:
                day += timedelta(days=1)
            local = datetime.combine(day, time.min, tzinfo=IST)
            return local.astimezone(STORE_TZ).replace(tzinfo=None), "<" if end else ">="

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid timestamp: {value!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=STORE_TZ)

    return parsed.astimezone(STORE_TZ).replace(tzinfo=None), "<=" if end else ">="


# ============================================================
# Helpers
# ============================================================

def _run_query(sql: str, params: tuple) -> list[dict[str, Any]]:
    conn = None
    try:
        conn = connect_questdb()
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception:
        log.exception("Historical QuestDB query failed: %s", sql)
        raise HTTPException(status_code=502, detail="Query against QuestDB failed.")
    finally:
        if conn is not None:
            conn.close()


def _resolve_table(mode: str) -> str:
    table_name = TABLES.get(("commodity", mode))
    if table_name is None:
        raise HTTPException(
            status_code=404,
            detail=f"No commodity table available for mode={mode!r}.",
        )
    return table_name


def _build_where(
    name: str,
    instrument_type: str | None,
    symbol: str | None,
    exchange: str | None,
    expiry: str | None,
    start_time: str | None,
    end_time: str | None,
) -> tuple[str, list[Any]]:
    """Only adds a condition for filters the caller actually passed."""
    conditions: list[str] = ["name = %s"]
    params: list[Any] = [name.upper()]

    if instrument_type is not None:
        conditions.append("instrument_type = %s")
        params.append(instrument_type.lower())
    if symbol is not None:
        conditions.append("symbol = %s")
        params.append(symbol.upper())
    if exchange is not None:
        conditions.append("exchange = %s")
        params.append(exchange.upper())
    if expiry is not None:
        conditions.append("expiry = %s")
        params.append(expiry)
    if start_time is not None:
        start_dt, op = _parse_bound(start_time, end=False)
        conditions.append(f"quote_time {op} %s")
        params.append(start_dt)
    if end_time is not None:
        end_dt, op = _parse_bound(end_time, end=True)
        conditions.append(f"quote_time {op} %s")
        params.append(end_dt)

    return " AND ".join(conditions), params


# ============================================================
# Raw ticks
# ============================================================

@router.get("/commodity")
def get_commodity_historical(
    name: str = Query(..., description="Commodity name. Example: GOLD"),
    mode: Mode = Query("live", description="Data mode: live or delayed."),
    instrument_type: str | None = Query(None, description="future, spot or option."),
    symbol: str | None = Query(None, description="Optional market symbol."),
    exchange: str | None = Query(None, description="Optional exchange."),
    expiry: str | None = Query(None, description="Futures expiry, e.g. 20261028."),
    start_time: str | None = Query(
        None, description="quote_time >= start. 'YYYY-MM-DD' (IST day) or ISO timestamp."
    ),
    end_time: str | None = Query(
        None, description="'YYYY-MM-DD' = through the END of that IST day, or ISO timestamp."
    ),
    order: Order = Query(
        "desc",
        description="desc = newest first (default, unchanged). asc = oldest first, for charts.",
    ),
    limit: int = Query(5000, ge=1, le=MAX_LIMIT, description="Maximum rows."),
) -> list[dict[str, Any]]:
    """To page through a long range oldest -> newest, use order=asc and pass the
    last row's quote_time as the next start_time (drop the first row of the next
    page, it repeats)."""
    table_name = _resolve_table(mode)
    where, params = _build_where(
        name, instrument_type, symbol, exchange, expiry, start_time, end_time
    )

    sql = f"""
        SELECT *
        FROM {table_name}
        WHERE {where}
        ORDER BY quote_time {order.upper()}
        LIMIT %s
    """
    params.append(limit)

    return _to_ist(_run_query(sql, tuple(params)))


# ============================================================
# Chart candles (open/high/low/close per interval)
# ============================================================

@router.get("/commodity/ohlc")
def get_commodity_ohlc(
    name: str = Query(..., description="Commodity name. Example: GOLD"),
    mode: Mode = Query("live", description="Data mode: live or delayed."),
    instrument_type: str | None = Query(None, description="future, spot or option."),
    symbol: str | None = Query(None, description="Optional market symbol."),
    exchange: str | None = Query(None, description="Optional exchange."),
    expiry: str | None = Query(None, description="Futures expiry. Required for futures."),
    start_time: str | None = Query(None, description="'YYYY-MM-DD' (IST day) or ISO timestamp."),
    end_time: str | None = Query(None, description="'YYYY-MM-DD' (through end of that day) or ISO."),
    interval: str = Query("1m", description="1s, 10s, 1m, 5m, 15m, 1h or 1d."),
    limit: int = Query(5000, ge=1, le=MAX_LIMIT, description="Maximum candles."),
) -> list[dict[str, Any]]:
    """Oldest -> newest candles of `last` price, plus the closing bid/ask."""
    if interval not in _INTERVALS:
        raise HTTPException(
            status_code=422,
            detail=f"interval must be one of {sorted(_INTERVALS)}",
        )
    if (instrument_type or "").lower() == "future" and expiry is None:
        raise HTTPException(
            status_code=422,
            detail="expiry is required for futures, otherwise contracts are mixed.",
        )

    table_name = _resolve_table(mode)
    where, params = _build_where(
        name, instrument_type, symbol, exchange, expiry, start_time, end_time
    )

    sql = f"""
        SELECT
            quote_time,
            first("last") AS open,
            max("last")   AS high,
            min("last")   AS low,
            last("last")  AS close,
            last(bid)     AS bid,
            last(ask)     AS ask,
            count()       AS ticks
        FROM {table_name}
        WHERE {where}
        SAMPLE BY {interval} ALIGN TO CALENDAR
        LIMIT %s
    """
    params.append(limit)

    return _to_ist(_run_query(sql, tuple(params)))