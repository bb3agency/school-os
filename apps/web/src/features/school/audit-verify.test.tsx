import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SchoolAuditVerifyPage from "@/app/[locale]/(school)/audit/verify/page";
import type { Locale } from "@/i18n/routing";
import { setSheetSaverForTesting } from "@/features/sheets/download";
import { registerStepUpHandler } from "@/lib/bff/step-up";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { AuditVerifyScreen } from "./AuditVerifyScreen";
import { AuditView } from "./AuditView";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/audit/verify",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  };
});

const VERIFY = "GET /bff/api/v1/audit/verify";
let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["audit.read"]));
});

afterEach(() => {
  setSheetSaverForTesting(undefined);
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

const QUEUE = "POST /bff/api/v1/audit/verify";

/** A stored check (GET /audit/verify; audit 2026-10-06 R-19). */
function stored(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    checked: 1234,
    first_bad_seq: null,
    reason: null,
    verified_at: "2026-10-05T20:45:00Z",
    mode: "full",
    source: "daily",
    checkpoint_seq: 1234,
    checkpoint_at: "2026-10-05T20:45:00Z",
    last_full_at: "2026-10-05T20:45:00Z",
    pending: false,
    requested_at: null,
    ...overrides,
  };
}

describe("audit chain check (US-1001 AC2, FR-AUD-003, FR-AUD-005)", () => {
  it("shows the stored result and when it was verified, without re-checking", async () => {
    stub.routes[VERIFY] = () => Response.json(stored());
    renderWithIntl(<AuditVerifyScreen />);
    expect(await screen.findByText("The audit log is intact")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Verified up to event 1,234: 1,234 events were checked and none was changed.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/^Last verified on /)).toBeInTheDocument();
    expect(stub.callsTo(VERIFY)).toHaveLength(1);
    expect(stub.callsTo(QUEUE)).toHaveLength(0);
  });

  it("says how many new events an incremental check covered", async () => {
    stub.routes[VERIFY] = () =>
      Response.json(stored({ mode: "incremental", source: "on_demand", checked: 3 }));
    renderWithIntl(<AuditVerifyScreen />);
    expect(
      await screen.findByText(
        "Verified up to event 1,234: 3 new events were checked and none was changed.",
      ),
    ).toBeInTheDocument();
  });

  it("check again queues a check, shows it queued and the result when it is done", async () => {
    let done = false;
    stub.routes[VERIFY] = () =>
      Response.json(done ? stored({ checkpoint_seq: 1240, checked: 6 }) : stored());
    stub.routes[QUEUE] = () =>
      Response.json(stored({ pending: true, requested_at: "2026-10-06T05:00:00Z" }), {
        status: 202,
      });
    const user = userEvent.setup();
    renderWithIntl(<AuditVerifyScreen />);
    await user.click(await screen.findByRole("button", { name: "Check again" }));
    await waitFor(() => expect(stub.callsTo(QUEUE)).toHaveLength(1));
    expect(JSON.parse(stub.callsTo(QUEUE)[0]?.body ?? "null")).toEqual({ full: false });
    expect(await screen.findByText("Check queued")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeDisabled();
    done = true;
  });

  it("explains the 10-minute cool-down when the school asked a moment ago (429)", async () => {
    stub.routes[VERIFY] = () => Response.json(stored());
    stub.routes[QUEUE] = () => problem(429, "rate_limited", { retry_after: 420 });
    const user = userEvent.setup();
    renderWithIntl(<AuditVerifyScreen />);
    await user.click(await screen.findByRole("button", { name: "Check again" }));
    expect(await screen.findByText("The log was checked a moment ago")).toBeInTheDocument();
    expect(
      screen.getByText(
        "You can ask for another check in 7 minutes. SchoolOS also checks the whole log every night.",
      ),
    ).toBeInTheDocument();
  });

  it("before the first check: says so and offers to check now", async () => {
    stub.routes[VERIFY] = () =>
      Response.json(
        stored({
          ok: null,
          checked: 0,
          verified_at: null,
          mode: null,
          source: null,
          checkpoint_seq: 0,
          checkpoint_at: null,
          last_full_at: null,
        }),
      );
    renderWithIntl(<AuditVerifyScreen />);
    expect(await screen.findByText("Not checked yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check integrity" })).toBeEnabled();
  });

  it("says where the chain is broken, why, and what to do", async () => {
    stub.routes[VERIFY] = () =>
      Response.json(stored({ ok: false, checked: 6, first_bad_seq: 7, reason: "hash_mismatch" }));
    renderWithIntl(<AuditVerifyScreen />);
    expect(await screen.findByText("The audit log is broken at event 7")).toBeInTheDocument();
    expect(
      screen.getByText("Events before it are intact (6 events, up to event 6)."),
    ).toBeInTheDocument();
    expect(screen.getByText("This event was changed after it was recorded.")).toBeInTheDocument();
    expect(screen.getByText(/Contact SchoolOS support now/)).toBeInTheDocument();
  });

  it("explains an unknown reason and a break at the first event plainly", async () => {
    stub.routes[VERIFY] = () =>
      Response.json(stored({ ok: false, checked: 0, first_bad_seq: 1, reason: "new_reason" }));
    renderWithIntl(<AuditVerifyScreen />, "te");
    expect(
      await screen.findByText(messages.te.school.audit.integrity.reason.other),
    ).toBeInTheDocument();
    expect(
      screen.getByText(messages.te.school.audit.integrity.brokenFromStart),
    ).toBeInTheDocument();
  });

  it("shows a plain error when the result cannot be read", async () => {
    stub.routes[VERIFY] = () => problem(503, "service_unavailable");
    renderWithIntl(<AuditVerifyScreen />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("The audit log is intact")).toBeNull();
  });

  it("is refused without audit.read and asks the API nothing", async () => {
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(["student.read_basic"]));
    renderWithIntl(<AuditVerifyScreen />);
    expect(await screen.findByText("You can't check the audit log")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check integrity" })).toBeNull();
    expect(stub.callsTo(VERIFY)).toHaveLength(0);
  });

  it("is linked from the audit log", () => {
    renderWithIntl(<AuditView events={{ status: "ready", data: [] }} />);
    expect(screen.getByRole("link", { name: "Check integrity" })).toHaveAttribute(
      "href",
      "/audit/verify",
    );
  });

  it("the audit log's filter bar is a GET search form that shows the URL's filters again (FR-AUD-005)", () => {
    renderWithIntl(
      <AuditView
        events={{ status: "ready", data: [] }}
        filters={{ action: "student.update", from: "01/06/2026" }}
      />,
    );
    const form = screen.getByRole("search", { name: "Filters" });
    expect(form).toHaveAttribute("method", "get");
    expect(screen.getByRole("searchbox", { name: "Action" })).toHaveValue("student.update");
    expect(screen.getByLabelText("From date")).toHaveValue("01/06/2026");
    expect(screen.getByRole("link", { name: "Clear filters" })).toHaveAttribute("href", "/audit");
    // FR-AUD-005: the CSV download is available next to the filters.
    expect(screen.getByRole("button", { name: "Download CSV" })).toBeEnabled();
  });

  for (const locale of ["en", "te"] as Locale[]) {
    it(`renders the page [${locale}]`, async () => {
      renderWithIntl(SchoolAuditVerifyPage(), locale);
      expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
        messages[locale].school.audit.integrity.title,
      );
    });
  }
});

describe("audit log CSV download (FR-AUD-005, US-1001, SEC-005)", () => {
  const EXPORT = "GET /bff/api/v1/audit/export";
  const USER = "0192f3a4-0000-7000-8000-0000000000d1";
  const csv = () =>
    new Response("\uFEFFseq,occurred_at_utc\r\n7,2026-09-26T04:30:00Z\r\n", {
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": 'attachment; filename="audit-log-2026-09-29.csv"',
      },
    });

  it("downloads the events the current filters select, through the BFF", async () => {
    stub.routes[EXPORT] = csv;
    const saved = vi.fn();
    setSheetSaverForTesting(saved);
    const user = userEvent.setup();
    renderWithIntl(
      <AuditView
        events={{ status: "ready", data: [] }}
        filters={{ actor: ` ${USER} `, action: "student.update", from: "01/06/2026", to: "x" }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Download CSV" }));
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
    expect(saved.mock.calls[0]?.[1]).toBe("audit-log-2026-09-29.csv");
    expect(await screen.findByText("Downloaded audit-log-2026-09-29.csv.")).toBeInTheDocument();
    const [call] = stub.callsTo(EXPORT);
    expect(call?.url.searchParams.get("actor")).toBe(USER);
    expect(call?.url.searchParams.get("action")).toBe("student.update");
    expect(call?.url.searchParams.get("from")).toBe("2026-06-01T00:00:00+05:30");
    // A date that is not real is left out, as in the table; no page size on a file.
    expect(call?.url.searchParams.has("to")).toBe(false);
    expect(call?.url.searchParams.has("limit")).toBe(false);
  });

  it("without filters, downloads the whole log", async () => {
    stub.routes[EXPORT] = csv;
    const saved = vi.fn();
    setSheetSaverForTesting(saved);
    const user = userEvent.setup();
    renderWithIntl(<AuditView events={{ status: "ready", data: [] }} />);
    await user.click(screen.getByRole("button", { name: "Download CSV" }));
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
    expect([...(stub.callsTo(EXPORT)[0]?.url.searchParams.keys() ?? [])]).toEqual([]);
  });

  it("asks the user to confirm it's them (428) and sends the request once more", async () => {
    let calls = 0;
    stub.routes[EXPORT] = () => (++calls === 1 ? problem(428, "step_up_required") : csv());
    const confirm = vi.fn(async () => true);
    const unregister = registerStepUpHandler("staff", confirm);
    const saved = vi.fn();
    setSheetSaverForTesting(saved);
    const user = userEvent.setup();
    try {
      renderWithIntl(<AuditView events={{ status: "ready", data: [] }} />);
      await user.click(screen.getByRole("button", { name: "Download CSV" }));
      await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(stub.callsTo(EXPORT)).toHaveLength(2);
    } finally {
      unregister();
    }
  });

  it("says nothing was downloaded when the user cancels the confirmation", async () => {
    stub.routes[EXPORT] = () => problem(428, "step_up_required");
    const unregister = registerStepUpHandler("staff", async () => false);
    const saved = vi.fn();
    setSheetSaverForTesting(saved);
    const user = userEvent.setup();
    try {
      renderWithIntl(<AuditView events={{ status: "ready", data: [] }} />);
      await user.click(screen.getByRole("button", { name: "Download CSV" }));
      expect(
        await screen.findByText(messages.en.errors.api.step_up_cancelled.title),
      ).toBeInTheDocument();
      expect(saved).not.toHaveBeenCalled();
      expect(stub.callsTo(EXPORT)).toHaveLength(1);
    } finally {
      unregister();
    }
  });

  it("explains a file that would be too large and how to fix it", async () => {
    stub.routes[EXPORT] = () => problem(422, "too_many_events");
    const user = userEvent.setup();
    renderWithIntl(<AuditView events={{ status: "ready", data: [] }} />, "te");
    await user.click(screen.getByRole("button", { name: messages.te.school.audit.exportCsv }));
    expect(
      await screen.findByText(messages.te.school.audit.errors.too_many_events.title),
    ).toBeInTheDocument();
    expect(screen.getByText(messages.te.school.audit.errors.too_many_events.body)).toBeVisible();
  });
});
