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


def resolve_future_chain(ib, contract: Future, months: int) -> List[Future]:
    """Same lookup-without-a-fixed-expiry technique as resolve_future_fallback,
    but returns the nearest `months` currently-listed contracts instead of
    just the single nearest one -- used to subscribe to a whole near-dated
    chain (e.g. corn's next 6 listed months) rather than guessing one."""
    lookup = Future(
        symbol=contract.symbol,
        exchange=contract.exchange,
        currency=contract.currency,
        multiplier=contract.multiplier,
        tradingClass=contract.tradingClass,
    )
    details = ib.reqContractDetails(lookup)
    if not details:
        return []

    contracts = [d.contract for d in details if getattr(d.contract, 'lastTradeDateOrContractMonth', None)]
    contracts.sort(key=lambda c: c.lastTradeDateOrContractMonth)
    return contracts[:months]


def qualify_contracts(ib, specs: Iterable[ContractSpec], request_delay_seconds: float) -> List[ContractSpec]:
    qualified: List[ContractSpec] = []
    for spec in specs:
        fallback_error = None
        label = spec.metadata.get('display_name', spec.metadata.get('display_code', spec.contract_type))

        # NEW: commodity futures loaded without a pinned expiry_month are
        # marked expand_to_chain=True by load_commodity_futures(). Instead
        # of qualifying one (possibly wrong) contract month, fetch the
        # nearest N currently-listed months and subscribe to all of them --
        # this spec becomes multiple qualified specs, one per month.
        if isinstance(spec.contract, Future) and spec.metadata.get('expand_to_chain'):
            months = spec.metadata.get('chain_months', 6)
            try:
                chain = resolve_future_chain(ib, spec.contract, months)
            except Exception as exc:
                log.warning('SKIPPED %s -> chain lookup failed: %s', label, exc)
                ib.sleep(request_delay_seconds)
                continue

            if not chain:
                log.warning('SKIPPED %s -> no listed contracts found for chain expansion', label)
                ib.sleep(request_delay_seconds)
                continue

            for contract in chain:
                try:
                    result = ib.qualifyContracts(contract)
                except Exception as exc:
                    log.warning('SKIPPED %s %s -> %s', label, contract.lastTradeDateOrContractMonth, exc)
                    continue
                if not result:
                    log.warning('SKIPPED %s %s -> no contract match', label, contract.lastTradeDateOrContractMonth)
                    continue

                chain_spec = ContractSpec(
                    contract_type=spec.contract_type,
                    contract=result[0],
                    metadata={**spec.metadata, 'expiry': result[0].lastTradeDateOrContractMonth},
                )
                qualified.append(chain_spec)
                log.info('Qualified %s %s', label, result[0].lastTradeDateOrContractMonth)

            ib.sleep(request_delay_seconds)
            continue  # this spec is fully handled -- skip the single-contract path below

        # --- existing single-contract path, unchanged, for everything else
        # (currency futures, spot proxies, options, and any commodity future
        # with an explicit pinned expiry_month in its JSON entry) ---
        fallback_error = None

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
