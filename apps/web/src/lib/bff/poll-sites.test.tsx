import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setAdminPollDelayForTesting, useTenantExports } from "@/features/admin/data";
import { useConversation } from "@/features/ask/conversations";
import { BatchScreen } from "@/features/extraction/BatchScreen";
import { RunProgress } from "@/features/findings/RunChecks";
import { ImportDetailScreen } from "@/features/imports/ImportDetail";
import { POLL_MS, usePolledQuery } from "@/features/imports/poll";
import { NotificationBell } from "@/features/notifications/NotificationBell";
import { IncidentsScreen } from "@/features/platform/incidents/IncidentsScreen";
import { DashboardScreen } from "@/features/platform/screens";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import {
  advance,
  expectPassivePolls,
  expectPollStops,
  expectPollsThroughTransient,
  failSecondRead,
} from "@/test/poll";
import { ALL_RECORD_PERMISSIONS, ID, extractionBatch, importBatch } from "@/test/records-fixtures";
import { renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { PASSIVE_HEADER } from "./fetch";
import { pollHeaders } from "./poll";
import { unwrap, useApiQuery, useBffClient } from "./query";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    usePathname: () => "/",
    useSearchParams: () => new URLSearchParams(),
  };
});

/**
 * XB-07 (FR-IAM-003, SEC-006) and XO-08 (FR-DOC-004): the two rules XB-06 and XO-07 applied
 * to the exports, documents, circulars and admin pollers hold for every other automatic poll
 * too. A poll after the first load is passive (it does not slide the idle timeout), and a poll
 * keeps asking after a transient failure (5xx, 408, 429, offline) but stops on any other.
 */

const ID1 = "0192f3a4-0000-7000-8000-0000000bb001";
let stub: BffStub;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => {
  uninstallBffStub();
  vi.useRealTimers();
});

const TRANSIENT_AND_NOT: [string, number, boolean][] = [
  ["keeps polling after a transient failure (429)", 429, false],
  ["stops polling after a failure asking again does not fix (403)", 403, true],
];

describe("school data export poll (XO-08, FR-ADM-001)", () => {
  const route = "GET /bff/api/v1/admin/tenant-export";
  function Probe() {
    useTenantExports(undefined, true);
    return null;
  }
  beforeEach(() => setAdminPollDelayForTesting(() => 1000));
  afterEach(() => setAdminPollDelayForTesting(null));
  it.each(TRANSIENT_AND_NOT)("%s", async (_name, status, stops) => {
    stub = installBffStub("staff");
    failSecondRead(stub, route, () => page([{ status: "running" }]), status, stops);
    renderWithIntl(<Probe />);
    if (stops) await expectPollStops(stub, route, 1000);
    else await expectPollsThroughTransient(stub, route, 1000);
  });
});

describe("useApiQuery polls (XB-07, XO-08)", () => {
  const route = `GET /bff/api/v1/certificates/${ID1}`;
  function Probe() {
    const api = useBffClient("staff");
    useApiQuery(
      ["probe", ID1],
      (context) =>
        unwrap(
          api.GET("/api/v1/certificates/{certificate_id}", {
            headers: pollHeaders(context),
            params: { path: { certificate_id: ID1 } },
          }),
        ),
      { refetchInterval: () => 1000 },
    );
    return null;
  }
  it.each(TRANSIENT_AND_NOT)("%s", async (_name, status, stops) => {
    stub = installBffStub("staff");
    failSecondRead(stub, route, () => Response.json({ pdf_status: "queued" }), status, stops);
    renderWithIntl(<Probe />);
    if (stops) await expectPollStops(stub, route, 1000);
    else await expectPollsThroughTransient(stub, route, 1000);
  });
});

describe("usePolledQuery (imports, register photos; XO-08)", () => {
  const route = `GET /bff/api/v1/imports/${ID.import}`;
  function Probe() {
    const api = useBffClient("staff");
    usePolledQuery(
      ["probe-import", ID.import],
      (context) =>
        unwrap(
          api.GET("/api/v1/imports/{import_id}", {
            headers: pollHeaders(context),
            params: { path: { import_id: ID.import } },
          }),
        ),
      () => POLL_MS,
    );
    return null;
  }
  it.each(TRANSIENT_AND_NOT)("%s", async (_name, status, stops) => {
    stub = installBffStub("staff");
    failSecondRead(
      stub,
      route,
      () => Response.json(importBatch({ status: "validating" })),
      status,
      stops,
    );
    renderWithIntl(<Probe />);
    if (stops) await expectPollStops(stub, route, POLL_MS);
    else await expectPollsThroughTransient(stub, route, POLL_MS);
  });
});

describe("import status poll (XB-07, FR-IMP-002)", () => {
  it("is passive after the first load and keeps asking after a transient failure", async () => {
    stub = installBffStub("staff");
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([...ALL_RECORD_PERMISSIONS]));
    stub.routes["GET /bff/api/v1/attributes"] = () => Response.json([]);
    stub.routes[`GET /bff/api/v1/imports/${ID.import}/rows`] = () => page([]);
    const route = `GET /bff/api/v1/imports/${ID.import}`;
    failSecondRead(stub, route, () => Response.json(importBatch({ status: "validating" })), 503);
    renderWithIntl(<ImportDetailScreen importId={ID.import} />);
    await expectPollsThroughTransient(stub, route, POLL_MS);
  });
});

describe("register photos batch poll (XB-07, FR-EXT-001)", () => {
  it("is passive after the first load and keeps asking after a transient failure", async () => {
    stub = installBffStub("staff");
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me([...ALL_RECORD_PERMISSIONS]));
    const route = `GET /bff/api/v1/extraction-batches/${ID.batch}`;
    failSecondRead(
      stub,
      route,
      () => Response.json(extractionBatch({ status: "processing", pages: [] })),
      429,
    );
    renderWithIntl(<BatchScreen batchId={ID.batch} />);
    await expectPollsThroughTransient(stub, route, POLL_MS);
  });
});

describe("data check run poll (XB-07, XO-08, FR-DQ-001)", () => {
  const route = `GET /bff/api/v1/dq/runs/${ID1}`;
  const running = () =>
    Response.json({
      id: ID1,
      trigger: "manual",
      event_type: null,
      status: "running",
      profile_key: null,
      scope: {},
      stats: {},
      error: null,
      started_at: null,
      finished_at: null,
      created_at: "2026-11-08T05:00:00Z",
    });
  it.each(TRANSIENT_AND_NOT)("%s", async (_name, status, stops) => {
    stub = installBffStub("staff");
    failSecondRead(stub, route, running, status, stops);
    renderWithIntl(<RunProgress runId={ID1} />);
    if (stops) await expectPollStops(stub, route, 15_000);
    else await expectPollsThroughTransient(stub, route, 15_000);
  });
});

describe("conversation poll while an answer is written elsewhere (XB-07, XO-08, FR-AI-001)", () => {
  const route = `GET /bff/api/v1/knowledge/conversations/${ID1}`;
  const streaming = () =>
    Response.json({
      id: ID1,
      title: "Fees",
      messages: [
        {
          id: "0192f3a4-0000-7000-8000-0000000bb101",
          status: "streaming",
          superseded: false,
          created_at: new Date().toISOString(),
        },
      ],
    });
  function Probe() {
    useConversation(ID1);
    return null;
  }
  it.each(TRANSIENT_AND_NOT)("%s", async (_name, status, stops) => {
    stub = installBffStub("staff");
    failSecondRead(stub, route, streaming, status, stops);
    renderWithIntl(<Probe />);
    if (stops) await expectPollStops(stub, route, 10_000);
    else await expectPollsThroughTransient(stub, route, 10_000);
  });
});

describe("notification bell (XB-07, XO-08, FR-NOT-001)", () => {
  const route = "GET /bff/api/v1/notifications/unread-count";
  it("every automatic read is passive, and it keeps asking after a transient failure", async () => {
    stub = installBffStub("staff");
    failSecondRead(stub, route, () => Response.json({ count: 2 }), 503);
    renderWithIntl(<NotificationBell />);
    // One minute, then two and four after the failures in a row, then back to one.
    await advance(60_000, 12);
    const calls = stub.callsTo(route);
    expect(calls.length).toBeGreaterThanOrEqual(5);
    for (const call of calls) expect(call.headers.get(PASSIVE_HEADER)).toBe("1");
  });
  it("stops asking after a failure asking again does not fix (404)", async () => {
    stub = installBffStub("staff");
    failSecondRead(stub, route, () => Response.json({ count: 2 }), 404, true);
    renderWithIntl(<NotificationBell />);
    await expectPollStops(stub, route, 15 * 60_000);
  });
});

describe("platform incident clocks (XB-07, XO-08, FR-DPDP-031)", () => {
  const route = "GET /bff/api/v1/platform/incidents/clocks";
  function operator() {
    stub = installBffStub("operator");
    stub.routes["GET /bff/api/v1/platform/me"] = () =>
      Response.json({
        operator_id: "0192f3a4-0000-7000-8000-0000000bb201",
        roles: ["platform_owner"],
        permissions: ["platform.incidents.read"],
        step_up_fresh: true,
      });
    stub.routes["GET /bff/api/v1/platform/incidents"] = () => page([]);
    stub.routes["GET /bff/api/v1/platform/tenants"] = () => page([]);
    stub.routes["GET /bff/api/v1/platform/dashboard"] = () =>
      Response.json({ incident_clocks: [] });
  }
  it.each(TRANSIENT_AND_NOT)("incidents screen %s", async (_name, status, stops) => {
    operator();
    failSecondRead(stub, route, () => Response.json([]), status, stops);
    renderWithIntl(<IncidentsScreen />);
    if (stops) await expectPollStops(stub, route, 60_000);
    else await expectPollsThroughTransient(stub, route, 60_000);
  });
  it("dashboard clocks are passive after the first load", async () => {
    operator();
    stub.routes[route] = () => Response.json([]);
    renderWithIntl(<DashboardScreen />);
    await advance(60_000, 3);
    expectPassivePolls(stub.callsTo(route));
  });
});
