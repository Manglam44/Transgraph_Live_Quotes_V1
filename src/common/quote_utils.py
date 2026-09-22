from __future__ import annotations

from src.utils.validators import valid_price, valid_size


def extract_quote_fields(ticker):
    if not (valid_price(ticker.bid) or valid_price(ticker.ask) or valid_price(ticker.last)):
        return None

    return (
        ticker.bid if valid_price(ticker.bid) else None,
        ticker.ask if valid_price(ticker.ask) else None,
        ticker.last if valid_price(ticker.last) else None,
        ticker.bidSize if valid_size(ticker.bidSize) else None,
        ticker.askSize if valid_size(ticker.askSize) else None,
        ticker.lastSize if valid_size(ticker.lastSize) else None,
    )
