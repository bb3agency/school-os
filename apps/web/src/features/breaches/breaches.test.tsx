import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { me } from "@/test/records-fixtures";
import { messages, renderWithIntl } from "@/test/render";
import { notificationHref } from "@/features/notifications/data";
import { BreachDetailScreen } from "./BreachDetailScreen";
import { BreachesScreen } from "./BreachesScreen";
import { NewBreachScreen } from "./NewBreachScreen";
import { dutyClock, registerOrder, type Breach, type BreachDetail, type Duty } from "./types";

const push = vi.fn();
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/privacy/breaches",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

/** Breach register (ADR-0050; US-2701, US-2702; FR-DPDP-030..035). Synthetic data only. */
const bm = messages.en.breaches;
const NOW = new Date("2026-10-21T06:00:00Z");
const B1 = "0192f3a4-0000-7000-8000-00000000b001";
const B2 = "0192f3a4-0000-7000-8000-00000000b002";
const READ = "privacy.breach.read";
const MANAGE = "privacy.breach.manage";
let stub: BffStub;

function duties(extra: Partial<Record<Duty["key"], Partial<Duty>>> = {}): Duty[] {
  return (["board_notice", "board_report", "people_notified"] as const).map((key) => ({
    key,
    due_at:
      key === "board_notice"
        ? "2026-10-21T00:00:00Z"
        : key === "board_report"
          ? "2026-10-22T23:00:00Z"
          : null,
    done_at: null,
    method: null,
    overdue: key === "board_notice",
    default_rule: key === "board_notice",
    ...extra[key],
  }));
}

function breach(extra: Partial<Breach> = {}): Breach {
  return {
    id: B1,
    origin: "school",
    noticed_at: "2026-10-20T00:00:00Z",
    categories: ["contact", "attendance"],
    approx_children: 1284,
    approx_guardians: 2100,
    approx_staff: 0,
    status: "open",
    duties: duties(),
    next_duty: { key: "board_notice", due_at: "2026-10-21T00:00:00Z", hours_left: -6 },
    processor_facts: null,
    platform_reopens: 0,
    platform_reopened_at: null,
    platform_incident_id: null,
    closed_at: null,
    created_at: "2026-10-20T00:10:00Z",
    version: 3,
    ...extra,
  };
}

function detail(extra: Partial<BreachDetail> = {}): BreachDetail {
  return {
    ...breach(),
    description: "A printed attendance list with parents' phone numbers was left on a bus.",
    containment: null,
    close_reason: null,
    affected: { students: 0, guardians: 0, staff: 0 },
    recorded_by_name: "Venkata Naga Sai Lakshmi Prasanna Kumari Bhimavarapu",
    ...extra,
  };
}

function setMe(permissions: readonly string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  stub = installBffStub("staff");
  push.mockReset();
});
afterEach(() => {
  uninstallBffStub();
  vi.useRealTimers();
});

describe("duty clocks (FR-DPDP-032)", () => {
  it("counts whole hours left, overdue, within the hour, done and no clock", () => {
    expect(dutyClock({ due_at: "2026-10-22T23:30:00Z", done_at: null }, NOW)).toEqual({
      state: "due",
      hours: 41,
    });
    expect(dutyClock({ due_at: "2026-10-21T00:00:00Z", done_at: null }, NOW)).toEqual({
      state: "overdue",
      hours: 6,
    });
    expect(dutyClock({ due_at: "2026-10-21T06:30:00Z", done_at: null }, NOW)).toEqual({
      state: "dueSoon",
    });
    expect(dutyClock({ due_at: null, done_at: null }, NOW)).toEqual({ state: "noClock" });
    expect(
      dutyClock({ due_at: "2026-10-21T00:00:00Z", done_at: "2026-10-20T10:00:00Z" }, NOW).state,
    ).toBe("done");
  });

  it("a breach notification opens the breach (resource_type breach_record)", () => {
    expect(notificationHref({ resource_type: "breach_record", resource_id: B1 })).toBe(
      `/privacy/breaches/${B1}`,
    );
  });

  it("puts open notices from SchoolOS first", () => {
    const rows = registerOrder([
      breach({ id: B1 }),
      breach({ id: B2, origin: "processor_notice" }),
    ]);
    expect(rows.map((row) => row.id)).toEqual([B2, B1]);
  });
});

describe("US-2701 breach register", () => {
  it("shows the next duty with its time left and a processor notice on top", async () => {
    setMe([READ, MANAGE]);
    stub.routes["GET /bff/api/v1/privacy/breaches"] = () =>
      Response.json([
        breach(),
        breach({
          id: B2,
          origin: "processor_notice",
          next_duty: { key: "board_report", due_at: "2026-10-22T23:00:00Z", hours_left: 41 },
        }),
      ]);
    renderWithIntl(<BreachesScreen />);
    expect(await screen.findByText("Board report due in 41 h")).toBeInTheDocument();
    expect(screen.getByText("Board notice overdue by 6 h")).toBeInTheDocument();
    expect(screen.getByText("SchoolOS reported a breach on its side")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: bm.record })).toHaveAttribute(
      "href",
      expect.stringContaining("/privacy/breaches/new"),
    );
  });

  it("auditors read the register without the record action", async () => {
    setMe([READ]);
    stub.routes["GET /bff/api/v1/privacy/breaches"] = () => Response.json([]);
    renderWithIntl(<BreachesScreen />);
    expect(await screen.findByText(bm.emptyTitle)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: bm.record })).toBeNull();
  });

  it("without privacy.breach.read nothing is loaded", async () => {
    setMe(["student.read_basic"]);
    renderWithIntl(<BreachesScreen />);
    expect(await screen.findByText(bm.noPermissionTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/privacy/breaches")).toEqual([]);
  });

  it("the form lists every problem in an error summary before sending", async () => {
    setMe([READ, MANAGE]);
    const user = userEvent.setup();
    renderWithIntl(<NewBreachScreen />);
    await user.click(await screen.findByRole("button", { name: bm.form.submit }));
    const summary = await screen.findByRole("alert");
    expect(within(summary).getByText(messages.en.validation.chooseCategories)).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/privacy/breaches")).toEqual([]);
  });
});

describe("US-2702 breach detail", () => {
  it("shows three duty clocks and records a duty as done with If-Match", async () => {
    setMe([READ, MANAGE]);
    stub.routes[`GET /bff/api/v1/privacy/breaches/${B1}`] = () => Response.json(detail());
    stub.routes[`PATCH /bff/api/v1/privacy/breaches/${B1}`] = () =>
      Response.json(detail({ version: 4 }));
    const user = userEvent.setup();
    renderWithIntl(<BreachDetailScreen breachId={B1} />);
    expect(await screen.findByText("Board notice overdue by 6 h")).toBeInTheDocument();
    expect(screen.getByText("Board report due in 41 h")).toBeInTheDocument();
    expect(screen.getByText("Families and staff told: as soon as possible")).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: bm.detail.markDone });
    expect(buttons).toHaveLength(3);
    await user.click(buttons[0] as HTMLElement);
    const dialog = screen.getByRole("dialog", { name: "Mark done: Board notice" });
    await user.selectOptions(within(dialog).getByLabelText(bm.detail.method), "portal");
    await user.click(within(dialog).getByRole("button", { name: bm.detail.markDone }));
    await waitFor(() =>
      expect(stub.callsTo(`PATCH /bff/api/v1/privacy/breaches/${B1}`)).toHaveLength(1),
    );
    const call = stub.callsTo(`PATCH /bff/api/v1/privacy/breaches/${B1}`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"3"');
    const body = JSON.parse(call?.body ?? "{}") as { board_notice?: { method: string } };
    expect(body.board_notice?.method).toBe("portal");
  });

  it("closing with open duties needs a reason; the API's refusal is explained", async () => {
    setMe([READ, MANAGE]);
    stub.routes[`GET /bff/api/v1/privacy/breaches/${B1}`] = () => Response.json(detail());
    stub.routes[`PATCH /bff/api/v1/privacy/breaches/${B1}`] = () => problem(409, "duties_open");
    const user = userEvent.setup();
    renderWithIntl(<BreachDetailScreen breachId={B1} />);
    await user.click(await screen.findByRole("button", { name: bm.detail.close }));
    const dialog = screen.getByRole("dialog", { name: bm.detail.closeTitle });
    await user.click(within(dialog).getByRole("button", { name: bm.detail.close }));
    expect(
      await within(dialog).findByText(messages.en.validation.reasonTooShort),
    ).toBeInTheDocument();
    await user.type(
      within(dialog).getByLabelText(bm.detail.closeReason),
      "The bus company returned the list unopened.",
    );
    await user.click(within(dialog).getByRole("button", { name: bm.detail.close }));
    expect(await within(dialog).findByText(bm.errors.duties_open.title)).toBeInTheDocument();
  });

  it("a reader sees the facts but no actions", async () => {
    setMe([READ]);
    stub.routes[`GET /bff/api/v1/privacy/breaches/${B1}`] = () => Response.json(detail());
    renderWithIntl(<BreachDetailScreen breachId={B1} />);
    expect(await screen.findByText(/left on a bus/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: bm.detail.markDone })).toBeNull();
    expect(screen.queryByRole("button", { name: bm.drafts.makeNotice })).toBeNull();
  });

  it("D49: a breach SchoolOS reopened says so and can be closed again", async () => {
    setMe([READ, MANAGE]);
    stub.routes[`GET /bff/api/v1/privacy/breaches/${B1}`] = () =>
      Response.json(
        detail({
          origin: "processor_notice",
          platform_incident_id: "0192f3a4-0000-7000-8000-00000000e001",
          platform_reopens: 2,
          platform_reopened_at: "2026-10-21T04:00:00Z",
        }),
      );
    renderWithIntl(<BreachDetailScreen breachId={B1} />);
    expect(await screen.findByText(bm.detail.reopenedTitle)).toBeInTheDocument();
    expect(
      screen.getByText(/reopened this incident on 21\/10\/2026.*2 times in all/),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: bm.detail.close })).toBeInTheDocument();
  });

  it("D49: no reopen note on a breach SchoolOS never reopened", async () => {
    setMe([READ]);
    stub.routes[`GET /bff/api/v1/privacy/breaches/${B1}`] = () => Response.json(detail());
    renderWithIntl(<BreachDetailScreen breachId={B1} />);
    expect(await screen.findByText(/left on a bus/)).toBeInTheDocument();
    expect(screen.queryByText(bm.detail.reopenedTitle)).toBeNull();
  });
});
