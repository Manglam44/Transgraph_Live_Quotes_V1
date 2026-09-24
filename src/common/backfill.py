from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Sequence, Tuple

from src.common.connection_events import mark_backfill_status
from src.common.models import ContractSpec
from src.common.questdb import get_batch_writer

log = logging.getLogger(__name__)

MAX_TICKS_PER_REQUEST = 1000  # IB hard limit per reqHistoricalTicks call


def _fetch_ticks_for_contract(ib, contract, start_dt: datetime, end_dt: datetime, what_to_show: str):
    """Pages backwards from end_dt towards start_dt, MAX_TICKS_PER_REQUEST at
    a time (IB's reqHistoricalTicks caps a single call at 1000 ticks)."""
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

        cursor_end = earliest  # walk further back for the next page
        ib.sleep(1)  # stay well under IB's historical-data pacing limits

    return all_ticks


def _tick_to_row(spec: ContractSpec, tick, mode: str, asset_class: str, columns: Tuple[str, ...]) -> tuple:
    """Maps a historical TRADES tick onto the same column layout your live
    rows use, so it can go through the identical writer/table.

    NOTE: this assumes whatToShow='TRADES' (HistoricalTickLast: .price/.size).
    If you also want to backfill bid/ask, request whatToShow='BID_ASK'
    separately -- those ticks expose .priceBid/.priceAsk/.sizeBid/.sizeAsk
    instead, and you'd add a second mapping branch for them.
    """
    values = {
        'quote_time': tick.time,
        'ticker_time': tick.time,
        'mode': mode,
        'asset_class': asset_class,
        'instrument_type': spec.contract_type,
        'name': spec.metadata.get('display_name'),
        'symbol': getattr(spec.contract, 'symbol', None),
        'exchange': getattr(spec.contract, 'exchange', None),
        'expiry': getattr(spec.contract, 'lastTradeDateOrContractMonth', None),
        'last': getattr(tick, 'price', None),
        'last_size': getattr(tick, 'size', None),
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
    """Synchronous by design -- see note in base_streamer.py about why this
    runs inline on the same ib/event-loop thread rather than backgrounded."""
    writer = get_batch_writer(table_name, columns, batch_size=200, flush_interval_seconds=2)
    status = 'done'
    total = 0

    try:
        for spec in qualified:
            label = spec.metadata.get('display_name', spec.contract_type)
            try:
                ticks = _fetch_ticks_for_contract(ib, spec.contract, start_dt, end_dt, what_to_show)
            except Exception:
                log.exception('Backfill request failed for %s', label)
                status = 'partial'
                continue

            for tick in ticks:
                writer.add(_tick_to_row(spec, tick, mode, asset_class, columns))

            total += len(ticks)
            log.info('Backfilled %s ticks for %s (%s -> %s)', len(ticks), label, start_dt, end_dt)
    finally:
        writer.close()
        mark_backfill_status(start_dt, asset_class, flow, mode, status)
        log.info('Backfill finished for %s/%s/%s: %s ticks, status=%s', asset_class, flow, mode, total, status)