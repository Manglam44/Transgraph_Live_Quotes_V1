from __future__ import annotations

import logging
from typing import Callable, Iterable, List

from ib_insync import Future

from src.common.models import ContractSpec

log = logging.getLogger(__name__)


def resolve_future_fallback(ib, contract: Future):
    lookup = Future(
        symbol=contract.symbol,
        exchange=contract.exchange,
        currency=contract.currency,
        multiplier=contract.multiplier,
        tradingClass=contract.tradingClass,
    )
    details = ib.reqContractDetails(lookup)
    if not details:
        return None

    # Pick the nearest available expiry returned by IB for this symbol/exchange.
    contracts = [d.contract for d in details if getattr(d.contract, 'lastTradeDateOrContractMonth', None)]
    if not contracts:
        return details[0].contract

    return sorted(contracts, key=lambda c: c.lastTradeDateOrContractMonth)[0]


def qualify_contracts(ib, specs: Iterable[ContractSpec], request_delay_seconds: float) -> List[ContractSpec]:
    qualified: List[ContractSpec] = []
    for spec in specs:
        fallback_error = None
        label = spec.metadata.get('display_name', spec.metadata.get('display_code', spec.contract_type))

        def try_future_fallback() -> bool:
            nonlocal fallback_error
            if not isinstance(spec.contract, Future):
                return False
            try:
                fallback = resolve_future_fallback(ib, spec.contract)
                if fallback is None:
                    return False

                result = ib.qualifyContracts(fallback)
                if not result:
                    return False

                spec.contract = result[0]
                qualified.append(spec)
                log.info('Qualified %s with fallback expiry %s', label, spec.contract.lastTradeDateOrContractMonth)
                return True
            except Exception as exc:
                fallback_error = exc
                return False

        try:
            result = ib.qualifyContracts(spec.contract)
            if result:
                spec.contract = result[0]
                qualified.append(spec)
                log.info('Qualified %s', label)
            elif not try_future_fallback():
                if fallback_error is not None:
                    log.warning('SKIPPED %s -> no contract match and fallback failed: %s', label, fallback_error)
                else:
                    log.warning('SKIPPED %s -> no contract match', label)
        except Exception as exc:
            if not try_future_fallback():
                log.warning('SKIPPED %s -> %s', label, exc)

        ib.sleep(request_delay_seconds)

    return qualified


def subscribe_market_data(ib, specs: Iterable[ContractSpec], market_data_type: int, request_delay_seconds: float, handler_factory: Callable[[ContractSpec], Callable]) -> None:
    ib.reqMarketDataType(market_data_type)
    for spec in specs:
        ticker = ib.reqMktData(spec.contract, '', False, False)
        ticker.updateEvent += handler_factory(spec)
        log.info('Subscribed %s', spec.metadata.get('display_name', spec.contract_type))
        ib.sleep(request_delay_seconds)


def run_forever(ib, writer, should_stop: Callable[[], bool] = lambda: False) -> None:
    """Runs the IB event loop until the connection drops or shutdown is requested.

    Returns normally when ib becomes disconnected (so the caller can
    reconnect and re-subscribe) or when should_stop() becomes true (so the
    caller can shut down cleanly on SIGTERM). Previously this looped
    unconditionally, which meant a dropped socket left the process spinning
    doing nothing forever with no data flowing and no indication anything
    was wrong.
    """
    while ib.isConnected() and not should_stop():
        ib.sleep(0.5)
        writer.flush_if_due()

    if not ib.isConnected():
        log.warning('IB connection lost -- returning control for reconnect')
