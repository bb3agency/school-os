import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { grant, me, structureRoutes } from "@/test/school-fixtures";
import { BreakGlassDetailScreen, BreakGlassScreen } from "./BreakGlassScreens";
import { grantStatusFilter } from "./filters";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/break-glass",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  };
});

const ID = "0192f3a4-0000-7000-8000-00000000b6a1";
const OWNER = ["breakglass.approve", "audit.read", "student.read_basic"];
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("support access list (US-103, FR-OPS-004, SEC-021)", () => {
  it("shows who asked, why, for how long, and what waits for a decision", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(OWNER, { roles: ["owner"] }));
    stub.routes["GET /bff/api/v1/breakglass/requests"] = () =>
      page([
        grant(),
        grant({
          id: "0192f3a4-0000-7000-8000-00000000b6a2",
          status: "active",
          expires_at: "2026-09-26T09:00:00Z",
        }),
      ]);
    renderWithIntl(<BreakGlassScreen status={null} />);
    expect(await screen.findByText("1 request is waiting for your decision.")).toBeInTheDocument();
    expect(screen.getAllByText("Support Person")).toHaveLength(2);
    expect(screen.getAllByText("2 hours")).toHaveLength(2);
    expect(within(screen.getByRole("table")).getByText("Open now")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Decide/ })).toHaveAttribute(
      "href",
      `/break-glass/${ID}`,
    );
    expect(
      screen.getByRole("link", { name: "See what support looked at (audit log)" }),
    ).toHaveAttribute("href", "/audit?action=breakglass.access");
  });

  it("is only for the owner and principal", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    renderWithIntl(<BreakGlassScreen status={null} />);
    expect(
      await screen.findByText("Only the owner and the principal can see this"),
    ).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/breakglass/requests")).toHaveLength(0);
  });

  it("reads a valid status filter from the URL only", () => {
    expect(grantStatusFilter("active")).toBe("active");
    expect(grantStatusFilter("drop table")).toBeNull();
    expect(grantStatusFilter(undefined)).toBeNull();
  });
});

describe("support access request (US-103 AC1/AC2)", () => {
  function detail(overrides = {}) {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(OWNER, { roles: ["owner"] }));
    stub.routes[`GET /bff/api/v1/breakglass/requests/${ID}`] = () =>
      Response.json(grant(overrides));
  }

  it("shows reason, scope in plain words and duration, and approves with step-up", async () => {
    detail();
    stub.routes[`POST /bff/api/v1/breakglass/requests/${ID}/approve`] = () =>
      Response.json(grant({ status: "active" }));
    renderWithIntl(<BreakGlassDetailScreen grantId={ID} />);
    expect(
      await screen.findByText("The school asked for help with an import that failed."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Class 9 · A")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog", { name: "Give SchoolOS support access" });
    expect(within(dialog).getByText(/ending by itself after 2 hours/)).toBeInTheDocument();
    expect(within(dialog).getByText(/you may be asked to sign in again/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/breakglass/requests/${ID}/approve`)).toHaveLength(1),
    );
  });

  it("states the real reach: keys that do not narrow access read as the whole school (DL-10)", async () => {
    detail({ scope: { student_id: "0192f3a4-0000-7000-8000-00000000aaaa", access: "read" } });
    renderWithIntl(<BreakGlassDetailScreen grantId={ID} />);
    const reach = await screen.findByTestId("breakglass-reach");
    expect(reach).toHaveTextContent("The whole school");
    expect(reach).not.toHaveTextContent(/^Student:/);
    expect(screen.getByTestId("breakglass-scope-ignored")).toHaveTextContent(
      /do not limit what they can see/,
    );
  });

  it("an empty scope says the whole school, with no extra note (DL-10)", async () => {
    detail({ scope: {} });
    renderWithIntl(<BreakGlassDetailScreen grantId={ID} />);
    expect(await screen.findByTestId("breakglass-reach")).toHaveTextContent("The whole school");
    expect(screen.queryByTestId("breakglass-scope-ignored")).toBeNull();
  });

  it("explains when support withdrew the request", async () => {
    detail();
    stub.routes[`POST /bff/api/v1/breakglass/requests/${ID}/deny`] = () =>
      problem(409, "request_withdrawn");
    renderWithIntl(<BreakGlassDetailScreen grantId={ID} />);
    await userEvent.click(await screen.findByRole("button", { name: "Deny" }));
    const dialog = screen.getByRole("dialog", { name: "Deny this request" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Deny" }));
    expect(
      await within(dialog).findByText("SchoolOS support withdrew this request"),
    ).toBeInTheDocument();
  });

  it("open access can be ended now; decided requests offer no approve or deny", async () => {
    detail({
      status: "active",
      expires_at: "2026-09-26T09:00:00Z",
      starts_at: "2026-09-26T07:00:00Z",
    });
    stub.routes[`POST /bff/api/v1/breakglass/grants/${ID}/revoke`] = () =>
      Response.json(grant({ status: "revoked" }));
    renderWithIntl(<BreakGlassDetailScreen grantId={ID} />);
    expect(await screen.findByText("Support can see your records now")).toBeInTheDocument();
    // Lifecycle as a timeline: asked, started (now), ends (still to come).
    const timeline = screen.getByRole("list", { name: "Timeline" });
    const steps = within(timeline).getAllByRole("listitem");
    expect(steps.map((step) => step.querySelector("p")?.textContent)).toEqual([
      "Done: Asked on",
      "Now: Access started",
      "Still to come: Access ends",
    ]);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "End access now" }));
    const dialog = screen.getByRole("dialog", { name: "End support access now" });
    await userEvent.click(within(dialog).getByRole("button", { name: "End access now" }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/breakglass/grants/${ID}/revoke`)).toHaveLength(1),
    );
  });

  it("flags emergency access and does not offer an approval for it", async () => {
    detail({ emergency: true, status: "active", reason_code: "security_incident" });
    renderWithIntl(<BreakGlassDetailScreen grantId={ID} />);
    expect(await screen.findByText("Emergency access")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByRole("button", { name: "End access now" })).toBeInTheDocument();
  });
});
