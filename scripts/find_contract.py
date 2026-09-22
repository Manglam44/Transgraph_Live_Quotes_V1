"""find_contract.py -- ask IB Gateway/TWS what contracts actually exist,
instead of guessing tradingClass/expiry/strike from memory.

Usage examples:

    # All CPO futures IBKR actually lists (real expiries, tradingClass, multiplier):
    python scripts/find_contract.py --symbol CPO --sectype FUT --exchange CME --currency USD

    # All CPO *options* (this is the one we need for the options_map entry):
    python scripts/find_contract.py --symbol CPO --sectype FOP --exchange CME --currency USD

Leave --expiry/--strike/--right off entirely for the first pass -- IB
returns every real combination it has, which is what you copy into the
JSON map files instead of a guessed value.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running this as `python scripts/find_contract.py` only puts scripts/ itself
# on sys.path, not the project root -- so `from src...` fails. Add the
# project root (one level up from this file) explicitly, the same way it's
# already implicitly on the path when running `python -m src.streamers.x`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ib_insync import Contract

from src.common.ib_client import connect_ib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', help='Broad symbol search instead of a specific contract lookup -- '
                                          'use this FIRST when a --sectype query returns nothing, to see '
                                          'what IB actually recognizes for a given text (e.g. "palm" or "CPO").')
    parser.add_argument('--symbol')
    parser.add_argument('--sectype', choices=('FUT', 'FOP', 'STK', 'CASH'))
    parser.add_argument('--exchange')
    parser.add_argument('--currency', default='USD')
    parser.add_argument('--expiry', default='', help='Optional: narrow to one contract month (YYYYMM)')
    parser.add_argument('--client-id', type=int, default=998, help='Different from test_ib.py\'s 999')
    args = parser.parse_args()
    if not args.search and not (args.symbol and args.sectype and args.exchange):
        parser.error('Either --search TEXT, or --symbol/--sectype/--exchange together, are required.')
    return args


def run_search(ib, text: str) -> None:
    print(f'Searching IB for symbols matching: {text!r}\n')
    matches = ib.reqMatchingSymbols(text)
    if not matches:
        print('No matches at all -- try a shorter or different search term.')
        return
    for m in matches:
        c = m.contract
        types = ', '.join(m.derivativeSecTypes) if m.derivativeSecTypes else '(none)'
        print(f'  symbol={c.symbol}  secType={c.secType}  exchange={c.primaryExchange or c.exchange}  '
              f'currency={c.currency}  description={m.contract.description or ""}  '
              f'available derivative types={types}')


def main() -> None:
    args = parse_args()

    ib = connect_ib(args.client_id)
    try:
        if args.search:
            run_search(ib, args.search)
            return

        contract = Contract(
            symbol=args.symbol,
            secType=args.sectype,
            exchange=args.exchange,
            currency=args.currency,
        )
        if args.expiry:
            contract.lastTradeDateOrContractMonth = args.expiry

        print(f'Requesting contract details for: {contract}\n')
        details = ib.reqContractDetails(contract)

        if not details:
            print('No contracts found at all -- check symbol/exchange/currency/sectype '
                  'are correct, or that this exchange/product is enabled on your account.')
            return

        print(f'Found {len(details)} matching contract(s):\n')
        seen_trading_classes = set()
        for d in details:
            c = d.contract
            seen_trading_classes.add(c.tradingClass)
            line = (
                f'  expiry={c.lastTradeDateOrContractMonth}  '
                f'tradingClass={c.tradingClass}  '
                f'multiplier={c.multiplier}  '
                f'localSymbol={c.localSymbol}'
            )
            if args.sectype == 'FOP':
                line += f'  strike={c.strike}  right={c.right}'
            print(line)

        print(f'\ndistinct tradingClass value(s) seen: {sorted(seen_trading_classes)}')
        if args.sectype == 'FOP' and not args.expiry:
            print('(Options often only list a handful of near-term expiries -- '
                  're-run with --expiry <one of the ones printed above> if this '
                  'list looks truncated, to also see the full strike range for that month.)')
    finally:
        ib.disconnect()


if __name__ == '__main__':
    main()