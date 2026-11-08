import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { setAdminPollDelayForTesting, useTenantExports } from "@/features/admin/data";
import {
  setCircularsPollDelayForTesting,
  useCircular,
  useCirculars,
  useNotice,
  useNotices,
} from "@/features/circulars/data";
import {
  setDocumentPollDelayForTesting,
  useDocument,
  useDocumentList,
} from "@/features/documents/data";
import { setExportPollDelayForTesting, useExport, useExportList } from "@/features/exports/data";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { renderWithIntl, testQueryClient } from "@/test/render";
import { PASSIVE_HEADER } from "./fetch";
import { pollHeaders } from "./poll";

/**
 * XB-06 (FR-IAM-003, SEC-006): status polls do not slide the idle timeout. The BFF side (a GET
 * with `x-sos-passive: 1` reads the session without touching it) is tested in
 * src/server/bff/proxy.test.ts; here: the first load is active, every poll after it passive.
 */

const ID = "0192f3a4-0000-7000-8000-0000000aa001";
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  for (const set of [
    setExportPollDelayForTesting,
    setDocumentPollDelayForTesting,
    setCircularsPollDelayForTesting,
    setAdminPollDelayForTesting,
  ]) {
    set(() => 5);
  }
});

afterEach(() => {
  for (const set of [
    setExportPollDelayForTesting,
    setDocumentPollDelayForTesting,
    setCircularsPollDelayForTesting,
    setAdminPollDelayForTesting,
  ]) {
    set(null);
  }
  uninstallBffStub();
});

describe("pollHeaders (XB-06, FR-IAM-003)", () => {
  it("the first load counts as activity, a fetch of data already shown is passive", () => {
    const client = testQueryClient();
    const queryKey = ["x"];
    expect(pollHeaders({ client, queryKey })).toEqual({});
    client.setQueryData(queryKey, { ok: true });
    expect(pollHeaders({ client, queryKey })).toEqual({ [PASSIVE_HEADER]: "1" });
  });
});

const busyVersion = { current_version: { status: "scanning" }, versions: [{ status: "scanning" }] };
const page = (row: object) => ({ data: [row], next_cursor: null });

const cases: [string, string, object, () => void][] = [
  ["export", `GET /bff/api/v1/exports/${ID}`, { status: "queued" }, () => useExport(ID)],
  [
    "export list",
    "GET /bff/api/v1/exports",
    page({ status: "running" }),
    () => useExportList("me", undefined, true),
  ],
  ["document", `GET /bff/api/v1/documents/${ID}`, busyVersion, () => useDocument(ID, true)],
  [
    "document list",
    "GET /bff/api/v1/documents",
    page(busyVersion),
    () => useDocumentList({ purpose: null, docType: null, status: null } as never, undefined, true),
  ],
  [
    "circular",
    `GET /bff/api/v1/circulars/${ID}`,
    { reading_status: "queued" },
    () => useCircular(ID, true),
  ],
  [
    "circular list",
    "GET /bff/api/v1/circulars",
    page({ reading_status: "running" }),
    () => useCirculars(undefined, true),
  ],
  ["notice", `GET /bff/api/v1/notices/${ID}`, { status: "drafting" }, () => useNotice(ID, true)],
  ["notice list", "GET /bff/api/v1/notices", page({ status: "drafting" }), () => useNotices(true)],
  [
    "school data export",
    "GET /bff/api/v1/admin/tenant-export",
    page({ status: "queued" }),
    () => useTenantExports(undefined, true),
  ],
];

describe.each(cases)("%s status poll (XB-06)", (_name, route, busy, useHook) => {
  it("is passive after the first load, so it does not slide the idle timeout", async () => {
    stub.routes[route] = () => Response.json(busy);
    function Probe() {
      useHook();
      return null;
    }
    renderWithIntl(<Probe />);
    await waitFor(() => expect(stub.callsTo(route).length).toBeGreaterThanOrEqual(3));
    const [first, ...polls] = stub.callsTo(route);
    expect(first?.headers.get(PASSIVE_HEADER)).toBeNull();
    for (const poll of polls) expect(poll.headers.get(PASSIVE_HEADER)).toBe("1");
  });
});
