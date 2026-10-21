import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setSchoolDateFormat } from "@/lib/date-format";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { CalendarScreen } from "./CalendarScreen";
import { expectedRange, type CalendarEvent, type CalendarOut, type PortalWindow } from "./data";
import { LinkPortalEventDialog } from "./LinkPortalEventDialog";
import { NextWindow } from "./NextWindow";

/**
 * Portal calendar (US-3101..US-3104, FR-RDY-003..005, FR-RDY-009; docs/17 §5.10): expected and
 * confirmed windows, owners, states and readiness; confirming dates with If-Match; adding and
 * removing the school's own events; the next window on the readiness screen. Synthetic data.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/findings/calendar",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const t = messages.en.portalCalendar;
const YEAR_ID = "0192f3a4-0000-7000-8000-000000000071";
const OWNER = "0192f3a4-0000-7000-8000-0000000000b7";
const OWNER2 = "0192f3a4-0000-7000-8000-0000000000b8";
const SSC = "bseap_ssc_2027_nominal_roll";
const PUT = `PUT /bff/api/v1/dq/calendar/${SSC}`;
const READER = ["dq.readiness.read"];
const MANAGER = ["dq.readiness.read", "readiness.calendar.manage"];

function event(overrides: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    event_key: SSC,
    academic_year_id: YEAR_ID,
    own: false,
    portal: "bseap",
    label_en: "AP SSC March 2027 nominal roll corrections",
    label_te: "",
    profile_keys: ["bseap-ssc-2027"],
    boards: ["BSEAP"],
    classes: ["X"],
    expected: {
      opens_on: "2027-02-01",
      closes_on: "2027-02-05",
      opens_approximate: true,
      closes_approximate: true,
    },
    opens_on: "2027-02-01",
    closes_on: "2027-02-05",
    opens_approximate: true,
    closes_approximate: true,
    confirmed: false,
    state: "expected",
    days_left: 107,
    source_kind: null,
    document_id: null,
    confirmed_by: null,
    confirmed_at: null,
    owner: null,
    lead_days: [30, 14, 7, 1],
    sources: ["https://example.org/press-note"],
    verified: false,
    readiness: [
      { profile_key: "bseap-ssc-2027", label_en: "AP SSC 2027", ready: 142, students: 160 },
    ],
    version: null,
    ...overrides,
  };
}

function calendar(events: CalendarEvent[]): CalendarOut {
  return { academic_year_id: YEAR_ID, academic_year_label: "2026-27", events };
}

let stub: BffStub;

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/dq/calendar/assignees"] = () =>
    Response.json([
      { membership_id: OWNER, display_name: "Synthetic Coordinator", roles: ["exam_coordinator"] },
      { membership_id: OWNER2, display_name: "Synthetic Clerk", roles: ["office_admin"] },
    ]);
  stub.routes["GET /bff/api/v1/circulars"] = () => page([]);
});
afterEach(() => {
  uninstallBffStub();
  setSchoolDateFormat(null);
  expect(intlErrors).toEqual([]);
});

function bodyOf(key: string, index = 0) {
  return JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;
}

describe("portal calendar helpers", () => {
  it("shows an expected window as months, never as exact dates", () => {
    expect(expectedRange("2027-02-01", "2027-02-05", "en")).toBe("Feb 2027");
    expect(expectedRange("2026-09-01", "2026-11-30", "en")).toBe("Sept 2026 – Nov 2026");
  });
});

describe("portal calendar (US-3101)", () => {
  it("lists expected and confirmed windows with owner, state and readiness; readers get no actions", async () => {
    signedIn(READER);
    stub.routes["GET /bff/api/v1/dq/calendar"] = () =>
      Response.json(
        calendar([
          event({
            event_key: "cbse_registration_2027",
            portal: "cbse",
            label_en: "CBSE registration, Class IX and XI (2026-27)",
            confirmed: true,
            state: "open",
            opens_on: "2026-10-01",
            closes_on: "2026-10-30",
            opens_approximate: false,
            closes_approximate: false,
            days_left: 9,
            source_kind: "circular",
            owner: { membership_id: OWNER, display_name: "Synthetic Coordinator" },
            readiness: [],
            version: 2,
          }),
          event(),
        ]),
      );
    renderWithIntl(<CalendarScreen />);
    expect(await screen.findByText("AP SSC March 2027 nominal roll corrections")).toBeVisible();
    expect(screen.getByRole("heading", { level: 1, name: t.title })).toBeVisible();
    expect(screen.getByText("Expected around Feb 2027")).toBeVisible();
    expect(screen.getByText("Confirmed: 01/10/2026 – 30/10/2026")).toBeVisible();
    expect(screen.getByText(t.state.open)).toBeVisible();
    expect(screen.getByText(t.state.expected)).toBeVisible();
    expect(screen.getByText("Closes in 9 days")).toBeVisible();
    expect(screen.getByText("Synthetic Coordinator")).toBeVisible();
    expect(screen.getByText(t.noOwner)).toBeVisible();
    expect(screen.getByText(t.noReadiness)).toBeVisible();
    expect(screen.getByText(/142 of 160/)).toBeVisible();
    expect(screen.getByText(t.unverifiedTitle)).toBeVisible();
    expect(screen.queryByRole("button", { name: t.confirm.open })).toBeNull();
    expect(screen.queryByRole("button", { name: t.add.open })).toBeNull();
  });

  it("says so when no window concerns the school", async () => {
    signedIn(READER);
    stub.routes["GET /bff/api/v1/dq/calendar"] = () => Response.json(calendar([]));
    renderWithIntl(<CalendarScreen />);
    expect(await screen.findByText(t.emptyTitle)).toBeVisible();
  });
});

describe("confirming dates (US-3102, FR-RDY-004)", () => {
  it("sends the typed dates, the source and the owner; no If-Match the first time", async () => {
    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/dq/calendar"] = () => Response.json(calendar([event()]));
    stub.routes[PUT] = () =>
      Response.json(event({ confirmed: true, state: "confirmed", version: 1 }));
    renderWithIntl(<CalendarScreen />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: t.confirm.open }));
    const dialog = within(await screen.findByRole("dialog", { name: t.confirm.title }));
    expect(dialog.getByLabelText(t.dialog.opensOn)).toHaveValue("01/02/2027");
    await user.clear(dialog.getByLabelText(t.dialog.closesOn));
    await user.type(dialog.getByLabelText(t.dialog.closesOn), "04/02/2027");
    await user.selectOptions(dialog.getByLabelText(t.dialog.source), "portal");
    await waitFor(() =>
      expect(dialog.getByRole("option", { name: "Synthetic Coordinator" })).toBeInTheDocument(),
    );
    await user.selectOptions(dialog.getByLabelText(t.dialog.owner), OWNER);
    await user.click(dialog.getByRole("button", { name: t.confirm.save }));
    await waitFor(() => expect(stub.callsTo(PUT)).toHaveLength(1));
    expect(stub.callsTo(PUT)[0]?.headers.get("If-Match")).toBeNull();
    expect(bodyOf(PUT)).toEqual({
      academic_year_id: YEAR_ID,
      opens_on: "2027-02-01",
      closes_on: "2027-02-04",
      source_kind: "portal",
      document_id: null,
      owner_membership_id: OWNER,
    });
  });

  it("changing a confirmation sends If-Match; a closing date before the opening is refused", async () => {
    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/dq/calendar"] = () =>
      Response.json(
        calendar([
          event({ confirmed: true, state: "confirmed", version: 3, source_kind: "phone" }),
        ]),
      );
    stub.routes[PUT] = () => problem(412, "version_mismatch");
    renderWithIntl(<CalendarScreen />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: t.confirm.change }));
    const dialog = within(await screen.findByRole("dialog", { name: t.confirm.title }));
    await user.clear(dialog.getByLabelText(t.dialog.closesOn));
    await user.type(dialog.getByLabelText(t.dialog.closesOn), "01/01/2027");
    await user.click(dialog.getByRole("button", { name: t.confirm.save }));
    expect(stub.callsTo(PUT)).toHaveLength(0);
    await user.clear(dialog.getByLabelText(t.dialog.closesOn));
    await user.type(dialog.getByLabelText(t.dialog.closesOn), "06/02/2027");
    await user.click(dialog.getByRole("button", { name: t.confirm.save }));
    await waitFor(() => expect(stub.callsTo(PUT)).toHaveLength(1));
    expect(stub.callsTo(PUT)[0]?.headers.get("If-Match")).toBe('W/"3"');
    expect(await dialog.findByText(t.errors.version_mismatch.title)).toBeVisible();
  });
});

describe("own events (FR-RDY-005)", () => {
  it("adds an event in the school's words and removes it with If-Match", async () => {
    signedIn(MANAGER);
    const own = event({
      event_key: "school:mandal-meeting-0123",
      own: true,
      portal: "school",
      label_en: "Mandal meeting",
      expected: null,
      confirmed: true,
      state: "confirmed",
      readiness: [],
      version: 1,
    });
    stub.routes["GET /bff/api/v1/dq/calendar"] = () => Response.json(calendar([own]));
    stub.routes["POST /bff/api/v1/dq/calendar"] = () => Response.json(own, { status: 201 });
    const DELETE = `DELETE /bff/api/v1/dq/calendar/${encodeURIComponent(own.event_key)}`;
    stub.routes[DELETE] = () => new Response(null, { status: 204 });
    renderWithIntl(<CalendarScreen />);
    const user = userEvent.setup();
    expect(await screen.findByText(t.ownEvent)).toBeVisible();

    await user.click(screen.getByRole("button", { name: t.add.open }));
    const add = within(await screen.findByRole("dialog", { name: t.add.title }));
    await user.type(add.getByLabelText(t.dialog.label), "Mandal UDISE+ review meeting");
    await user.clear(add.getByLabelText(t.dialog.opensOn));
    await user.type(add.getByLabelText(t.dialog.opensOn), "10/11/2026");
    await user.clear(add.getByLabelText(t.dialog.closesOn));
    await user.type(add.getByLabelText(t.dialog.closesOn), "12/11/2026");
    await user.click(add.getByRole("button", { name: t.add.save }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/dq/calendar")).toHaveLength(1));
    expect(bodyOf("POST /bff/api/v1/dq/calendar")).toMatchObject({
      academic_year_id: YEAR_ID,
      label: "Mandal UDISE+ review meeting",
      opens_on: "2026-11-10",
      closes_on: "2026-11-12",
      source_kind: "circular",
    });

    await user.click(screen.getByRole("button", { name: t.remove.openOwn }));
    const remove = within(await screen.findByRole("dialog", { name: t.remove.titleOwn }));
    expect(remove.getByText(t.remove.consequenceOwn)).toBeVisible();
    await user.click(remove.getByRole("button", { name: t.remove.confirmOwn }));
    await waitFor(() => expect(stub.callsTo(DELETE)).toHaveLength(1));
    const call = stub.callsTo(DELETE)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"1"');
    expect(call?.url.searchParams.get("academic_year_id")).toBe(YEAR_ID);
  });
});

describe("next window on readiness (US-3104, FR-RDY-009)", () => {
  it("names the window, the days left and the state, and links the calendar", () => {
    const window: PortalWindow = {
      event_key: SSC,
      academic_year_id: YEAR_ID,
      portal: "bseap",
      label_en: "AP SSC March 2027 nominal roll corrections",
      label_te: "",
      opens_on: "2027-02-01",
      closes_on: "2027-02-05",
      opens_approximate: true,
      closes_approximate: true,
      confirmed: false,
      state: "expected",
      days_left: 14,
    };
    renderWithIntl(<NextWindow window={window} />);
    const line = screen.getByTestId("next-window");
    expect(line).toHaveTextContent(
      "Next window: AP SSC March 2027 nominal roll corrections. Closes in 14 days",
    );
    expect(within(line).getByText(t.state.expected)).toBeVisible();
    expect(within(line).getByRole("link", { name: t.openCalendar })).toHaveAttribute(
      "href",
      "/findings/calendar",
    );
  });
});

describe("linking a circular task (FR-RDY-010)", () => {
  it("links the task to a window of the year that has not closed", async () => {
    signedIn(MANAGER);
    const TASK = "0192f3a4-0000-7000-8000-00000000f001";
    const LINK = `POST /bff/api/v1/tasks/${TASK}/portal-event`;
    stub.routes["GET /bff/api/v1/dq/calendar"] = () =>
      Response.json(calendar([event(), event({ event_key: "old_window", state: "closed" })]));
    stub.routes[LINK] = () => Response.json(event({ confirmed: true, version: 1 }));
    renderWithIntl(
      <LinkPortalEventDialog
        task={{ id: TASK, title: "Send the nominal roll", due_on: "2027-02-03" }}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: t.link.open }));
    const dialog = within(await screen.findByRole("dialog", { name: t.link.title }));
    await waitFor(() =>
      expect(
        dialog.getByRole("option", { name: "AP SSC March 2027 nominal roll corrections" }),
      ).toBeInTheDocument(),
    );
    expect(dialog.getAllByRole("option")).toHaveLength(2); // the closed window is left out
    await user.selectOptions(dialog.getByLabelText(t.link.event), SSC);
    await user.click(dialog.getByRole("button", { name: t.link.save }));
    await waitFor(() => expect(stub.callsTo(LINK)).toHaveLength(1));
    expect(bodyOf(LINK)).toEqual({ event_key: SSC, academic_year_id: YEAR_ID });
  });
});
