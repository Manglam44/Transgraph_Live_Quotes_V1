from __future__ import annotations

import json
import os

from ib_insync import Future, FuturesOption, Stock

from src.common.models import ContractSpec
from src.common.settings import DEFAULT_COMMODITY_EXPIRY_MONTH

_MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'commodity_futures_map.json')
_SPOT_MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'commodity_spot_proxy_map.json')
_OPTIONS_MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'commodity_options_map.json')

with open(_MAP_FILE, encoding='utf-8') as _f:
    COMMODITY_FUTURES_MAP: dict = json.load(_f)

with open(_SPOT_MAP_FILE, encoding='utf-8') as _f:
    COMMODITY_SPOT_PROXY_MAP: dict = json.load(_f)

with open(_OPTIONS_MAP_FILE, encoding='utf-8') as _f:
    COMMODITY_OPTIONS_MAP: dict = json.load(_f)


def load_commodity_futures(expiry_month: str = DEFAULT_COMMODITY_EXPIRY_MONTH) -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for asset_code, mapped in COMMODITY_FUTURES_MAP.items():
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
                    'display_code': asset_code,
                    'display_name': display_name,
                    'exchange': exchange,
                    'instrument_type': 'future',
                },
            )
        )
    return specs


def load_commodity_spot_proxies() -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for asset_code, mapped in COMMODITY_SPOT_PROXY_MAP.items():
        symbol = mapped['symbol']
        exchange = mapped['exchange']
        currency = mapped['currency']
        display_name = mapped['name']
        specs.append(
            ContractSpec(
                contract_type='spot_proxy',
                contract=Stock(
                    symbol=symbol,
                    exchange=exchange,
                    currency=currency,
                ),
                metadata={
                    'display_code': asset_code,
                    'display_name': display_name,
                    'exchange': exchange,
                    'instrument_type': 'spot',
                },
            )
        )
    return specs


def load_commodity_options(expiry_month: str = DEFAULT_COMMODITY_EXPIRY_MONTH) -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for asset_code, mapped in COMMODITY_OPTIONS_MAP.items():
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
                    'display_code': asset_code,
                    'display_name': display_name,
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
