import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { schoolDateFormat, setSchoolDateFormat } from "@/lib/date-format";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { useStaffCan, useStaffMe, useStaffMeQuery } from "./staff-me";

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
});

afterEach(() => {
  uninstallBffStub();
  setSchoolDateFormat(null);
});

function Probe() {
  const current = useStaffMe();
  return current ? <p>loaded</p> : null;
}

describe("GET /me brings the school's display settings (FR-TEN-012)", () => {
  it("sets the school's date format for display dates", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(
        me([], {
          settings: { idle_timeout_minutes: 10, date_format: "YYYY-MM-DD", languages: ["en"] },
        }),
      );
    renderWithIntl(<Probe />);
    await screen.findByText("loaded");
    expect(schoolDateFormat()).toBe("YYYY-MM-DD");
  });

  it("keeps the default when /me has no settings", async () => {
    const { settings: _unused, ...withoutSettings } = me([]);
    void _unused;
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(withoutSettings);
    renderWithIntl(<Probe />);
    await screen.findByText("loaded");
    expect(schoolDateFormat()).toBe("DD/MM/YYYY");
  });
});

/** A screen that waits for /me, then shows a child that reads permissions (like AskTabs). */
function Gate() {
  const query = useStaffMeQuery();
  if (query.isPending) return <p>loading</p>;
  return <Child />;
}
function Child() {
  const can = useStaffCan();
  return <p>{can("kb.search") ? "allowed" : "not allowed"}</p>;
}

describe("a failed GET /me is not fetched again in a loop (NFR-PERF, WA-04)", () => {
  it("one request when /me fails and a child reads it again on mount", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json({ status: 403, code: "tenant_suspended" }, { status: 403 });
    renderWithIntl(<Gate />);
    await screen.findByText("not allowed");
    // A refetch put the query back to pending (no data yet), which unmounted the child; its
    // next mount fetched again: hundreds of /me calls a second (english-only.test timeouts).
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(stub.callsTo("GET /bff/api/v1/me")).toHaveLength(1);
    expect(screen.getByText("not allowed")).toBeInTheDocument();
  });
});
