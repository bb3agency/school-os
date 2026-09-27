"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.audit.api import router as audit_router
from app.core.config import DeploymentMode, Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import install_middleware
from app.core.telemetry import setup_telemetry
from app.identity.api import router as identity_router
from app.platform.api import fleet_router
from app.platform.api import router as platform_router
from app.platform.tenant_api import router as platform_tenant_router
from app.students.api import router as students_router
from app.tenancy.api import router as tenancy_router

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings)
    docs_enabled = not settings.is_production_like
    app = FastAPI(
        title="SchoolOS API",
        version=settings.version,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
        docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
        redoc_url=None,
    )
    install_error_handlers(app)
    install_middleware(app, settings)
    app.include_router(health_router)
    # Tenant (school-side) routes stay mounted in both shared and dedicated deployments.
    app.include_router(identity_router)
    app.include_router(tenancy_router)
    app.include_router(students_router)
    app.include_router(audit_router)
    app.include_router(platform_tenant_router)
    # Control plane + fleet heartbeat: shared deployment only (ADR-0017); 404 on dedicated hosts.
    if settings.deployment_mode is DeploymentMode.SHARED:
        app.include_router(platform_router)
        app.include_router(fleet_router)
    setup_telemetry(app, settings)
    return app


app = create_app()
