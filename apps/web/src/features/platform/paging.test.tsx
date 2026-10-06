import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { AnnouncementsScreen } from "./AnnouncementsView";
import { BreakGlassScreen, FleetScreen } from "./OperationsViews";

/**
 * Cursor paging of the operator lists of deployments, announcements and break-glass requests
 * (audit 2026-10-06 R-14): the first page asks for `limit`, "Show more" follows `next_cursor`
 * and appends rows, and disappears on the last page. Synthetic data only.
 */

const OP = "0192f3a4-0000-7000-8000-0000000000f1";
const T = "0192f3a4-0000-7000-8000-000000000001";
const pm = messages.en.platform;
let stub: BffStub;

/** First call: `first` with a cursor; with `cursor=c1`: `second` and no cursor. */
function paged(key: string, first: unknown[], second: unknown[]) {
  stub.routes[key] = (_request, url) =>
    url.searchParams.get("cursor") === "c1"
      ? page(second)
      : Response.json({ data: first, next_cursor: "c1" });
}

async function showMore(key: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: pm.showMore }));
  await waitFor(() => expect(stub.callsTo(key)).toHaveLength(2));
  const [one, two] = stub.callsTo(key);
  expect(one?.url.searchParams.get("limit")).toBe("50");
  expect(one?.url.searchParams.has("cursor")).toBe(false);
  expect(two?.url.searchParams.get("cursor")).toBe("c1");
  await waitFor(() => expect(screen.queryByRole("button", { name: pm.showMore })).toBeNull());
}

const deployment = (id: string, name: string) => ({
  id,
  tenant_id: T,
  tenant_code: "sshs",
  school_name: name,
  tenant_status: "active",
  mode: "dedicated",
  region: "ap-south-1",
  backup_region: "ap-south-2",
  host_ref: null,
  hostname: null,
  custom_domain: null,
  app_version: "2026.09.1",
  target_version: null,
  last_heartbeat_at: null,
  status: "healthy",
  heartbeat_key_id: "hk_1",
  heartbeat_next_key_id: null,
  version: 1,
});

const request = (id: string, reason: string) => ({
  id,
  tenant_id: T,
  requested_by: OP,
  reason_code: "support_request",
  reason,
  scope: {},
  duration_minutes: 60,
  emergency: false,
  emergency_confirmed_by_1: null,
  emergency_confirmed_by_2: null,
  status: "requested",
  created_at: "2026-09-27T04:30:00Z",
});

const announcement = (id: string, title: string) => ({
  id,
  title_en: title,
  title_te: "",
  body_en: "SchoolOS is unavailable 06:00-07:00.",
  body_te: "",
  severity: "maintenance",
  audience: "all",
  audience_tier: null,
  audience_tenant_ids: [],
  starts_at: "2026-10-11T00:30:00Z",
  ends_at: "2026-10-11T01:30:00Z",
  status: "scheduled",
  version: 1,
});

beforeEach(() => {
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/me"] = () =>
    Response.json({
      operator_id: OP,
      roles: ["platform_viewer"],
      permissions: ["platform.tenants.read", "platform.fleet.read"],
      step_up_fresh: true,
    });
  stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([]);
  stub.routes["GET /bff/api/v1/platform/fleet/versions"] = () => Response.json([]);
});
afterEach(uninstallBffStub);

describe("operator lists load more pages (R-14)", () => {
  it("fleet: deployments", async () => {
    const key = "GET /bff/api/v1/platform/deployments";
    paged(
      key,
      [deployment("0192f3a4-0000-7000-8000-00000000d002", "Second Synthetic School")],
      [deployment("0192f3a4-0000-7000-8000-00000000d001", "First Synthetic School")],
    );
    renderWithIntl(<FleetScreen />);
    expect(await screen.findByText("Second Synthetic School")).toBeVisible();
    expect(screen.queryByText("First Synthetic School")).toBeNull();
    await showMore(key);
    expect(screen.getByText("First Synthetic School")).toBeVisible();
    expect(screen.getByText("Second Synthetic School")).toBeVisible();
  });

  it("break-glass requests", async () => {
    const key = "GET /bff/api/v1/platform/break-glass-requests";
    paged(
      key,
      [request("0192f3a4-0000-7000-8000-00000000f302", "Newest synthetic request reason")],
      [request("0192f3a4-0000-7000-8000-00000000f301", "Oldest synthetic request reason")],
    );
    renderWithIntl(<BreakGlassScreen />);
    expect(await screen.findByText("Newest synthetic request reason")).toBeVisible();
    await showMore(key);
    expect(screen.getByText("Oldest synthetic request reason")).toBeVisible();
  });

  it("announcements", async () => {
    const key = "GET /bff/api/v1/platform/announcements";
    paged(
      key,
      [announcement("0192f3a4-0000-7000-8000-00000000c202", "Newer synthetic banner")],
      [announcement("0192f3a4-0000-7000-8000-00000000c201", "Older synthetic banner")],
    );
    renderWithIntl(<AnnouncementsScreen />);
    expect(await screen.findByText("Newer synthetic banner")).toBeVisible();
    await showMore(key);
    expect(screen.getByText("Older synthetic banner")).toBeVisible();
  });

  it("a single page shows no Show more button", async () => {
    stub.routes["GET /bff/api/v1/platform/deployments"] = () =>
      page([deployment("0192f3a4-0000-7000-8000-00000000d003", "Only Synthetic School")]);
    renderWithIntl(<FleetScreen />);
    expect(await screen.findByText("Only Synthetic School")).toBeVisible();
    expect(screen.queryByRole("button", { name: pm.showMore })).toBeNull();
  });
});
