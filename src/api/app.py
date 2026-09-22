from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import ALL_ROUTERS
from src.common.logging_config import configure_logging
from src.common.questdb import connect_questdb
from src.common.settings import API_CORS_ALLOW_ORIGINS

configure_logging('api')

app = FastAPI(
    title='IB Project QuestDB API',
    description='FastAPI endpoints to query QuestDB tables used by the IB project.',
    version='1.0.0',
)

# API_CORS_ALLOW_ORIGINS defaults to an empty list (no cross-origin access)
# unless explicitly set via env -- the original wildcard "*" is not safe now
# that the API requires an API key, since a wildcard + credentialed requests
# from an unexpected origin is exactly the CSRF-style risk CORS exists to
# prevent.
app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=['GET', 'POST'],
    allow_headers=['X-API-Key', 'Content-Type'],
)

# Every router registered in src/api/routers/__init__.py gets mounted here
# automatically -- adding a new set of endpoints later needs no change to
# this file. See src/api/routers/__init__.py for the "how to add one" note.
for router in ALL_ROUTERS:
    app.include_router(router)


@app.get('/health', tags=['health'])
def health() -> dict[str, str]:
    """Unauthenticated liveness/readiness check for load balancers and the
    systemd/health-check tooling that replaced Docker healthchecks.
    Verifies QuestDB is actually reachable, not just that the FastAPI
    process is up."""
    try:
        conn = connect_questdb()
        conn.close()
        return {'status': 'ok', 'questdb': 'reachable'}
    except Exception as exc:
        return {'status': 'degraded', 'questdb': f'unreachable: {exc}'}
