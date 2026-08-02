"""
ReconFlow AI - application entry point.

Run with: uvicorn main:app --reload (from apps/backend/src/)

Everything module-specific lives in the 16 modules themselves; everything
here is composition - creating tables, including routers, and wiring
every placeholder dependency to its real implementation, per
bootstrap/wiring.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bootstrap.config import get_allowed_origins
from bootstrap.database import create_all_tables
from bootstrap.wiring import register_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all_tables()
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
