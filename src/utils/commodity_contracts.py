from __future__ import annotations

import json
import logging
import os

from ib_insync import Future, FuturesOption, Stock

from src.common.models import ContractSpec
from src.common.settings import COMMODITY_FUTURES_NEAREST_MONTHS, DEFAULT_COMMODITY_EXPIRY_MONTH

log = logging.getLogger(__name__)

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
        try:
            symbol = mapped['symbol']
            exchange = mapped['exchange']
            currency = mapped['currency']
            display_name = mapped['name']

            # An entry can still pin one specific contract month explicitly
            # -- kept as an escape hatch for cases where you want exactly
            # one named contract, not a chain.
            pinned_expiry = mapped.get('expiry_month')

            metadata = {
                'display_code': asset_code,
                'display_name': display_name,
                'exchange': exchange,
                'instrument_type': 'future',
            }

            if pinned_expiry:
                contract = Future(
                    symbol=symbol,
                    lastTradeDateOrContractMonth=pinned_expiry,
                    exchange=exchange,
                    currency=currency,
                    multiplier=mapped.get('multiplier'),
                    tradingClass=mapped.get('tradingClass'),
                )
            else:
                # NEW default: no pinned month -- build the contract WITHOUT
                # an expiry and mark it for chain expansion. qualify_contracts()
                # in streaming.py sees expand_to_chain=True and turns this
                # ONE spec into COMMODITY_FUTURES_NEAREST_MONTHS specs, one
                # per currently-listed near month, instead of guessing a
                # single expiry_month the way the old code did.
                contract = Future(
                    symbol=symbol,
                    exchange=exchange,
                    currency=currency,
                    multiplier=mapped.get('multiplier'),
                    tradingClass=mapped.get('tradingClass'),
                )
                metadata['expand_to_chain'] = True
                metadata['chain_months'] = COMMODITY_FUTURES_NEAREST_MONTHS

            specs.append(ContractSpec(contract_type='future', contract=contract, metadata=metadata))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('Skipping malformed commodity future entry %r: %s', asset_code, exc)
            continue
    return specs


def load_commodity_spot_proxies() -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for asset_code, mapped in COMMODITY_SPOT_PROXY_MAP.items():
        try:
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
        except (KeyError, TypeError, ValueError) as exc:
            log.warning('Skipping malformed commodity spot proxy entry %r: %s', asset_code, exc)
            continue
    return specs


def load_commodity_options(expiry_month: str = DEFAULT_COMMODITY_EXPIRY_MONTH) -> list[ContractSpec]:
    specs: list[ContractSpec] = []
    for asset_code, mapped in COMMODITY_OPTIONS_MAP.items():
        try:
            symbol = mapped['symbol']
            exchange = mapped['exchange']
            currency = mapped['currency']
            display_name = mapped['name']
            strike = float(mapped['strike'])  # this line is what crashed you
            right = mapped['right']
            resolved_expiry = mapped.get('expiry_month') or expiry_month  # NEW: same per-entry override as futures

            specs.append(
                ContractSpec(
                    contract_type='futures_option',
                    contract=FuturesOption(
                        symbol=symbol,
                        lastTradeDateOrContractMonth=resolved_expiry,
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
                        'expiry': resolved_expiry,
                        'strike': str(strike),
                        'right': right,
                        'underlying': symbol,
                    },
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            # NEW: this is the fix for your crash. mapped['strike'] was None
            # for at least one entry in commodity_options_map.json, which
            # made float(None) raise TypeError and take down the whole
            # commodity_options streamer before it connected to anything.
            # Now that one bad entry is skipped and logged instead --
            # check the warning below for which asset_code to go fix in
            # the JSON file itself (the actual data bug still needs fixing
            # there; this just stops it from being fatal).
            log.warning('Skipping malformed commodity option entry %r: %s', asset_code, exc)
            continue
    return specs
