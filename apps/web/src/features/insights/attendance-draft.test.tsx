import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClearDeviceDrafts } from "@/features/auth/ClearDeviceDrafts";
import { resetUnsavedGuardForTesting } from "@/features/sheets/unsaved";
import { clearDeviceDrafts, deviceDraftKeys, draftStorage } from "@/lib/device-drafts";
import { installBffStub, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { ME_MEMBERSHIP, SECTION, me, structureRoutes } from "@/test/school-fixtures";
import {
  draftIdempotencyKey,
  draftKey,
  listDrafts,
  nextDraft,
  readDraft,
  retryDelay,
  writeDraft,
  type AttendanceDraft,
} from "./attendance-draft";
import { AttendanceScreen } from "./AttendanceScreen";
import { draftKeepHours, todayIst, type AttendanceDay } from "./data";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/attendance",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en;
const RAVI = "0192f3a4-0000-7000-8000-00000000e501";
const ANJALI = "0192f3a4-0000-7000-8000-00000000e502";
const OTHER_PERSON = "0192f3a4-0000-7000-8000-0000000000b9";
const POST = `POST /bff/api/v1/sections/${SECTION}/attendance`;
const GET = `GET /bff/api/v1/sections/${SECTION}/attendance`;
const HOUR = 3_600_000;

let stub: BffStub;

function day(overrides: Partial<AttendanceDay> = {}): AttendanceDay {
  return {
    section: {
      id: SECTION,
      class_id: "0192f3a4-0000-7000-8000-0000000000c9",
      academic_year_id: "0192f3a4-0000-7000-8000-0000000000y1",
      label: "IX-A",
    },
    on_date: todayIst(),
    marked: false,
    students: [
      {
        student: {
          student_id: RAVI,
          full_name: "Synthetica Ravi",
          admission_no: "SYN-0001",
          roll_no: "1",
        },
        status: null,
        version: 0,
      },
      {
        student: {
          student_id: ANJALI,
          full_name: "Synthetica Anjali",
          admission_no: "SYN-0002",
          roll_no: "2",
        },
        status: null,
        version: 0,
      },
    ],
    counts: {},
    ...overrides,
  };
}

function stored(overrides: Partial<AttendanceDraft> = {}): AttendanceDraft {
  return {
    v: 1,
    owner: ME_MEMBERSHIP,
    section: SECTION,
    section_label: "IX-A",
    date: todayIst(),
    id: "0192f3a4-0000-7000-8000-00000000d001",
    rev: 0,
    queued: false,
    updated_at: Date.now() - HOUR,
    keep_until: Date.now() + 23 * HOUR,
    entries: [{ student_id: ANJALI, status: "absent", expected_version: 0 }],
    ...overrides,
  };
}

function setOnline(value: boolean) {
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => value });
}

async function openSection() {
  renderWithIntl(<AttendanceScreen />);
  const picker = await screen.findByLabelText(en.attendance.section);
  await waitFor(() => expect(within(picker).getAllByRole("option").length).toBeGreaterThan(1));
  await userEvent.selectOptions(picker, SECTION);
}

beforeEach(() => {
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(me(["attendance.read", "attendance.record"]));
  stub.routes[GET] = () => Response.json(day());
  setOnline(true);
});
afterEach(() => {
  uninstallBffStub();
  resetUnsavedGuardForTesting();
  clearDeviceDrafts();
  setOnline(true);
  expect(intlErrors).toEqual([]);
});

describe("the draft kept on the device (FR-ATT-026, FR-ATT-032)", () => {
  it("keeps ids and statuses per person, drops expired and other people's drafts", () => {
    expect(writeDraft(stored())).toBe(true);
    expect(readDraft(ME_MEMBERSHIP, SECTION, todayIst())?.entries).toHaveLength(1);
    const raw = draftStorage()?.getItem(draftKey(SECTION, todayIst())) ?? "";
    expect(raw).not.toContain("Synthetica");
    expect(raw).not.toContain("SYN-");
    // Someone else signed in on this PC: the draft is removed, not shown.
    expect(listDrafts(OTHER_PERSON)).toEqual([]);
    expect(deviceDraftKeys()).toEqual([]);
    writeDraft(stored({ keep_until: Date.now() - 1 }));
    expect(listDrafts(ME_MEMBERSHIP)).toEqual([]);
    expect(deviceDraftKeys()).toEqual([]);
    draftStorage()?.setItem(draftKey(SECTION, todayIst()), "{not json");
    expect(listDrafts(ME_MEMBERSHIP)).toEqual([]);
  });

  it("a changed draft gets a new revision and so a new Idempotency-Key (FR-ATT-027)", () => {
    const fields = {
      owner: ME_MEMBERSHIP,
      section: SECTION,
      section_label: "IX-A",
      date: todayIst(),
      entries: stored().entries,
      queued: false,
      keepHours: 24,
    };
    const first = nextDraft(null, fields, 1_000, () => "0192f3a4-0000-7000-8000-00000000d002");
    expect(first.keep_until).toBe(1_000 + 24 * HOUR);
    const same = nextDraft(first, { ...fields, queued: true }, 2_000);
    expect(draftIdempotencyKey(same)).toBe(draftIdempotencyKey(first));
    const changed = nextDraft(
      same,
      { ...fields, entries: [{ student_id: ANJALI, status: "late", expected_version: 0 }] },
      3_000,
    );
    expect(changed.rev).toBe(1);
    expect(draftIdempotencyKey(changed)).not.toBe(draftIdempotencyKey(first));
    expect(draftIdempotencyKey(changed)).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect([0, 1, 2, 3, 4, 9].map(retryDelay)).toEqual([
      5_000, 15_000, 30_000, 60_000, 120_000, 120_000,
    ]);
  });

  it("the keep time comes from the day, bounded, 24 hours by default", () => {
    expect(draftKeepHours(day())).toBe(24);
    expect(draftKeepHours({ ...day(), draft_keep_hours: 8 } as AttendanceDay)).toBe(8);
    expect(draftKeepHours({ ...day(), draft_keep_hours: 500 } as AttendanceDay)).toBe(24);
  });

  it("the signed-out page forgets every draft on this device", () => {
    writeDraft(stored());
    render(<ClearDeviceDrafts />);
    expect(deviceDraftKeys()).toEqual([]);
  });
});

describe("saving over a bad network (FR-ATT-027, FR-ATT-033)", () => {
  it("offline: says not saved yet, keeps the day, and sends it when the network returns", async () => {
    stub.routes[POST] = () => Response.json({ written: 2, unchanged: 0, dates: [todayIst()] });
    await openSection();
    const anjali = await screen.findByRole("group", { name: "Attendance of Synthetica Anjali" });
    await userEvent.click(within(anjali).getByRole("radio", { name: en.attendance.status.absent }));
    // Kept on the device at once, before any save.
    expect(readDraft(ME_MEMBERSHIP, SECTION, todayIst())?.entries).toEqual([
      { student_id: ANJALI, status: "absent", expected_version: 0 },
    ]);
    expect(screen.getByText(en.attendance.draft.notSavedYet)).toBeVisible();
    setOnline(false);
    await userEvent.click(screen.getByRole("button", { name: en.attendance.save }));
    expect(await screen.findByText(en.attendance.draft.offline)).toBeVisible();
    expect(stub.callsTo(POST)).toHaveLength(0);
    expect(readDraft(ME_MEMBERSHIP, SECTION, todayIst())?.queued).toBe(true);
    setOnline(true);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    await waitFor(() => expect(stub.callsTo(POST)).toHaveLength(1));
    const call = stub.callsTo(POST)[0];
    expect(call?.headers.get("Idempotency-Key")).toMatch(/^att-/);
    expect(JSON.parse(call?.body ?? "{}").entries).toHaveLength(2);
    expect(await screen.findByText("Saved 2 entries.")).toBeVisible();
    expect(deviceDraftKeys()).toEqual([]);
  });

  it("a failed answer is retried with the same Idempotency-Key", async () => {
    let posts = 0;
    stub.routes[POST] = () => {
      posts += 1;
      return posts === 1
        ? problem(503, "service_unavailable")
        : Response.json({ written: 2, unchanged: 0, dates: [todayIst()] });
    };
    await openSection();
    await screen.findByRole("group", { name: "Attendance of Synthetica Anjali" });
    await userEvent.click(screen.getByRole("button", { name: en.attendance.save }));
    expect(await screen.findByText(en.attendance.draft.retrying)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: en.attendance.draft.tryNow }));
    await waitFor(() => expect(stub.callsTo(POST)).toHaveLength(2));
    const [first, second] = stub.callsTo(POST);
    expect(second?.headers.get("Idempotency-Key")).toBe(first?.headers.get("Idempotency-Key"));
    expect(second?.body).toBe(first?.body);
    expect(await screen.findByText("Saved 2 entries.")).toBeVisible();
    expect(deviceDraftKeys()).toEqual([]);
  });

  it("a kept draft comes back after a reload and is saved only when the teacher saves", async () => {
    writeDraft(stored());
    stub.routes[POST] = () => Response.json({ written: 2, unchanged: 0, dates: [todayIst()] });
    await openSection();
    expect(await screen.findByText(en.attendance.draft.restoredTitle)).toBeVisible();
    const anjali = screen.getByRole("group", { name: "Attendance of Synthetica Anjali" });
    expect(within(anjali).getByRole("radio", { name: en.attendance.status.absent })).toBeChecked();
    expect(stub.callsTo(POST)).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: en.attendance.save }));
    await waitFor(() => expect(stub.callsTo(POST)).toHaveLength(1));
    expect(await screen.findByText("Saved 2 entries.")).toBeVisible();
  });

  it("a queued draft restored after a reload is sent again with its key", async () => {
    const draft = stored({ queued: true });
    writeDraft(draft);
    stub.routes[POST] = () => Response.json({ written: 1, unchanged: 0, dates: [todayIst()] });
    await openSection();
    await waitFor(() => expect(stub.callsTo(POST)).toHaveLength(1));
    expect(stub.callsTo(POST)[0]?.headers.get("Idempotency-Key")).toMatch(/^att-/);
    expect(await screen.findByText("Saved 1 entry.")).toBeVisible();
    expect(deviceDraftKeys()).toEqual([]);
  });

  it("a draft older than someone else's save is not sent over it (FR-ATT-029)", async () => {
    writeDraft(stored({ queued: true }));
    stub.routes[GET] = () =>
      Response.json(
        day({
          marked: true,
          students: day().students.map((row) => ({ ...row, status: "present", version: 1 })),
        }),
      );
    await openSection();
    expect(await screen.findByText(en.attendance.changedBySomeone)).toBeVisible();
    expect(screen.getByText("Saved now: Present")).toBeVisible();
    const anjali = screen.getByRole("group", { name: "Attendance of Synthetica Anjali" });
    expect(within(anjali).getByRole("radio", { name: en.attendance.status.absent })).toBeChecked();
    expect(stub.callsTo(POST)).toHaveLength(0);
  });

  it("another day not saved yet is listed and opens", async () => {
    writeDraft(stored({ date: "2026-09-01" }));
    await openSection();
    const open = await screen.findByRole("button", { name: "IX-A, 01/09/2026" });
    expect(screen.getByText("1 day not saved yet")).toBeVisible();
    await userEvent.click(open);
    // Today's day is not marked yet, so the switch asks first (XA-05).
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: en.attendance.discard.discard }),
    );
    await waitFor(() =>
      expect(stub.callsTo(GET).at(-1)?.url.searchParams.get("date")).toBe("2026-09-01"),
    );
  });
});
