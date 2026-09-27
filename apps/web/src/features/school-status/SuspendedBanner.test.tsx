import { act, screen } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TENANT_SUSPENDED_EVENT } from "@/lib/bff/fetch";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { renderWithIntl } from "@/test/render";
import { SuspendedBanner } from "./SuspendedBanner";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  };
});

const TENANT = "0192f3a4-0000-7000-8000-000000000001";

function me(
  roles: string[],
  status: "active" | "suspended" | "offboarding",
  permissions: string[] = [],
) {
  return {
    user_id: "0192f3a4-0000-7000-8000-0000000000a1",
    tenant_id: TENANT,
    membership_id: "0192f3a4-0000-7000-8000-0000000000b1",
    display_name: "Test Owner",
    preferred_language: "en",
    roles,
    permissions,
    scopes: [],
    mfa: true,
    tenant_ids: [TENANT],
    tenant_status: status,
  };
}

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => uninstallBffStub());

describe("suspended-school banner (FR-PLT-004, BR-08)", () => {
  it("shows nothing for an active school", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["office_admin"], "active"));
    renderWithIntl(<SuspendedBanner />);
    await vi.waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me")).toHaveLength(1));
    expect(screen.queryByText(/paused/)).not.toBeInTheDocument();
  });

  it("tells the owner what still works, with a link to Plan and billing", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(me(["owner"], "suspended", ["tenant.billing.read"]));
    renderWithIntl(<SuspendedBanner />);
    expect(await screen.findByText("This school's SchoolOS access is paused")).toBeInTheDocument();
    expect(
      screen.getByText(/Only the owner and the principal can still sign in/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Plan and billing" })).toHaveAttribute(
      "href",
      "/en/settings/billing",
    );
  });

  it("tells other staff to ask the owner when /me answers tenant_suspended", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => problem(403, "tenant_suspended");
    renderWithIntl(<SuspendedBanner />);
    expect(await screen.findByText(/You can't use SchoolOS for this school/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("appears when any screen gets 403 tenant_suspended (client event)", async () => {
    let suspended = false;
    stub.routes["GET /bff/api/v1/me"] = () =>
      suspended ? problem(403, "tenant_suspended") : Response.json(me(["teacher"], "active"));
    renderWithIntl(<SuspendedBanner />);
    await vi.waitFor(() => expect(stub.callsTo("GET /bff/api/v1/me")).toHaveLength(1));
    suspended = true;
    act(() => {
      window.dispatchEvent(new CustomEvent(TENANT_SUSPENDED_EVENT));
    });
    expect(await screen.findByText("This school's SchoolOS access is paused")).toBeInTheDocument();
  });

  it("says the school is leaving while it is offboarding", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["principal"], "offboarding"));
    renderWithIntl(<SuspendedBanner />);
    expect(await screen.findByText("This school is leaving SchoolOS")).toBeInTheDocument();
  });

  it("uses the server's status before /me has loaded", () => {
    stub.routes["GET /bff/api/v1/me"] = () => new Promise<Response>(() => {});
    renderWithIntl(<SuspendedBanner initialStatus="suspended" />);
    expect(screen.getByText("This school's SchoolOS access is paused")).toBeInTheDocument();
  });
});
