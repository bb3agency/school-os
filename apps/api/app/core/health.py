"""Liveness and readiness endpoints (10 §6). Public, return no data beyond status.

These are the ONLY routes allowlisted without ``require()`` (CLAUDE.md §6.2).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal

import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine

router = APIRouter(tags=["health"])

PUBLIC_PATHS: frozenset[str] = frozenset({"/healthz", "/readyz"})


class HealthOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "unavailable"]


class ReadyOut(HealthOut):
    checks: dict[str, Literal["ok", "fail"]]


def check_database() -> bool:
    try:
        with get_engine("app").connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_redis() -> bool:
    try:
        client = redis.Redis.from_url(
            get_settings().redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


def get_checks() -> dict[str, Callable[[], bool]]:
    return {"database": check_database, "redis": check_redis}


@router.get("/healthz", response_model=HealthOut)
def healthz() -> HealthOut:
    """Liveness: the process is up. No dependencies are checked."""
    return HealthOut(status="ok")


@router.get("/readyz", response_model=ReadyOut, responses={503: {"model": ReadyOut}})
def readyz(
    checks: Annotated[dict[str, Callable[[], bool]], Depends(get_checks)],
) -> JSONResponse:
    """Readiness: database and Redis/Valkey reachable."""
    results: dict[str, Literal["ok", "fail"]] = {
        name: ("ok" if fn() else "fail") for name, fn in checks.items()
    }
    ready = all(v == "ok" for v in results.values())
    body = ReadyOut(status="ok" if ready else "unavailable", checks=results)
    return JSONResponse(body.model_dump(), status_code=200 if ready else 503)
