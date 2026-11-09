import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import {
  stepBody,
  stepFormSchema,
  type FollowupItem,
  type FollowupList,
  type TodoItem,
  type TodoList,
} from "./data";
import { FollowupsScreen } from "./FollowupsScreen";
import { UdiseTodoScreen } from "./UdiseTodoScreen";

/**
 * FO-01 follow-ups and FO-03 the UDISE+ to-do list (FR-UDI-021..FR-UDI-040): request bodies,
 * the follow-up states and the tick / undo flow. Synthetic data only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/findings/udise-todo",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const tf = messages.en.udiseFollowup;
const tt = messages.en.udiseTodo;
const STUDENT = "0192f3a4-0000-7000-8000-0000000000c1";
const READER = ["dq.readiness.read", "student.read_basic"];
const MANAGER = [...READER, "dq.readiness.manage"];
const SSC = "bseap-ssc-2027";

let stub: BffStub;

function signIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

beforeEach(() => {
  stub = installBffStub("staff");
});

afterEach(() => {
  uninstallBffStub();
});

function followup(over: Partial<FollowupItem> = {}): FollowupItem {
  return {
    student: { id: STUDENT, display_name: "Synthetica Kumari", admission_no: "SYN-12" },
    section_id: null,
    readiness_status: "needs_parent",
    fix_owner: "parent_aadhaar",
    state: "overdue",
    overdue_reason: "promise_passed",
    last_event: "promised_by",
    last_on: "2026-10-20",
    promised_on: "2026-11-05",
    steps: 2,
    ...over,
  };
}

function followupList(items: FollowupItem[]): FollowupList {
  return {
    profile_key: SSC,
    today: "2026-11-09",
    overdue_after_days: 14,
    totals: [
      {
        fix_owner: "parent_aadhaar",
        counts: { open: 18, not_started: 3, waiting: 8, overdue: 7, update_reported: 0 },
      },
      {
        fix_owner: "school_udise",
        counts: { open: 0, not_started: 0, waiting: 0, overdue: 0, update_reported: 0 },
      },
    ],
    items,
  };
}

function todo(over: Partial<TodoItem> = {}): TodoItem {
  return {
    item_key: `admission:${STUDENT}`,
    kind: "admission",
    action: "admission_with_pen",
    student: { id: STUDENT, display_name: "Synthetica Kumari", admission_no: "SYN-12" },
    class_section: "Class IX A",
    event_on: "2026-07-01",
    certificate_serial: null,
    progression: null,
    state: "open",
    overdue: true,
    marked_at: null,
    version: null,
    ...over,
  };
}

function todoList(items: TodoItem[]): TodoList {
  return {
    verified: false,
    source: ["https://example.invalid/udise"],
    since: "2026-03-03",
    today: "2026-11-09",
    overdue_after_days: 14,
    totals: { open: 1, overdue: 1, done: 1, not_needed: 0 },
    items,
  };
}

describe("FR-UDI-027 follow-up step body", () => {
  it("sends a channel only for steps that name one and a promise date only for a promise", () => {
    expect(
      stepBody({
        studentIds: [STUDENT],
        owner: "parent_aadhaar",
        event: "slip_given",
        channel: "phone",
        promisedOn: "2026-12-01",
      }),
    ).toEqual({ student_ids: [STUDENT], fix_owner: "parent_aadhaar", event: "slip_given" });
    expect(
      stepBody({
        studentIds: [STUDENT],
        owner: "parent_aadhaar",
        event: "promised_by",
        occurredOn: "2026-11-09",
        promisedOn: "2026-12-01",
      }),
    ).toMatchObject({ occurred_on: "2026-11-09", promised_on: "2026-12-01" });
    expect(
      stepBody({
        studentIds: [STUDENT],
        owner: "school_udise",
        event: "request_sent",
        channel: "email",
      }),
    ).toMatchObject({ channel: "email" });
  });

  it("needs a step and a date", () => {
    expect(stepFormSchema.safeParse({ event: "", occurred_on: "" }).success).toBe(false);
  });
});

describe("FR-UDI-023 follow-up list", () => {
  it("shows who waits, the overdue reason and the counts", async () => {
    signIn(READER);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/follow-ups`] = () =>
      Response.json(followupList([followup()]));
    renderWithIntl(<FollowupsScreen profileKey={SSC} sectionId={null} />);
    expect(await screen.findByText(tf.state.overdue_promise_passed)).toBeInTheDocument();
    expect(screen.getByText(tf.waiting.parent_aadhaar)).toBeInTheDocument();
    expect(screen.getByText("7 overdue, 3 not contacted yet")).toBeInTheDocument();
    // Readers without dq.readiness.manage get no record button.
    expect(screen.queryByRole("button", { name: /Record a step/ })).not.toBeInTheDocument();
  });

  it("says so when nothing waits outside the school", async () => {
    signIn(MANAGER);
    stub.routes[`GET /bff/api/v1/dq/readiness/${SSC}/follow-ups`] = () =>
      Response.json(followupList([]));
    renderWithIntl(<FollowupsScreen profileKey={SSC} sectionId={null} />);
    expect(await screen.findByText(tf.emptyTitle)).toBeInTheDocument();
  });
});

describe("FR-UDI-035 UDISE+ to-do ticks", () => {
  const PUT = `PUT /bff/api/v1/dq/udise-todo/admission%3A${STUDENT}`;

  it("lists open items first and ticks one done without If-Match", async () => {
    signIn(MANAGER);
    const done = todo({
      item_key: `progression:${STUDENT}`,
      kind: "progression",
      action: "progression",
      student: null,
      progression: { promoted: 30, held_back: 2, graduated: 0 },
      state: "done",
      overdue: false,
      version: 1,
    });
    stub.routes["GET /bff/api/v1/dq/udise-todo"] = () => Response.json(todoList([todo(), done]));
    stub.routes[PUT] = () =>
      Response.json({ ...todo(), state: "done", overdue: false, version: 1 });
    renderWithIntl(<UdiseTodoScreen />);
    expect(await screen.findByText(tt.action.admission_with_pen)).toBeInTheDocument();
    expect(screen.getByText(tt.unverifiedTitle)).toBeInTheDocument();
    expect(screen.getByText(tt.waitingLong)).toBeInTheDocument();
    // The ticked progression item is under "Done", not in the open view.
    expect(screen.queryByText(tt.action.progression)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Mark done in UDISE\+/ }));
    await waitFor(() => expect(stub.callsTo(PUT)).toHaveLength(1));
    const call = stub.callsTo(PUT)[0]!;
    expect(JSON.parse(call.body)).toEqual({ state: "done" });
    expect(call.headers.get("If-Match")).toBeNull();
  });

  it("readers see the list without tick buttons", async () => {
    signIn(READER);
    stub.routes["GET /bff/api/v1/dq/udise-todo"] = () => Response.json(todoList([todo()]));
    renderWithIntl(<UdiseTodoScreen />);
    expect(await screen.findByText(tt.action.admission_with_pen)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: tt.markNotNeeded })).not.toBeInTheDocument();
  });
});
