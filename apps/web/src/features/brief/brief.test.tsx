import type { ApiClient } from "@schoolos/api-client";
import { describe, expect, it } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import {
  fetchBrief,
  ifMatch,
  readyShare,
  savePreferences,
  signed,
  weekParam,
  type ReadinessLine,
} from "./data";

/** FA-05 weekly management brief (FR-BRF-001..015): links, small cells and the API calls. */

const ID = "00000000-0000-7000-8000-000000000001";

const BRIEF = {
  id: ID,
  stored: true,
  week_start: "2026-11-02",
  week_end: "2026-11-08",
  as_of: "2026-11-09",
  built_at: "2026-11-09T02:15:00Z",
  previous_week_start: "2026-10-26",
  small_cell_min: 5,
  readiness: [
    {
      profile_key: "bseap_ssc",
      label: "BSEAP SSC",
      students: 160,
      ready: 142,
      needs_parent: null,
      needs_school: null,
      blocked: 0,
      ready_change: 18,
    },
  ],
  findings: { blockers: 7, warnings: 12, students_with_blockers: null },
  changes_waiting: 2,
  certificates_issued: 1,
  deadlines: [],
  deadline_days: 14,
  fees: null,
  ask: { answers: 40, included: 300 },
};

function fakeApi(calls: { method: string; path: string; init?: unknown }[], body: unknown) {
  const answer = async (method: string, path: string, init?: unknown) => {
    calls.push({ method, path, init });
    return { data: body, response: new Response(null, { status: 200 }) };
  };
  return {
    GET: (path: string, init?: unknown) => answer("GET", path, init),
    PUT: (path: string, init?: unknown) => answer("PUT", path, init),
  } as unknown as ApiClient;
}

describe("FR-BRF-010 the Monday notice", () => {
  it("opens that week's stored brief", () => {
    expect(notificationHref({ resource_type: "weekly_brief", resource_id: ID })).toBe(
      `/brief?week=${ID}`,
    );
  });

  it("only a UUID from the URL picks a week", () => {
    expect(weekParam(ID)).toBe(ID);
    expect(weekParam("../weekly")).toBeNull();
    expect(weekParam(undefined)).toBeNull();
  });
});

describe("FR-BRF-009 small cells", () => {
  it("no percentage when a count is hidden", () => {
    const line: ReadinessLine = BRIEF.readiness[0]!;
    expect(readyShare(line)).toBe(89);
    expect(readyShare({ ...line, ready: null })).toBeNull();
    expect(readyShare({ ...line, students: 0 })).toBeNull();
  });

  it("changes read with their sign", () => {
    expect(signed(18)).toBe("+18");
    expect(signed(-3)).toBe("−3");
    expect(signed(0)).toBe("0");
  });
});

describe("FR-BRF-001 API calls", () => {
  it("reads the newest week or one week by id", async () => {
    const calls: { method: string; path: string; init?: unknown }[] = [];
    const api = fakeApi(calls, BRIEF);
    expect((await fetchBrief(api, null)).readiness?.[0]?.ready_change).toBe(18);
    await fetchBrief(api, ID);
    expect(calls.map((c) => [c.path, c.init])).toEqual([
      ["/api/v1/brief/weekly", undefined],
      ["/api/v1/brief/weekly/{brief_id}", { params: { path: { brief_id: ID } } }],
    ]);
  });

  it("saves the member's own choice with If-Match", async () => {
    const calls: { method: string; path: string; init?: unknown }[] = [];
    const api = fakeApi(calls, { in_app: true, email: true, email_available: true, version: 2 });
    const saved = await savePreferences(api, { in_app: true, email: true }, 1);
    expect(saved.version).toBe(2);
    expect(calls[0]).toEqual({
      method: "PUT",
      path: "/api/v1/brief/preferences",
      init: { body: { in_app: true, email: true }, headers: { "If-Match": ifMatch(1) } },
    });
  });
});
