"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.health import router as health_router

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    docs_enabled = not settings.is_production_like
    app = FastAPI(
        title="SchoolOS API",
        version=settings.version,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
        docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
        redoc_url=None,
    )
    install_error_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
