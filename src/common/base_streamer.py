from __future__ import annotations

import argparse
import signal
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from src.common.ib_client import MARKET_DATA_TYPES, connect_ib_with_retry
from src.common.logging_config import configure_logging
from src.common.models import ContractSpec
from src.common.questdb import get_batch_writer
from src.common.settings import (
    CLIENT_IDS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_COMMODITY_EXPIRY_MONTH,
    DEFAULT_FLUSH_INTERVAL_SECONDS,
    DEFAULT_REQUEST_DELAY_SECONDS,
)
from src.common.streaming import qualify_contracts, run_forever, subscribe_market_data
from src.common.tables import get_table_name

COLUMNS = (
    'quote_time',
    'ticker_time',
    'ticker_time_zone',
    'mode',
    'asset_class',
    'instrument_type',
    'name',
    'symbol',
    'pair',
    'exchange',
    'expiry',
    'strike',
    'right',
    'underlying',
    'bid',
    'ask',
    'last',
    'bid_size',
    'ask_size',
    'last_size',
)

BuildRowFn = Callable[[ContractSpec, object, str], Optional[tuple]]
LoadSpecsFn = Callable[[argparse.Namespace], Sequence[ContractSpec]]

_shutdown_requested = False


def _handle_shutdown_signal(signum, frame) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def _should_stop() -> bool:
    return _shutdown_requested


@dataclass
class StreamerConfig:
    asset_class: str        # 'commodity' | 'currency'  -- must match CLIENT_IDS / TABLES keys
    flow: str                # 'spot_all' | 'futures_all' | 'options_all'
    description: str
    load_specs: LoadSpecsFn
    build_row: BuildRowFn
    needs_expiry: bool = False
    no_contracts_retry_seconds: float = 30.0


def _parse_args(config: StreamerConfig) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=config.description)
    parser.add_argument('--mode', choices=('live', 'delayed'), default='delayed')
    if config.needs_expiry:
        parser.add_argument('--expiry-month', default=DEFAULT_COMMODITY_EXPIRY_MONTH)
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--flush-interval-seconds', type=float, default=DEFAULT_FLUSH_INTERVAL_SECONDS)
    parser.add_argument('--request-delay-seconds', type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    return parser.parse_args()


def _build_handler(writer, mode: str, spec: ContractSpec, build_row: BuildRowFn):
    def on_update(ticker):
        row = build_row(spec, ticker, mode)
        if row is None:
            return
        writer.add(row)

    return on_update


def run_streamer(config: StreamerConfig) -> None:
    """Shared entrypoint body for all six streamers.

    Handles: arg parsing, writer lifecycle, IB connect-with-retry, and an
    outer loop that reconnects and re-subscribes whenever the IB connection
    drops -- rather than the previous behaviour of a single connect attempt
    with no recovery path. Also handles SIGTERM/SIGINT for a clean shutdown
    under Docker (`docker stop` sends SIGTERM; without a handler the process
    is hard-killed after the grace period and may lose buffered rows).
    """
    log = configure_logging(f'{config.asset_class}.{config.flow}')
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    args = _parse_args(config)
    client_id = CLIENT_IDS[(config.asset_class, config.flow, args.mode)]
    # Table is resolved by (asset_class, mode) -- live and delayed quotes
    # for the same asset class now land in separate tables.
    table_name = get_table_name(config.asset_class, args.mode)
    log.info('Writing to table %s', table_name)

    writer = get_batch_writer(table_name, COLUMNS, args.batch_size, args.flush_interval_seconds)

    try:
        while not _should_stop():
            try:
                ib = connect_ib_with_retry(client_id)
            except Exception:
                log.exception('Unrecoverable error establishing IB connection')
                break

            try:
                specs = config.load_specs(args)
                qualified = qualify_contracts(ib, specs, args.request_delay_seconds)

                if not qualified:
                    log.error(
                        'No contracts qualified for %s %s -- nothing to subscribe. '
                        'Retrying in %.0fs.',
                        config.asset_class, config.flow, config.no_contracts_retry_seconds,
                    )
                    time.sleep(config.no_contracts_retry_seconds)
                    continue

                subscribe_market_data(
                    ib,
                    qualified,
                    MARKET_DATA_TYPES[args.mode],
                    args.request_delay_seconds,
                    lambda spec: _build_handler(writer, args.mode, spec, config.build_row),
                )
                log.info('Listening for %s %s (%s)', config.asset_class, config.flow, args.mode)
                run_forever(ib, writer, should_stop=_should_stop)
            finally:
                if ib.isConnected():
                    ib.disconnect()

            if not _should_stop():
                log.warning('Connection dropped -- reconnecting and re-subscribing')
    finally:
        writer.close()
        log.info('Streamer stopped cleanly')
