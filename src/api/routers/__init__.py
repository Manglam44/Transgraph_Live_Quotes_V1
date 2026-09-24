"""Central registry of API routers.

To add a new set of endpoints in the future:
  1. Create src/api/routers/<name>.py with its own `router = APIRouter(...)`
     (copy the shape of questdb_api.py -- same `Depends(require_api_key)`
     pattern if it needs auth).
  2. Import that router below and add it to ALL_ROUTERS.

No change to src/api/app.py is ever needed after that -- it just loops
over ALL_ROUTERS and mounts whatever is registered here.
"""
from __future__ import annotations

from src.api.routers.live_ws import router as live_ws_router
from src.api.routers.questdb_api import router as questdb_router
from src.api.routers.historical import router as historical_router

ALL_ROUTERS = [
    questdb_router,
    live_ws_router,
    historical_router,
]
