from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence, Tuple

from src.common.connection_events import mark_backfill_status
from src.common.models import ContractSpec
from src.common.questdb import get_batch_writer
from src.common.settings import LOCAL_TZ

log = logging.getLogger(__name__)

MAX_TICKS_PER_REQUEST = 1000  # IB hard limit per reqHistoricalTicks call


def _is_forex(spec: ContractSpec) -> bool:
    return getattr(spec.contract, 'secType', '') == 'CASH'


def _to_utc(t: datetime) -> datetime:
    return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _fetch_ticks_for_contract(ib, contract, start_dt: datetime, end_dt: datetime, what_to_show: str):
    """Pages backwards from end_dt towards start_dt, 1000 ticks at a time."""
    all_ticks = []
    cursor_end = end_dt

    while True:
        ticks = ib.reqHistoricalTicks(
            contract,
            startDateTime='',
            endDateTime=cursor_end,
            numberOfTicks=MAX_TICKS_PER_REQUEST,
            whatToShow=what_to_show,
            useRth=False,
        )
        if not ticks:
            break

        in_window = [t for t in ticks if start_dt <= t.time <= cursor_end]
        all_ticks.extend(in_window)

        earliest = ticks[0].time
        if earliest <= start_dt or len(ticks) < MAX_TICKS_PER_REQUEST:
            break

        cursor_end = earliest
        ib.sleep(1)  # stay under IB historical-data pacing limits

    return all_ticks


def _tick_to_row(spec: ContractSpec, tick, mode: str, asset_class: str, columns: Tuple[str, ...]) -> tuple:
    """Build a row shaped like the live rows.

    Live convention (unchanged): quote_time = IST wall clock with no tz (stored
    as if UTC), ticker_time = real UTC with no tz, mode upper case.
    """
    utc = _to_utc(tick.time)
    contract = spec.contract
    is_fx = _is_forex(spec)

    pair = spec.metadata.get('pair') if is_fx else None
    symbol = None if is_fx else getattr(contract, 'symbol', None)

    bid = ask = last = bid_size = ask_size = last_size = None
    if hasattr(tick, 'priceBid'):  # BID_ASK tick
        bid, ask = tick.priceBid, tick.priceAsk
        bid_size, ask_size = tick.sizeBid, tick.sizeAsk
    else:                          # TRADES tick
        last, last_size = getattr(tick, 'price', None), getattr(tick, 'size', None)

    values = {
        'quote_time': utc.astimezone(LOCAL_TZ).replace(tzinfo=None),
        'ticker_time': utc.replace(tzinfo=None),
        'ticker_time_zone': 'UTC',
        'mode': mode.upper(),
        'asset_class': asset_class,
        'instrument_type': spec.metadata.get('instrument_type', spec.contract_type),
        'name': spec.metadata.get('display_name'),
        'symbol': symbol,
        'pair': pair,
        'exchange': None if is_fx else getattr(contract, 'exchange', None),
        'expiry': None if is_fx else getattr(contract, 'lastTradeDateOrContractMonth', None),
        'strike': None,
        'right': None,
        'underlying': pair if is_fx else symbol,
        'bid': bid,
        'ask': ask,
        'last': last,
        'bid_size': bid_size,
        'ask_size': ask_size,
        'last_size': last_size,
        'source': 'backfill',
    }
    return tuple(values.get(col) for col in columns)


def run_backfill(
    ib,
    qualified: Sequence[ContractSpec],
    asset_class: str,
    flow: str,
    mode: str,
    table_name: str,
    columns: Tuple[str, ...],
    start_dt: datetime,
    end_dt: datetime,
    what_to_show: str = 'TRADES',
) -> None:
    """Synchronous by design (same ib/event-loop thread). Forex uses BID_ASK
    ticks (IDEALPRO has no trade ticks); everything else uses `what_to_show`."""
    writer = get_batch_writer(table_name, columns, batch_size=200, flush_interval_seconds=2)
    status = 'done'
    total = 0

    try:
        for spec in qualified:
            label = spec.metadata.get('display_name', spec.contract_type)
            wts = 'BID_ASK' if _is_forex(spec) else what_to_show
            try:
                ticks = _fetch_ticks_for_contract(ib, spec.contract, start_dt, end_dt, wts)
            except Exception:
                log.exception('Backfill request failed for %s', label)
                status = 'partial'
                continue

            for tick in ticks:
                writer.add(_tick_to_row(spec, tick, mode, asset_class, columns))

            total += len(ticks)
            log.info('Backfilled %s %s ticks for %s (%s -> %s)', len(ticks), wts, label, start_dt, end_dt)
    finally:
        writer.close()
        mark_backfill_status(start_dt, asset_class, flow, mode, status)
        log.info('Backfill finished for %s/%s/%s: %s ticks, status=%s', asset_class, flow, mode, total, status)
