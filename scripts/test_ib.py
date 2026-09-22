from ib_insync import IB, Future
import math

IB_HOST = "127.0.0.1"
IB_PORT = 7496          # 7496 = TWS live, 7497 = TWS paper, 4001 = Gateway live, 4002 = Gateway paper
CLIENT_ID = 999          # Fine to use -- doesn't collide with the reserved production IDs (40-51)

def valid(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))

ib = IB()
print(f"Connecting to IBKR at {IB_HOST}:{IB_PORT} ...")

try:
    ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=10)
    print("Connected:", ib.isConnected())
    print("Server version:", ib.client.serverVersion())

    contract = Future(
        symbol="GC",
        lastTradeDateOrContractMonth="202612",
        exchange="COMEX",
        currency="USD",
    )

    print("Qualifying contract...")
    contracts = ib.qualifyContracts(contract)
    print("Qualified:", contracts)

    if not contracts:
        print("Contract qualification failed -- check symbol/exchange/expiry.")
    else:
        # Explicit, rather than relying on IBKR's default: 1 = live, 3 = delayed.
        # Being explicit here matters because on a live account with no
        # active COMEX subscription, live data silently never arrives --
        # this script won't tell you *why* bid/ask stay None, only *that* they do.
        ib.reqMarketDataType(1)
        ticker = ib.reqMktData(contracts[0], "", False, False)

        for i in range(10):
            ib.sleep(1)
            bid = ticker.bid if valid(ticker.bid) else None
            ask = ticker.ask if valid(ticker.ask) else None
            last = ticker.last if valid(ticker.last) else None
            volume = ticker.volume if valid(ticker.volume) else None
            print(f"[{i+1}/10] Bid={bid}, Ask={ask}, Last={last}, Volume={volume}")

        ib.cancelMktData(contracts[0])
finally:
    ib.disconnect()
    print("Disconnected")
