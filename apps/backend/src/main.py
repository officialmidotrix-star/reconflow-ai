"""
ReconFlow AI - application entry point.

Run with: uvicorn main:app --reload (from apps/backend/src/)

Everything module-specific lives in the 16 modules themselves; everything
here is composition - verifying the schema, including routers, and wiring
every placeholder dependency to its real implementation, per
bootstrap/wiring.py.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bootstrap.config import get_allowed_origins
from bootstrap.schema import ensure_schema
from bootstrap.seed import run_operator_seed
from bootstrap.seed_demo import run_demo_seed
from bootstrap.wiring import register_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    run_operator_seed()
    run_demo_seed()  # only if RECONFLOW_SEED_DEMO_DATA=1: clearly labelled test data
    # Route handlers run in a thread pool; keep it within the database pool
    # (RECONFLOW_DB_POOL_SIZE + RECONFLOW_DB_MAX_OVERFLOW, default 20) so
    # threads never starve waiting for a connection.
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = int(
        os.environ.get("RECONFLOW_THREAD_LIMIT", "16")
    )
    yield


app = FastAPI(
    title="ReconFlow AI",
    description="Delivery-platform reconciliation for restaurants.",
    lifespan=lifespan,
)

# The API takes its credential as a bearer token in an Authorization
# header, never a cookie, so a wildcard origin here can't be leveraged
# for the ambient-credential CSRF-style attacks wildcard CORS enables on
# cookie-authenticated APIs. Still pinned to an explicit allowlist rather
# than "*" as ordinary hygiene - see get_allowed_origins().
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

register_all(app)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
