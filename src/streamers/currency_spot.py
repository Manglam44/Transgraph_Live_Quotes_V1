from __future__ import annotations

from datetime import datetime

from src.common.base_streamer import StreamerConfig, run_streamer
from src.common.quote_utils import extract_quote_fields
from src.common.settings import LOCAL_TZ
from src.utils.currency_contracts import load_currency_quotes_all


def build_row(spec, ticker, mode):
    quote_fields = extract_quote_fields(ticker)
    if quote_fields is None:
        return None

    tick_time = ticker.time
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    ib_ticker_time = tick_time.replace(tzinfo=None) if tick_time else None
    ib_ticker_time_zone = str(tick_time.tzinfo) if tick_time and tick_time.tzinfo else None
    bid, ask, last, bid_size, ask_size, last_size = quote_fields

    return (
        now,
        ib_ticker_time,
        ib_ticker_time_zone,
        mode.upper(),
        'currency',
        spec.metadata.get('instrument_type', 'spot'),
        spec.metadata['display_name'],
        None,
        spec.metadata['pair'],
        None,
        None,
        None,
        None,
        spec.metadata['pair'],
        bid,
        ask,
        last,
        bid_size,
        ask_size,
        last_size,
    )


CONFIG = StreamerConfig(
    asset_class='currency',
    flow='spot_all',
    description='Currency quotes all streamer',
    load_specs=lambda args: load_currency_quotes_all(),
    build_row=build_row,
    needs_expiry=False,
)


def main() -> None:
    run_streamer(CONFIG)

if __name__ == '__main__':
    main()
