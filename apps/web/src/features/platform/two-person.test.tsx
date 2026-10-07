import type { Announcement } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { AnnouncementsScreen } from "./AnnouncementsView";
import { BreakGlassScreen } from "./OperationsViews";
import { offboardRequestExpired, SchoolDetailScreen } from "./SchoolDetailView";

/**
 * Two-person requests in the operator panel (audit 2026-10-05 A-13, A-14 and the critical
 * announcement hardening): requests show when they expire, can be withdrawn where they are
 * shown, an expired offboarding request cannot be approved but can be replaced, emergency
 * break-glass needs an incident or legal reason, and a critical announcement waits for a
 * second operator's approval. The clock is pinned. Synthetic data only.
 */

const NOW = new Date("2026-10-07T06:00:00Z");
const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const OTHER = "0192f3a4-0000-7000-8000-0000000000f2";
const BG = "0192f3a4-0000-7000-8000-00000000f101";
const A1 = "0192f3a4-0000-7000-8000-00000000c101";
const pm = messages.en.platform;

const PERMISSIONS = [
  "platform.tenants.read",
  "platform.tenants.offboard",
  "platform.announcements.manage",
  "platform.breakglass.request",
  "platform.breakglass.emergency",
];

const DETAIL = {
  tenant_id: T,
  school_name: "Sri Saraswati High School",
  code: "sshs",
  tier: "shared",
  boards: ["CBSE"],
  tenant_status: "active",
  tenant_status_reason: null,
  subscription_status: "active",
  subscription: null,
  plan_code: "standard",
  deployment_status: "healthy",
  app_version: null,
  last_heartbeat_at: null,
  created_at: "2026-06-01T04:30:00Z",
  counts: null,
  open_tickets: 0,
  flag_overrides: {},
  invoices: [],
  offboard_requested_at: "2026-10-06T04:30:00Z",
  offboard_requested_by: OTHER,
  offboard_request_expires_at: "2026-10-09T04:30:00Z",
  offboard_approved_at: null,
};

function tenantSummary() {
  return {
    tenant_id: T,
    school_name: DETAIL.school_name,
    code: DETAIL.code,
    tier: "shared",
    tenant_status: "active",
    subscription_status: "active",
    plan_code: "standard",
    deployment_status: "healthy",
    app_version: null,
    last_heartbeat_at: null,
    created_at: DETAIL.created_at,
  };
}

let stub: BffStub;
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({
      operator_id: OP,
      roles: ["platform_owner"],
      permissions: PERMISSIONS,
      step_up_fresh: true,
    });
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([tenantSummary()]);
});
afterEach(() => {
  uninstallBffStub();
  vi.useRealTimers();
});

describe("offboarding request (A-13)", () => {
  it("shows when the request expires and can be withdrawn", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(DETAIL);
    stub.routes[`POST /bff/api/v1/platform/tenants/${T}/offboarding:withdraw`] = () =>
      Response.json({ ...DETAIL, offboard_requested_at: null, offboard_request_expires_at: null });
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(pm.schoolDetail.offboardPendingTitle)).toBeVisible();
    expect(screen.getByText(/it expires and must be requested again/)).toBeVisible();
    expect(screen.getByRole("button", { name: pm.schoolDetail.approveOffboard })).toBeVisible();
    await user.click(screen.getByRole("button", { name: pm.schoolDetail.withdrawOffboard }));
    const dialog = screen.getByRole("dialog", { name: pm.schoolDetail.withdrawOffboardTitle });
    await user.click(
      within(dialog).getByRole("button", { name: pm.schoolDetail.withdrawOffboard }),
    );
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/platform/tenants/${T}/offboarding:withdraw`),
      ).toHaveLength(1),
    );
  });

  it("an expired request cannot be approved, and a new one can be made", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () =>
      Response.json({
        ...DETAIL,
        offboard_requested_at: "2026-10-01T04:30:00Z",
        offboard_request_expires_at: "2026-10-04T04:30:00Z",
      });
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    expect(await screen.findByText(pm.schoolDetail.offboardExpiredTitle)).toBeVisible();
    expect(screen.queryByRole("button", { name: pm.schoolDetail.approveOffboard })).toBeNull();
    expect(screen.getByRole("button", { name: pm.schoolDetail.offboard })).toBeVisible();
    expect(screen.getByRole("button", { name: pm.schoolDetail.withdrawOffboard })).toBeVisible();
  });

  it("explains the new refusals in plain language", async () => {
    stub.routes[`GET /bff/api/v1/platform/tenants/${T}`] = () => Response.json(DETAIL);
    stub.routes[`POST /bff/api/v1/platform/tenants/${T}/offboarding:approve`] = () =>
      problem(409, "approver_not_eligible");
    const user = userEvent.setup();
    renderWithIntl(<SchoolDetailScreen schoolId={T} tab="overview" />);
    await user.click(await screen.findByRole("button", { name: pm.schoolDetail.approveOffboard }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: pm.schoolDetail.approveOffboard }));
    expect(
      await within(dialog).findByText(messages.en.errors.api.approver_not_eligible.title),
    ).toBeVisible();
  });

  it("expiry is read against the clock", () => {
    expect(offboardRequestExpired({ offboard_request_expires_at: null })).toBe(false);
    expect(offboardRequestExpired({ offboard_request_expires_at: "2026-10-07T05:59:59Z" })).toBe(
      true,
    );
    expect(offboardRequestExpired({ offboard_request_expires_at: "2026-10-07T06:00:01Z" })).toBe(
      false,
    );
  });
});

describe("break-glass requests (A-13)", () => {
  const waiting = {
    id: BG,
    tenant_id: T,
    requested_by: OTHER,
    reason_code: "security_incident",
    reason: "Suspected account compromise reported by principal",
    scope: {},
    duration_minutes: 60,
    emergency: true,
    emergency_confirmed_by_1: OTHER,
    emergency_confirmed_by_2: null,
    status: "requested",
    created_at: "2026-10-07T04:30:00Z",
    confirm_by: "2026-10-10T04:30:00Z",
  };

  it("a waiting request shows its deadline and can be withdrawn", async () => {
    stub.routes["GET /bff/api/v1/platform/break-glass-requests"] = () => page([waiting]);
    stub.routes[`POST /bff/api/v1/platform/break-glass-requests/${BG}/withdraw`] = () =>
      Response.json({ ...waiting, status: "revoked", confirm_by: null });
    const user = userEvent.setup();
    renderWithIntl(<BreakGlassScreen />);
    expect(await screen.findByText(/^Confirm by /)).toBeVisible();
    await user.click(screen.getByRole("button", { name: pm.breakGlass.withdraw }));
    const dialog = screen.getByRole("dialog", { name: pm.breakGlass.withdrawTitle });
    await user.click(within(dialog).getByRole("button", { name: pm.breakGlass.withdraw }));
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/platform/break-glass-requests/${BG}/withdraw`),
      ).toHaveLength(1),
    );
  });

  it("an emergency needs a security incident or a legal obligation", async () => {
    stub.routes["GET /bff/api/v1/platform/break-glass-requests"] = () => page([]);
    const user = userEvent.setup();
    renderWithIntl(<BreakGlassScreen />);
    await user.click(await screen.findByRole("button", { name: pm.breakGlass.request }));
    const dialog = screen.getByRole("dialog", { name: pm.breakGlass.requestTitle });
    await user.selectOptions(within(dialog).getByLabelText(pm.breakGlass.colSchool), T);
    await user.type(
      within(dialog).getByLabelText(pm.breakGlass.colReason),
      "The school asked for help with an import that failed",
    );
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: pm.breakGlass.request }));
    expect(await within(dialog).findByText(messages.en.validation.emergencyReason)).toBeVisible();
    expect(stub.callsTo("POST /bff/api/v1/platform/break-glass-requests")).toHaveLength(0);
  });
});

describe("critical announcements are two-person", () => {
  function critical(overrides: Partial<Announcement> = {}): Announcement {
    return {
      id: A1,
      title_en: "Security notice",
      title_te: "Security notice",
      body_en: "Do not share sign-in codes.",
      body_te: "Do not share sign-in codes.",
      severity: "critical",
      audience: "all",
      audience_tier: null,
      audience_tenant_ids: [],
      starts_at: "2026-10-11T00:30:00Z",
      ends_at: "2026-10-12T00:30:00Z",
      status: "pending_approval",
      version: 1,
      submitted_by: OTHER,
      submitted_at: "2026-10-07T05:00:00Z",
      approval_expires_at: "2026-10-10T05:00:00Z",
      ...overrides,
    };
  }

  it("a pending one says so and a second operator approves it", async () => {
    stub.routes["GET /bff/api/v1/platform/announcements"] = () => page([critical()]);
    stub.routes[`POST /bff/api/v1/platform/announcements/${A1}/approve`] = () =>
      Response.json(critical({ status: "scheduled", approved_by: OP }));
    const user = userEvent.setup();
    renderWithIntl(<AnnouncementsScreen />);
    expect(await screen.findByText(messages.en.status.announcement.pending_approval)).toBeVisible();
    expect(screen.getByText(/wait for a second operator's approval/)).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: new RegExp(`^${pm.announcements.approve}`) }),
    );
    const dialog = screen.getByRole("dialog", { name: pm.announcements.approveTitle });
    await user.click(within(dialog).getByRole("button", { name: pm.announcements.approve }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/platform/announcements/${A1}/approve`)).toHaveLength(1),
    );
  });

  it("a scheduled one has no Approve button", async () => {
    stub.routes["GET /bff/api/v1/platform/announcements"] = () =>
      page([critical({ status: "scheduled", approved_by: OP })]);
    renderWithIntl(<AnnouncementsScreen />);
    expect(await screen.findByText(messages.en.status.announcement.scheduled)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: new RegExp(`^${pm.announcements.approve}`) }),
    ).toBeNull();
  });
});
