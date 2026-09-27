import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { BreakGlassScreen, supportSignInUrl } from "./OperationsViews";

/**
 * Admin panel → school app support sign-in (ADR-0023 option C, docs/16 §5.15, SEC-021).
 * Synthetic IDs only.
 */

const T = "0192f3a4-0000-7000-8000-000000000001";
const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const pm = messages.en.platform;
let stub: BffStub;

const request = (id: string, status: string) => ({
  id,
  tenant_id: T,
  requested_by: OP,
  reason_code: "support_request",
  reason: "Import batch shows duplicate rows; need to read the batch",
  scope: {},
  duration_minutes: 60,
  emergency: false,
  emergency_confirmed_by_1: null,
  emergency_confirmed_by_2: null,
  status,
  created_at: "2026-09-27T04:30:00Z",
});

beforeEach(() => {
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({
      operator_id: OP,
      roles: ["support_agent"],
      permissions: ["platform.tenants.read", "platform.breakglass.request"],
      step_up_fresh: true,
    });
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([]);
});
afterEach(uninstallBffStub);

describe("break-glass: open the school with the support client", () => {
  it("offers the support sign-in link only for active requests, keyboard reachable", async () => {
    const active = "0192f3a4-0000-7000-8000-00000000f201";
    stub.routes["GET /bff/api/v1/platform/break-glass-requests"] = () =>
      page([
        request(active, "active"),
        request("0192f3a4-0000-7000-8000-00000000f202", "requested"),
      ]);
    renderWithIntl(<BreakGlassScreen />);
    const links = await screen.findAllByRole("link", { name: pm.breakGlass.openSchool });
    expect(links).toHaveLength(1);
    const [link] = links;
    expect(link).toHaveAttribute("href", `/bff/auth/support/login?request=${active}&tenant=${T}`);
    expect(link).toHaveAccessibleDescription(pm.breakGlass.openSchoolHint);
    link?.focus();
    expect(link).toHaveFocus();
  });

  it("builds the link from IDs only", () => {
    const url = supportSignInUrl({ id: "0192f3a4-0000-7000-8000-00000000f203", tenant_id: T });
    expect(url).toBe(
      `/bff/auth/support/login?request=0192f3a4-0000-7000-8000-00000000f203&tenant=${T}`,
    );
  });
});
