# Final Project Structure -- Docker Fully Removed

This is the real, tested project. No Docker artifacts remain anywhere in
this tree -- QuestDB, IB Gateway, the API, and all six streamers run as
native processes everywhere (plain venv locally, venv + systemd on the
server).

## Full tree

```
app/
├── requirements.txt                      # clean -- pinned deps only, no run-notes
├── _env.example                          # copy to .env, edit, chmod 600
├── .gitignore                            # keeps __pycache__/.env/venv out of git
├── runner.py                             # local multi-stream launcher
├── orchestrator.py                       # server supervisor (API + 6 streamers)
├── PROJECT_STRUCTURE.md                  # this file
├── LOCAL_TESTING.md                      # full local walkthrough, start here for local
│
├── scripts/
│   └── test_ib.py                        # standalone IB connectivity check
│
├── deploy/
│   ├── DEPLOYMENT.md                     # full server setup, start here for server
│   ├── 00_ec2_bootstrap.sh
│   ├── questdb.service
│   ├── ibgateway.service
│   ├── ibkr-orchestrator.service
│   └── start_ibgateway.sh
│
└── src/
    ├── __init__.py
    ├── api/
    │   ├── __init__.py
    │   ├── app.py                        # FastAPI() instance, mounts ALL_ROUTERS
    │   ├── auth.py
    │   └── routers/
    │       ├── __init__.py               # add new endpoints here
    │       └── questdb_api.py
    ├── common/
    │   ├── __init__.py
    │   ├── settings.py                   # loads .env itself; 127.0.0.1 fallbacks
    │   ├── ib_client.py
    │   ├── base_streamer.py
    │   ├── streaming.py
    │   ├── questdb.py
    │   ├── models.py
    │   ├── tables.py
    │   ├── logging_config.py
    │   └── quote_utils.py
    ├── utils/
    │   ├── __init__.py
    │   ├── validators.py
    │   ├── commodity_contracts.py
    │   └── currency_contracts.py
    ├── streamers/
    │   ├── __init__.py
    │   ├── commodity_spot.py
    │   ├── commodity_futures.py
    │   ├── commodity_options.py
    │   ├── currency_spot.py
    │   ├── currency_futures.py
    │   └── currency_options.py
    └── data/
        ├── commodity_futures_map.json
        ├── commodity_options_map.json
        ├── commodity_spot_proxy_map.json
        ├── currency_futures_map.json
        ├── currency_options_map.json
        └── currency_pairs.json
```

## What this final cleanup pass removed, vs. what was there before

Across the full review (every file you sent, batch by batch):

- **`docker-compose.yml`** -- deleted. You confirmed `Dockerfile.streamer`
  and `Dockerfile.api` don't exist in your current project, so there was
  nothing left that Docker config could even build against. The
  "Rollback to Docker" section in `deploy/DEPLOYMENT.md` was removed to
  match -- there's no supported Docker path anymore, by design.
- **16 stray `.pyc` files / `__pycache__` folders** -- none of these
  belong in the project; they're regenerated automatically the moment
  Python imports a `.py` file. The new `.gitignore` stops them from
  reappearing in anything you commit or share going forward.
- **Terminal run-notes that had been pasted into `requirements.txt`** --
  moved into `LOCAL_TESTING.md`, which is the correct home for
  "how do I run this" documentation. `requirements.txt` is back to being
  just pinned dependencies.
- **`localhost` vs `127.0.0.1` inconsistency** -- `settings.py`,
  `orchestrator.py`, and `_env.example` now all default to `127.0.0.1`
  consistently for both `IB_HOST` and `QDB_HOST`, avoiding the IPv6
  loopback ambiguity `localhost` can introduce on some systems. Your real
  `.env` sets these explicitly either way, so this only hardens the
  fallback behavior.
- **`settings.py` now loads `.env` itself** (`load_dotenv()` at import
  time) -- carried forward from your working local version. This means
  every entrypoint (a streamer run directly, `runner.py`, `uvicorn` run by
  hand) picks up `.env` automatically, not just `orchestrator.py`.

Everything else -- every file in `src/common/`, `src/utils/`,
`src/streamers/`, `src/data/`, `src/api/`, plus `runner.py`,
`scripts/test_ib.py`, and all of `deploy/` -- was confirmed correct and
unchanged across the full review and is carried forward as-is.

## Where to start

- **Testing on your own machine right now:** `LOCAL_TESTING.md`
- **Deploying to the server (Option A -- IB Gateway headless on the
  server itself):** `deploy/DEPLOYMENT.md`
- **Adding a new API endpoint later:** see the section in
  `deploy/DEPLOYMENT.md` -- drop a file in `src/api/routers/`, register it
  in that folder's `__init__.py`, restart. Nothing else changes.

## Reminder

Your `_env` file uploaded during this review contained a real `API_KEYS`
value that is now in chat history. Rotate it (`python -c "import secrets;
print(secrets.token_urlsafe(32))"`) before this goes anywhere near
production, if you haven't already.
