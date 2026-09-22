from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Loading .env here (rather than relying solely on orchestrator.py to load
# it once for the whole process tree) means every entrypoint picks it up
# automatically -- a streamer run directly (`python -m src.streamers.x`),
# runner.py, or uvicorn invoked by hand all get the same config without
# needing to `export` anything first.
load_dotenv()

# --- IBKR connection -------------------------------------------------------
# Docker is gone -- IB Gateway and QuestDB both run as native
# processes/services on the SAME machine as this code now (either your
# local machine for testing, or the server under Option A). 127.0.0.1 is
# used explicitly rather than "localhost" as the fallback default: on some
# systems "localhost" resolves to the IPv6 loopback (::1) first, which
# fails silently if Gateway/QuestDB only bind their IPv4 socket -- your
# real .env should still set these explicitly either way.
IB_HOST = os.getenv('IB_HOST', '127.0.0.1')
IB_PORT = int(os.getenv('IB_PORT', '7496'))

IB_CONNECT_TIMEOUT_SECONDS = float(os.getenv('IB_CONNECT_TIMEOUT_SECONDS', '10'))
IB_RECONNECT_MIN_BACKOFF_SECONDS = float(os.getenv('IB_RECONNECT_MIN_BACKOFF_SECONDS', '2'))
IB_RECONNECT_MAX_BACKOFF_SECONDS = float(os.getenv('IB_RECONNECT_MAX_BACKOFF_SECONDS', '60'))

# --- QuestDB -----------------------------------------------------------------
QDB_HOST = os.getenv('QDB_HOST', '127.0.0.1')
QDB_PORT = int(os.getenv('QDB_PORT', '8812'))
QDB_ILP_PORT = int(os.getenv('QDB_ILP_PORT', '9009'))
QDB_HTTP_PORT = int(os.getenv('QDB_HTTP_PORT', '9000'))
QDB_USER = os.getenv('QDB_USER', 'admin')
QDB_PASSWORD = os.getenv('QDB_PASSWORD', 'quest')
QDB_DATABASE = os.getenv('QDB_DATABASE', 'qdb')


USE_ILP_INGESTION = os.getenv('USE_ILP_INGESTION', 'true').lower() == 'true'

LOCAL_TZ = ZoneInfo(os.getenv('LOCAL_TIMEZONE', 'Asia/Kolkata'))

DEFAULT_COMMODITY_EXPIRY_MONTH = os.getenv('COMMODITY_EXPIRY_MONTH', '202607')
DEFAULT_REQUEST_DELAY_SECONDS = float(os.getenv('REQUEST_DELAY_SECONDS', '0.2'))
DEFAULT_FLUSH_INTERVAL_SECONDS = float(os.getenv('FLUSH_INTERVAL_SECONDS', '2'))
DEFAULT_BATCH_SIZE = int(os.getenv('BATCH_SIZE', '100'))

CLIENT_IDS = {
    ('commodity', 'spot_all', 'live'): int(os.getenv('CLIENT_ID_COMMODITY_SPOT_ALL_LIVE', '44')),
    ('commodity', 'spot_all', 'delayed'): int(os.getenv('CLIENT_ID_COMMODITY_SPOT_ALL_DELAYED', '45')),
    ('commodity', 'futures_all', 'live'): int(os.getenv('CLIENT_ID_COMMODITY_FUTURES_ALL_LIVE', '41')),
    ('commodity', 'futures_all', 'delayed'): int(os.getenv('CLIENT_ID_COMMODITY_FUTURES_ALL_DELAYED', '43')),
    ('commodity', 'options_all', 'live'): int(os.getenv('CLIENT_ID_COMMODITY_OPTIONS_ALL_LIVE', '46')),
    ('commodity', 'options_all', 'delayed'): int(os.getenv('CLIENT_ID_COMMODITY_OPTIONS_ALL_DELAYED', '47')),
    ('currency', 'spot_all', 'live'): int(os.getenv('CLIENT_ID_CURRENCY_SPOT_ALL_LIVE', '40')),
    ('currency', 'spot_all', 'delayed'): int(os.getenv('CLIENT_ID_CURRENCY_SPOT_ALL_DELAYED', '42')),
    ('currency', 'futures_all', 'live'): int(os.getenv('CLIENT_ID_CURRENCY_FUTURES_ALL_LIVE', '48')),
    ('currency', 'futures_all', 'delayed'): int(os.getenv('CLIENT_ID_CURRENCY_FUTURES_ALL_DELAYED', '49')),
    ('currency', 'options_all', 'live'): int(os.getenv('CLIENT_ID_CURRENCY_OPTIONS_ALL_LIVE', '50')),
    ('currency', 'options_all', 'delayed'): int(os.getenv('CLIENT_ID_CURRENCY_OPTIONS_ALL_DELAYED', '51')),
}

# --- API ---------------------------------------------------------------------
# Comma-separated list of valid API keys. Rotate by adding a new one, updating
# clients, then removing the old one -- no downtime required.
API_KEYS = frozenset(
    key.strip() for key in os.getenv('API_KEYS', '').split(',') if key.strip()
)
API_CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv('API_CORS_ALLOW_ORIGINS', '').split(',')
    if origin.strip()
]

# --- Logging -------------------------------------------------------------
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_JSON = os.getenv('LOG_JSON', 'false').lower() == 'true'
