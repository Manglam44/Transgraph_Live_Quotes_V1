# Local Testing (No Server, No Docker)

Everything here runs on your own machine against your own TWS/IB Gateway
and a locally-installed QuestDB. This is the fastest way to confirm the
pipeline works before touching the server.

## 1. One-time setup

```powershell
# Windows PowerShell, from the project root
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy _env.example .env
```

```bash
# Mac/Linux
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp _env.example .env
```

Edit `.env`:
- `IB_HOST=127.0.0.1`, `IB_PORT=` whichever port your TWS/Gateway + account
  type combination actually uses (see table below)
- `QDB_HOST=127.0.0.1`
- `API_KEYS=` any placeholder value for local testing

| App | Live | Paper |
|-----|------|-------|
| TWS | 7496 | 7497 |
|IB-Gate| 4001 | 4002 |

## 2. Start QuestDB locally

Download the native binary (no Docker) from
`https://github.com/questdb/questdb/releases`, extract it, then:

```bash
./bin/questdb.sh start      # Linux/Mac
bin\questdb.exe             # Windows
```

Confirm at `http://localhost:9000`.

## 3. Confirm TWS/Gateway API access is enabled

File -> Global Configuration -> API -> Settings -> check "Enable ActiveX
and Socket Clients". Leave "Allow connections from localhost only" checked
-- for local testing this stays checked, unlike the server setup.

## 4. Verify the IB connection before running any streamer

```bash
python scripts/test_ib.py
```

Edit the `IB_PORT` constant near the top of that script first if you're
not on 7496 (TWS live). `Bid=None, Ask=None` for the full 10 seconds is
normal on a paper account with no live subscription -- it only confirms
the connection itself works.

## 5. Run the pipeline

Two terminals, both with the venv activated:

**Terminal 1 -- all six streamers:**
```powershell
.\venv\Scripts\Activate.ps1
python runner.py --asset all --flow all --mode live
```
(use `--mode delayed` if you don't have live data entitlements for the
symbols involved)

**Terminal 2 -- the API:**
```powershell
cd "<your project folder>"
.\venv\Scripts\Activate.ps1
python -m uvicorn src.api.app:app --reload --port 8000
```

`Ctrl+C` in Terminal 1 stops all six streamers cleanly (runner.py forwards
the interrupt and waits for each to exit).

## 6. Check it's actually working

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: LpI4LLnpgvZjut8STonAlFesUgtRnDagzU0rKj2ICNo" "http://localhost:8000/questdb/tables"
curl -G "http://localhost:9000/exec" --data-urlencode "query=SELECT * FROM currency_delayed LIMIT 10"
```

## 7. (Optional) Run the full orchestrator locally instead of runner.py + uvicorn separately

```bash
# set a local log dir first -- orchestrator.py defaults to /var/log/ibkr-pipeline,
# which won't exist on your machine
export IBKR_LOG_DIR=./logs        # Mac/Linux
set IBKR_LOG_DIR=.\logs           # Windows cmd
$env:IBKR_LOG_DIR = ".\logs"      # Windows PowerShell

python orchestrator.py
```

This runs the API + all six streamers under the same supervisor (restart
on crash, coordinated shutdown) the server uses -- useful for testing that
behavior specifically, though `runner.py` + `uvicorn --reload` is more
convenient for day-to-day development since `--reload` picks up code
changes automatically.

## Common local pitfalls

- **Port mismatch** between `.env`'s `IB_PORT` and what TWS/Gateway is
  actually listening on is the most common cause of a hang in
  `test_ib.py` or the streamers.
- **Client ID collisions** if you run `test_ib.py` twice at once, or
  alongside TWS's own API examples -- bump the `CLIENT_ID` constant.
- **QuestDB port already in use** from an old Docker container --
  `docker stop questdb` (or `docker rm` it) first if you ever ran the
  original Dockerized setup on this machine.

## Moving to the server afterward

Once this works locally, `deploy/DEPLOYMENT.md` covers the server setup
(Option A: IB Gateway headless on the server itself). Nothing in `src/`
changes between local and server -- only `.env` values and how the
processes are supervised (venv+manual here, venv+systemd there).
