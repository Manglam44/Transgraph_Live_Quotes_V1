from __future__ import annotations

from datetime import datetime

from src.common.base_streamer import StreamerConfig, run_streamer
from src.common.settings import LOCAL_TZ
from src.utils.commodity_contracts import load_commodity_spot_proxies
from src.utils.validators import valid_price, valid_size


def build_row(spec, ticker, mode):
    if not (valid_price(ticker.bid) or valid_price(ticker.ask) or valid_price(ticker.last)):
        return None

    tick_time = ticker.time
    now = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    ib_ticker_time = tick_time.replace(tzinfo=None) if tick_time else None
    ib_ticker_time_zone = str(tick_time.tzinfo) if tick_time and tick_time.tzinfo else None

    return (
        now,
        ib_ticker_time,
        ib_ticker_time_zone,
        mode.upper(),
        'commodity',
        spec.metadata.get('instrument_type', 'spot'),
        spec.metadata['display_name'],
        ticker.contract.symbol,
        None,
        ticker.contract.exchange,
        None,
        None,
        None,
        ticker.contract.symbol,
        ticker.bid if valid_price(ticker.bid) else None,
        ticker.ask if valid_price(ticker.ask) else None,
        ticker.last if valid_price(ticker.last) else None,
        ticker.bidSize if valid_size(ticker.bidSize) else None,
        ticker.askSize if valid_size(ticker.askSize) else None,
        ticker.lastSize if valid_size(ticker.lastSize) else None,
    )


CONFIG = StreamerConfig(
    asset_class='commodity',
    flow='spot_all',
    description='Commodity spot proxy all streamer',
    load_specs=lambda args: load_commodity_spot_proxies(),
    build_row=build_row,
    needs_expiry=False,
)


def main() -> None:
        run_streamer(CONFIG)


if __name__ == '__main__':
    main()
