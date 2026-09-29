"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.admin.api import router as admin_router
from app.audit.api import router as audit_router
from app.breakglass.api import router as breakglass_router
from app.certificates.api import router as certificates_router
from app.changes.api import router as changes_router
from app.circulars.api import router as circulars_router
from app.core.config import DeploymentMode, Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import install_middleware
from app.core.telemetry import setup_telemetry
from app.documents.api import router as documents_router
from app.dq.api import router as dq_router
from app.exports.api import router as exports_router
from app.extraction.api import router as extraction_router
from app.identity.api import router as identity_router
from app.imports.api import router as imports_router
from app.knowledge.api import router as knowledge_router
from app.notifications.api import invitations_router
from app.notifications.api import router as notifications_router
from app.platform.api import fleet_router
from app.platform.api import router as platform_router
from app.platform.tenant_api import router as platform_tenant_router
from app.students.api import router as students_router
from app.tally.api import agent_router as tally_agent_router
from app.tally.api import router as tally_router
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
    app.include_router(documents_router)
    app.include_router(imports_router)
    app.include_router(dq_router)
    app.include_router(platform_tenant_router)
    app.include_router(notifications_router)
    app.include_router(invitations_router)
    app.include_router(breakglass_router)
    app.include_router(changes_router)
    app.include_router(extraction_router)
    app.include_router(exports_router)
    app.include_router(admin_router)
    app.include_router(certificates_router)
    app.include_router(knowledge_router)
    app.include_router(circulars_router)
    # M6 Tally connector (ADR-0032 Proposed): behind the per-school flag
    # tally.connector.enabled (default off, 404). The agent routes are device-signed.
    app.include_router(tally_router)
    app.include_router(tally_agent_router)
    # Control plane + fleet heartbeat: shared deployment only (ADR-0017); 404 on dedicated hosts.
    if settings.deployment_mode is DeploymentMode.SHARED:
        app.include_router(platform_router)
        app.include_router(fleet_router)
    setup_telemetry(app, settings)
    return app


app = create_app()
