import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notificationHref } from "@/features/notifications/data";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { CircularDetailScreen } from "./CircularDetailScreen";
import { CircularsScreen } from "./CircularsScreen";
import {
  dueState,
  looksPersonal,
  noticeText,
  setCircularsPollDelayForTesting,
  setNoticeDownloadOpenerForTesting,
  todayIst,
  type Circular,
  type CircularDetail,
  type Notice,
  type Task,
} from "./data";
import { NoticeDetailScreen } from "./NoticeDetailScreen";
import { NoticesScreen } from "./NoticesScreen";
import { TasksScreen } from "./TasksScreen";

const push = vi.fn();
vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/circulars",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en;
const DOC = "0192f3a4-0000-7000-8000-00000000d001";
const SUGGESTION = "0192f3a4-0000-7000-8000-00000000e001";
const TASK = "0192f3a4-0000-7000-8000-00000000f001";
const NOTICE = "0192f3a4-0000-7000-8000-00000000a111";
const OWNER = "0192f3a4-0000-7000-8000-0000000000b7";
const QUOTE = "All Headmasters are requested to submit the UDISE+ sheets on or before 15/10/2026.";
const SIGNED = "https://s3.synthetic.test/sos-files/t/x/exports/n/notice.pdf?X-Amz-Signature=abc";

function circular(overrides: Partial<Circular> = {}): Circular {
  return {
    document_id: DOC,
    title: "UDISE+ data collection",
    issuer: "DEO Sitarampuram",
    issued_on: "2026-10-01",
    current_version_no: 1,
    reading_status: "ready",
    reading_error: null,
    reviewed: false,
    open_suggestions: 1,
    tasks: 0,
    created_at: "2026-10-01T04:30:00Z",
    ...overrides,
  };
}

function detail(overrides: Partial<CircularDetail["reading"] & object> = {}): CircularDetail {
  return {
    ...circular(),
    sensitivity: "C1",
    reading: {
      id: "0192f3a4-0000-7000-8000-00000000c001",
      version_no: 1,
      status: "ready",
      error_code: null,
      issuer: "Office of the District Educational Officer, Sitarampuram",
      reference_no: "Rc.No.101/A/2026",
      issued_on: "2026-10-01",
      subject: "UDISE+ data collection",
      summary_en: "Submit the UDISE+ sheets by 15 October.",
      summary_te: "UDISE+ వివరాలు 15 అక్టోబర్ లోగా సమర్పించండి.",
      summary_sources: [{ source: `sos://doc/${DOC}/v1#p1`, passage: 1, page: 1, quote: QUOTE }],
      suggestions: [
        {
          id: SUGGESTION,
          position: 1,
          title: "Submit the UDISE+ sheets",
          details: null,
          due_on: "2026-10-15",
          citation: { source: `sos://doc/${DOC}/v1#p1`, passage: 1, page: 1, quote: QUOTE },
          status: "suggested",
          task_id: null,
          decided_at: null,
          version: 1,
        },
      ],
      suggestions_dropped: 0,
      passages_sent: 1,
      passages_total: 1,
      attempts: 1,
      can_retry: false,
      ai_generated: true,
      reviewed_at: null,
      reviewed_by: null,
      completed_at: "2026-10-01T04:31:00Z",
      version: 2,
      ...overrides,
    },
  };
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: TASK,
    title: "Submit the UDISE+ sheets",
    details: null,
    owner: { membership_id: OWNER, display_name: "Synthetic Clerk" },
    due_on: "2020-01-01",
    status: "open",
    overdue: true,
    source: "circular",
    document_id: DOC,
    citation: { source: `sos://doc/${DOC}/v1#p1`, passage: 1, page: 1, quote: QUOTE },
    created_by: null,
    created_at: "2026-10-01T04:30:00Z",
    completed_at: null,
    cancelled_at: null,
    version: 3,
    ...overrides,
  };
}

function notice(overrides: Partial<Notice> = {}): Notice {
  return {
    id: NOTICE,
    source: "circular",
    document_id: DOC,
    status: "draft",
    ai_drafted: true,
    draft_error: null,
    title_en: "Sports day",
    body_en: "Sports day is on 14/11/2026.",
    title_te: "క్రీడా దినోత్సవం",
    body_te: "క్రీడా దినోత్సవం 14/11/2026న.",
    created_by: null,
    approved_by: null,
    approved_at: null,
    render_status: null,
    render_error: null,
    files_available: false,
    created_at: "2026-10-02T04:30:00Z",
    updated_at: "2026-10-02T04:30:00Z",
    version: 1,
    ...overrides,
  };
}

/** A notice the AI is still drafting in the background (FR-NOTICE-003). */
function drafting(overrides: Partial<Notice> = {}): Partial<Notice> {
  return {
    status: "drafting",
    ai_drafted: false,
    title_en: "",
    body_en: "",
    title_te: "",
    body_te: "",
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

let stub: BffStub;
let opened: string[];

beforeEach(() => {
  stub = installBffStub("staff");
  opened = [];
  push.mockReset();
  setCircularsPollDelayForTesting(() => 20);
  setNoticeDownloadOpenerForTesting((url) => opened.push(url));
  stub.routes["GET /bff/api/v1/task-assignees"] = () =>
    Response.json([
      { membership_id: OWNER, display_name: "Synthetic Clerk", roles: ["office_staff"] },
    ]);
});
afterEach(() => {
  uninstallBffStub();
  setCircularsPollDelayForTesting(null);
  setNoticeDownloadOpenerForTesting(null);
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions));
}

describe("circulars inbox and detail (US-1601, US-1602)", () => {
  it("lists circulars that still need a person, with the reading status", async () => {
    signedIn(["document.read"]);
    stub.routes["GET /bff/api/v1/circulars"] = () =>
      page([
        circular(),
        circular({
          document_id: "0192f3a4-0000-7000-8000-00000000d002",
          title: "Done one",
          reviewed: true,
        }),
      ]);
    renderWithIntl(<CircularsScreen />);
    expect(await screen.findByRole("link", { name: "UDISE+ data collection" })).toBeVisible();
    expect(screen.queryByText("Done one")).toBeNull();
    expect(screen.getByText(en.circulars.status.ready)).toBeVisible();
    await userEvent.click(screen.getByRole("radio", { name: en.circulars.showAll }));
    expect(screen.getByText("Done one")).toBeVisible();
  });

  it("shows the AI reading with source chips and confirms a suggestion with If-Match", async () => {
    signedIn(["document.read", "circular.review", "notice.draft"]);
    stub.routes[`GET /bff/api/v1/circulars/${DOC}`] = () => Response.json(detail());
    stub.routes[`POST /bff/api/v1/circular-suggestions/${SUGGESTION}/confirm`] = () =>
      Response.json(task(), { status: 201 });
    renderWithIntl(<CircularDetailScreen documentId={DOC} />);
    expect(await screen.findByText("Rc.No.101/A/2026")).toBeVisible();
    expect(screen.getAllByText(en.circulars.aiBadge).length).toBeGreaterThan(0);
    // Every AI statement comes with its source: the sentence of the circular (CLAUDE.md §10).
    expect(screen.getAllByText(QUOTE).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("UDISE+ వివరాలు 15 అక్టోబర్ లోగా సమర్పించండి.")).toHaveAttribute(
      "lang",
      "te",
    );
    const form = screen
      .getByRole("button", { name: en.circulars.suggestion.confirm })
      .closest("form");
    expect(form).not.toBeNull();
    await userEvent.click(
      within(form as HTMLElement).getByRole("button", { name: en.circulars.suggestion.confirm }),
    );
    // An owner is required before anything is sent.
    expect(await screen.findByText(en.validation.required)).toBeVisible();
    expect(
      stub.callsTo(`POST /bff/api/v1/circular-suggestions/${SUGGESTION}/confirm`),
    ).toHaveLength(0);
    await userEvent.selectOptions(screen.getByLabelText(en.circulars.suggestion.owner), OWNER);
    await userEvent.click(screen.getByRole("button", { name: en.circulars.suggestion.confirm }));
    await waitFor(() =>
      expect(
        stub.callsTo(`POST /bff/api/v1/circular-suggestions/${SUGGESTION}/confirm`),
      ).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/circular-suggestions/${SUGGESTION}/confirm`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"1"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      owner_membership_id: OWNER,
      title: "Submit the UDISE+ sheets",
      due_on: "2026-10-15",
    });
  });

  it("explains manual review and offers a retry", async () => {
    signedIn(["document.read", "circular.review"]);
    stub.routes[`GET /bff/api/v1/circulars/${DOC}`] = () =>
      Response.json({
        ...detail({
          status: "needs_review",
          error_code: "ai_budget_exhausted",
          can_retry: true,
          suggestions: [],
        }),
        reading_status: "needs_review",
      });
    stub.routes[`POST /bff/api/v1/circulars/${DOC}/read`] = () =>
      problem(409, "reading_in_progress");
    renderWithIntl(<CircularDetailScreen documentId={DOC} />);
    expect(await screen.findByText(en.circulars.reason.ai_budget_exhausted)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: en.circulars.tryAgain }));
    expect(await screen.findByText(en.circulars.errors.reading_in_progress.title)).toBeVisible();
  });

  it("drafts a parent notice from a C1 circular and opens it", async () => {
    signedIn(["document.read", "notice.draft"]);
    stub.routes[`GET /bff/api/v1/circulars/${DOC}`] = () => Response.json(detail());
    stub.routes["POST /bff/api/v1/notices"] = () =>
      Response.json(notice(drafting()), {
        status: 202,
        headers: { Location: `/api/v1/notices/${NOTICE}` },
      });
    renderWithIntl(<CircularDetailScreen documentId={DOC} />);
    await userEvent.click(await screen.findByRole("button", { name: en.circulars.draftNotice }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/notices/${NOTICE}`));
    const call = stub.callsTo("POST /bff/api/v1/notices")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ source: "circular", document_id: DOC });
    expect(call?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
  });
});

describe("tasks (US-1603, US-1604)", () => {
  it("shows my tasks with overdue in words and marks one done with If-Match", async () => {
    signedIn(["task.read"]);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task()]);
    stub.routes[`POST /bff/api/v1/tasks/${TASK}/status`] = () =>
      Response.json(task({ status: "done" }));
    renderWithIntl(<TasksScreen />);
    const done = await screen.findByRole("button", {
      name: `${en.tasks.move.done}: ${task().title}`,
    });
    expect(within(done.closest("tr") as HTMLElement).getByText(en.tasks.due.overdue)).toBeVisible();
    // No school view and no cancel without task.read_all / task.manage.
    expect(screen.queryByRole("radio", { name: en.tasks.view.all })).toBeNull();
    expect(screen.queryByRole("button", { name: new RegExp(en.tasks.move.cancelled) })).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: `${en.tasks.move.done}: ${task().title}` }),
    );
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/tasks/${TASK}/status`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/tasks/${TASK}/status`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"3"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ status: "done" });
    expect(stub.callsTo("GET /bff/api/v1/tasks")[0]?.url.searchParams.get("view")).toBe("mine");
  });

  it("managers see the whole school and can add a task", async () => {
    signedIn(["task.read", "task.read_all", "task.manage"]);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([]);
    stub.routes["POST /bff/api/v1/tasks"] = () => Response.json(task(), { status: 201 });
    renderWithIntl(<TasksScreen />);
    await userEvent.click(await screen.findByRole("radio", { name: en.tasks.view.all }));
    await waitFor(() =>
      expect(
        stub.callsTo("GET /bff/api/v1/tasks").some((c) => c.url.searchParams.get("view") === "all"),
      ).toBe(true),
    );
    await userEvent.type(screen.getByLabelText(en.tasks.new.taskTitle), "Send the fee report");
    await userEvent.selectOptions(await screen.findByLabelText(en.tasks.new.owner), OWNER);
    await userEvent.type(screen.getByLabelText(en.tasks.new.dueOn), "2026-12-01");
    await userEvent.click(screen.getByRole("button", { name: en.tasks.new.submit }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/tasks")).toHaveLength(1));
    expect(JSON.parse(stub.callsTo("POST /bff/api/v1/tasks")[0]?.body ?? "{}")).toEqual({
      title: "Send the fee report",
      owner_membership_id: OWNER,
      due_on: "2026-12-01",
    });
  });

  it("dueState says overdue, today, soon and later", () => {
    const today = "2026-10-10";
    expect(dueState({ due_on: "2026-10-09", status: "open" }, today)).toBe("overdue");
    expect(dueState({ due_on: "2026-10-10", status: "open" }, today)).toBe("today");
    expect(dueState({ due_on: "2026-10-12", status: "in_progress" }, today)).toBe("soon");
    expect(dueState({ due_on: "2026-10-20", status: "open" }, today)).toBe("later");
    expect(dueState({ due_on: "2026-10-01", status: "done" }, today)).toBe("closed");
    expect(todayIst(new Date("2026-10-09T19:00:00Z"))).toBe("2026-10-10");
  });
});

describe("parent notices (US-1605, US-1606)", () => {
  it("refuses personal numbers before drafting from staff text", async () => {
    signedIn(["notice.draft"]);
    stub.routes["GET /bff/api/v1/notices"] = () => page([]);
    renderWithIntl(<NoticesScreen />);
    await userEvent.type(await screen.findByLabelText(en.notices.new.textLabel), "Call 9876543210");
    expect(screen.getByText(en.notices.new.personal)).toBeVisible();
    expect(screen.getByRole("button", { name: en.notices.new.draftWithAi })).toBeDisabled();
    expect(looksPersonal("Sports day on 14/11/2026 at 9:00")).toBe(false);
    expect(looksPersonal("mail office@example.org")).toBe(true);
  });

  it("starts an AI notice (202) and opens it while it is drafted in the background", async () => {
    signedIn(["notice.draft"]);
    stub.routes["GET /bff/api/v1/notices"] = () => page([]);
    stub.routes["POST /bff/api/v1/notices"] = () =>
      Response.json(notice(drafting({ source: "staff_text", document_id: null })), {
        status: 202,
        headers: { Location: `/api/v1/notices/${NOTICE}` },
      });
    renderWithIntl(<NoticesScreen />);
    await userEvent.type(
      await screen.findByLabelText(en.notices.new.textLabel),
      "Sports day on 14/11/2026 at 9:00",
    );
    await userEvent.click(screen.getByRole("button", { name: en.notices.new.draftWithAi }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/notices/${NOTICE}`));
    const call = stub.callsTo("POST /bff/api/v1/notices")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({
      source: "staff_text",
      text: "Sports day on 14/11/2026 at 9:00",
    });
    expect(call?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("shows progress while the AI drafts, asks again with backoff, then opens the draft", async () => {
    signedIn(["notice.draft"]);
    let asked = 0;
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () => {
      asked += 1;
      return Response.json(asked < 3 ? notice(drafting()) : notice({ version: 2 }));
    };
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    expect(await screen.findByText(en.notices.drafting.title)).toBeVisible();
    expect(screen.getByText(en.notices.drafting.body)).toBeVisible();
    expect(screen.getAllByText(en.notices.status.drafting).length).toBeGreaterThan(0);
    // Nothing to edit or approve while drafting.
    expect(screen.queryByLabelText(en.notices.editor.title)).toBeNull();
    expect(await screen.findByText(en.notices.aiDrafted)).toBeVisible();
    expect(screen.getAllByLabelText(en.notices.editor.title)[0]).toHaveValue("Sports day");
    expect(asked).toBe(3);
    // Done: no more asking.
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(asked).toBe(3);
  });

  it("says when drafting takes longer than usual", async () => {
    signedIn(["notice.draft"]);
    const longAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () =>
      Response.json(notice(drafting({ updated_at: longAgo })));
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    expect(await screen.findByText(en.notices.drafting.slow)).toBeVisible();
  });

  it("a failed AI draft says why and can be tried again with If-Match", async () => {
    signedIn(["notice.draft"]);
    let current = notice(
      drafting({ status: "draft_failed", draft_error: "ai_budget_exhausted", version: 2 }),
    );
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () => Response.json(current);
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/draft`] = () => {
      current = notice(drafting({ version: 3 }));
      return Response.json(current, { status: 202 });
    };
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    expect(await screen.findByText(en.notices.draftFailed.title)).toBeVisible();
    expect(screen.getByText(en.notices.draftError.ai_budget_exhausted)).toBeVisible();
    // Writing it by hand stays possible.
    expect(screen.getAllByLabelText(en.notices.editor.title)[0]).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: en.notices.draftFailed.retry }));
    expect(await screen.findByText(en.notices.drafting.title)).toBeVisible();
    const call = stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/draft`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"2"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({});
  });

  it("explains a refused retry", async () => {
    signedIn(["notice.draft"]);
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () =>
      Response.json(notice(drafting({ status: "draft_failed", draft_error: "worker_error" })));
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/draft`] = () =>
      problem(409, "notice_not_draft_failed");
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    expect(await screen.findByText(en.notices.draftError.other)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: en.notices.draftFailed.retry }));
    expect(await screen.findByText(en.notices.errors.notice_not_draft_failed.title)).toBeVisible();
  });

  it("marks an AI draft, saves the edit, then approves with the new version", async () => {
    signedIn(["notice.draft", "notice.approve"]);
    let current = notice();
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () => Response.json(current);
    stub.routes[`PATCH /bff/api/v1/notices/${NOTICE}`] = () => {
      current = notice({ title_en: "Sports day 2026", version: 2 });
      return Response.json(current);
    };
    stub.routes[`POST /bff/api/v1/notices/${NOTICE}/approve`] = () => {
      current = notice({
        title_en: "Sports day 2026",
        status: "approved",
        render_status: "queued",
        version: 3,
      });
      return Response.json(current);
    };
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    expect(await screen.findByText(en.notices.aiDrafted)).toBeVisible();
    const title = screen.getAllByLabelText(en.notices.editor.title)[0] as HTMLInputElement;
    await userEvent.clear(title);
    await userEvent.type(title, "Sports day 2026");
    await userEvent.click(screen.getByRole("button", { name: en.notices.editor.approve }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/approve`)).toHaveLength(1),
    );
    expect(stub.callsTo(`PATCH /bff/api/v1/notices/${NOTICE}`)[0]?.headers.get("If-Match")).toBe(
      'W/"1"',
    );
    expect(
      stub.callsTo(`POST /bff/api/v1/notices/${NOTICE}/approve`)[0]?.headers.get("If-Match"),
    ).toBe('W/"2"');
  });

  it("an approved notice copies both languages and downloads the PDF", async () => {
    signedIn(["notice.draft"]);
    const approved = notice({ status: "approved", files_available: true, render_status: "ready" });
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}`] = () => Response.json(approved);
    stub.routes[`GET /bff/api/v1/notices/${NOTICE}/download-url`] = () =>
      Response.json({
        url: SIGNED,
        expires_at: "2026-10-02T04:35:00Z",
        filename: "n.pdf",
        mime_type: "application/pdf",
      });
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderWithIntl(<NoticeDetailScreen noticeId={NOTICE} />);
    await userEvent.click(await screen.findByRole("button", { name: en.notices.approved.copy }));
    expect(writeText).toHaveBeenCalledWith(noticeText(approved));
    expect(noticeText(approved)).toContain("క్రీడా దినోత్సవం");
    expect(await screen.findByText(en.notices.approved.copied)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: en.notices.approved.downloadPdf }));
    await waitFor(() => expect(opened).toEqual([SIGNED]));
    expect(document.body.textContent).not.toContain("X-Amz-Signature");
  });
});

describe("messages and links", () => {
  it("every circulars, tasks and notices message exists in Telugu", () => {
    const keys = (value: unknown, prefix = ""): string[] =>
      value && typeof value === "object"
        ? Object.entries(value).flatMap(([k, v]) => keys(v, `${prefix}${k}.`))
        : [prefix.slice(0, -1)];
    for (const ns of ["circulars", "tasks", "notices"] as const) {
      expect(keys(messages.te[ns]).sort()).toEqual(keys(messages.en[ns]).sort());
    }
  });

  it("notifications open the circular or the tasks page", () => {
    expect(notificationHref({ resource_type: "circular", resource_id: DOC })).toBe(
      `/circulars/${DOC}`,
    );
    expect(notificationHref({ resource_type: "task", resource_id: TASK })).toBe("/tasks");
  });
});
