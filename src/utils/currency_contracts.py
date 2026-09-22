from __future__ import annotations

import json
import os

from ib_insync import Forex, Future, FuturesOption

from src.common.models import ContractSpec
from src.common.settings import DEFAULT_COMMODITY_EXPIRY_MONTH

_PAIRS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'currency_pairs.json')
_FUTURES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'currency_futures_map.json')
_OPTIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'currency_options_map.json')

with open(_PAIRS_FILE, encoding='utf-8') as _f:
    IDEALPRO_PAIRS: frozenset[str] = frozenset(json.load(_f))

with open(_FUTURES_FILE, encoding='utf-8') as _f:
    CURRENCY_FUTURES_MAP: dict = json.load(_f)

with open(_OPTIONS_FILE, encoding='utf-8') as _f:
    CURRENCY_OPTIONS_MAP: dict = json.load(_f)


def load_currency_quotes_all() -> list[ContractSpec]:
    return [
        ContractSpec(
            contract_type='spot',
            contract=Forex(pair),
            metadata={
                'display_code': pair,
                'display_name': pair,
                'pair': pair,
                'instrument_type': 'spot',
            },
        )
        for pair in sorted(IDEALPRO_PAIRS)
    ]


def load_currency_spot_all() -> list[ContractSpec]:
    return load_currency_quotes_all()


def load_currency_futures(expiry_month: str = DEFAULT_COMMODITY_EXPIRY_MONTH) -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for code, mapped in CURRENCY_FUTURES_MAP.items():
        symbol = mapped['symbol']
        exchange = mapped['exchange']
        currency = mapped['currency']
        display_name = mapped['name']
        specs.append(
            ContractSpec(
                contract_type='future',
                contract=Future(
                    symbol=symbol,
                    lastTradeDateOrContractMonth=expiry_month,
                    exchange=exchange,
                    currency=currency,
                    multiplier=mapped.get('multiplier'),
                    tradingClass=mapped.get('tradingClass'),
                ),
                metadata={
                    'display_code': code,
                    'display_name': display_name,
                    'symbol': symbol,
                    'exchange': exchange,
                    'instrument_type': 'future',
                    'expiry': expiry_month,
                },
            )
        )
    return specs


def load_currency_options(expiry_month: str = DEFAULT_COMMODITY_EXPIRY_MONTH) -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for code, mapped in CURRENCY_OPTIONS_MAP.items():
        symbol = mapped['symbol']
        exchange = mapped['exchange']
        currency = mapped['currency']
        display_name = mapped['name']
        strike = float(mapped['strike'])
        right = mapped['right']
        specs.append(
            ContractSpec(
                contract_type='futures_option',
                contract=FuturesOption(
                    symbol=symbol,
                    lastTradeDateOrContractMonth=expiry_month,
                    strike=strike,
                    right=right,
                    exchange=exchange,
                    currency=currency,
                    multiplier=mapped.get('multiplier'),
                    tradingClass=mapped.get('tradingClass'),
                ),
                metadata={
                    'display_code': code,
                    'display_name': display_name,
                    'symbol': symbol,
                    'exchange': exchange,
                    'instrument_type': 'option',
                    'expiry': expiry_month,
                    'strike': str(strike),
                    'right': right,
                    'underlying': symbol,
                },
            )
        )
    return specs
