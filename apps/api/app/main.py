"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from app.academics.api import router as academics_router
from app.admin.api import router as admin_router
from app.apaar.api import router as apaar_router
from app.audit.api import router as audit_router
from app.breakglass.api import router as breakglass_router
from app.brief.api import router as brief_router
from app.certificates.api import public_router as certificates_public_router
from app.certificates.api import router as certificates_router
from app.changes.api import router as changes_router
from app.circulars.api import router as circulars_router
from app.compliance.api import router as compliance_router
from app.compliance.packs_api import router as compliance_packs_router
from app.compliance.safety_api import router as compliance_safety_router
from app.core import ratelimit
from app.core.config import DeploymentMode, Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import install_middleware
from app.core.telemetry import setup_telemetry
from app.disclosure.api import public_router as disclosure_public_router
from app.disclosure.api import router as disclosure_router
from app.documents.api import router as documents_router
from app.dq.api import router as dq_router
from app.dq.api import tasks_router as dq_tasks_router
from app.dq.fix_letter_api import router as dq_fix_letter_router
from app.dq.followup_api import router as dq_followup_router
from app.dq.udise_api import router as udise_router
from app.exports.api import router as exports_router
from app.exports.pack_api import router as export_packs_router
from app.extraction.api import router as extraction_router
from app.fees import webhook as payments_webhook
from app.fees.api import router as fees_router
from app.fees.api_addons import router as fees_addons_router
from app.fees.api_arrears import router as fees_arrears_router
from app.fees.api_bank_matching import router as fees_bank_matching_router
from app.fees.api_family import router as fees_family_router
from app.fees.api_online import router as payments_router
from app.fees.api_online import webhook_router as payments_webhook_router
from app.fees.api_reports import router as fees_reports_router
from app.fees.api_tax import router as fees_tax_router
from app.fees.api_upi import router as fees_upi_router
from app.identity.api import router as identity_router
from app.imports.api import router as imports_router
from app.insights.api import router as insights_router
from app.knowledge import service as knowledge
from app.knowledge.api import router as knowledge_router
from app.knowledge.gaps_api import router as knowledge_gaps_router
from app.messaging import webhook as messaging_webhook
from app.messaging.api import router as messaging_router
from app.messaging.api import webhook_router as messaging_webhook_router
from app.notifications.api import invitations_router
from app.notifications.api import router as notifications_router
from app.platform import payment_webhook as platform_payment_webhook
from app.platform.api import fleet_router
from app.platform.api import router as platform_router
from app.platform.tenant_api import router as platform_tenant_router
from app.privacy.api import router as privacy_router
from app.privacy.breach_api import router as breach_router
from app.privacy.consents_api import router as consents_router
from app.privacy.holds_api import router as holds_router
from app.privacy.readiness_api import router as readiness_router
from app.public.api import router as public_router
from app.public.config import rules as public_rules
from app.students.api import router as students_router
from app.tally.api import agent_router as tally_agent_router
from app.tally.api import router as tally_router
from app.tally.api_vouchers import router as tally_vouchers_router
from app.tenancy.api import router as tenancy_router

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:  # noqa: PLR0915 - one line per router
    settings = settings or get_settings()
    setup_logging(settings)
    # Claude safety lock: staging/prod refuse to start while a models.yaml role uses Anthropic
    # without SOS_ANTHROPIC_ZDR_CONFIRMED (docs/10 §11).
    knowledge.check_provider_agreements(settings)
    docs_enabled = not settings.is_production_like
    app = FastAPI(
        title="SchoolOS API",
        version=settings.version,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
        docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
        redoc_url=None,
        # Every route may answer 429 (docs/09 §2.7; only the health checks are exempt).
        responses=ratelimit.OPENAPI_429,
    )
    install_error_handlers(app)
    # R2-1C (ADR-0043): Meta's webhook deliveries may be up to 3 MB (app/public/config.yaml); the
    # signature guard enforces the provider's limit itself.
    meta_limit = public_rules().webhook_providers["meta"].max_body_bytes
    install_middleware(app, settings, path_body_limits={"/api/v1/webhooks/meta": meta_limit})
    app.include_router(health_router)
    # Tenant (school-side) routes stay mounted in both shared and dedicated deployments.
    app.include_router(identity_router)
    app.include_router(tenancy_router)
    app.include_router(students_router)
    app.include_router(audit_router)
    app.include_router(documents_router)
    app.include_router(imports_router)
    app.include_router(udise_router)  # FO-02 UDISE+ correction requests (before dq paths)
    app.include_router(dq_router)
    app.include_router(dq_followup_router)
    app.include_router(dq_fix_letter_router)  # FA-08 fix-owner letters
    # R3-1D (FR-RDY-010): link a circular task to a portal calendar event.
    app.include_router(dq_tasks_router)
    app.include_router(platform_tenant_router)
    app.include_router(notifications_router)
    app.include_router(invitations_router)
    app.include_router(breakglass_router)
    app.include_router(changes_router)
    app.include_router(extraction_router)
    app.include_router(exports_router)
    app.include_router(export_packs_router)  # FO-06 enrolment statistics, FO-11 scheme packs
    app.include_router(admin_router)
    app.include_router(certificates_router)
    app.include_router(apaar_router)
    # R3-1B consents and breaches (ADR-0049, ADR-0050)
    app.include_router(consents_router)
    app.include_router(breach_router)
    app.include_router(knowledge_router)
    app.include_router(knowledge_gaps_router)  # FA-06 knowledge gaps
    app.include_router(circulars_router)
    app.include_router(academics_router)
    app.include_router(insights_router)
    # R3-1A privacy requests, notices and record classes (ADR-0048).
    app.include_router(privacy_router)
    # FC-03 / FP-13 DPDP readiness checklist, notice at admission, notice on WhatsApp.
    app.include_router(readiness_router)
    # FC-12 legal holds (ADR-0059).
    app.include_router(holds_router)
    # FA-05 weekly management brief (FR-BRF-001..015).
    app.include_router(brief_router)
    # FC-01 school compliance certificate register.
    app.include_router(compliance_router)
    # FC-09 safety drills and checks, FC-10 policy library with staff acknowledgement.
    app.include_router(compliance_safety_router)
    # FC-07 / FO-12 inspection readiness packs.
    app.include_router(compliance_packs_router)
    # R2-1B fees (ADR-0042): every route answers 404 module_hidden while fees are off.
    app.include_router(fees_router)
    # R2-2E UPI and bank statements (ADR-0042 decision 9).
    app.include_router(fees_upi_router)
    app.include_router(fees_tax_router)  # FM-06 tuition fee certificates
    app.include_router(fees_arrears_router)
    app.include_router(fees_reports_router)  # FM-04 accountant and management reports
    app.include_router(fees_addons_router)  # FM-02 add-on fees
    app.include_router(fees_family_router)  # FM-07 family counter, FM-10 fee structure notice
    app.include_router(fees_bank_matching_router)  # FM-08 cheque status and deposit list
    # R2-2D online payments (ADR-0042): the school's Razorpay account, payment links, the
    # Razorpay webhook (require_webhook_signature("razorpay")) and online reconciliation.
    app.include_router(payments_router)
    app.include_router(payments_webhook_router)
    payments_webhook.install()
    # M6 Tally connector (ADR-0032 Proposed): behind the per-school flag
    # tally.connector.enabled (default off, 404). The agent routes are device-signed.
    app.include_router(tally_router)
    app.include_router(tally_agent_router)
    # FM-03: the Tally voucher file (fees gate; SchoolOS never writes to Tally) and the
    # ledger-vs-Tally check (also needs the connector flag).
    app.include_router(tally_vouchers_router)
    # R2 public surface (ADR-0047): parent link pages through the BFF's service token only;
    # every route is pinned in app/public/public_routes.yaml.
    app.include_router(public_router)
    # R2-1C WhatsApp messaging (ADR-0043): staff routes answer 404 module_unavailable unless
    # SOS_MESSAGING_ENABLED on the shared tier; the Meta webhook is pinned in public_routes.yaml.
    app.include_router(messaging_router)
    app.include_router(messaging_webhook_router)
    messaging_webhook.install()
    # R2-1A: certificate verification by code (ADR-0045), pinned in public_routes.yaml.
    app.include_router(certificates_public_router)
    # R3-1C CBSE Appendix IX disclosure (ADR-0051): staff routes answer 404 module_hidden
    # outside CBSE schools; the public page routes are pinned in public_routes.yaml.
    app.include_router(disclosure_router)
    app.include_router(disclosure_public_router)
    # Control plane + fleet heartbeat: shared deployment only (ADR-0017); 404 on dedicated hosts.
    if settings.deployment_mode is DeploymentMode.SHARED:
        app.include_router(platform_router)
        app.include_router(fleet_router)
        # FA-13: Razorpay webhook of SchoolOS's own account (SchoolOS invoices), pinned in
        # public_routes.yaml; 404 until SOS_PLATFORM_PAYMENTS_ENABLED.
        app.include_router(platform_payment_webhook.router)
        platform_payment_webhook.install()
    setup_telemetry(app, settings)
    return app


app = create_app()
