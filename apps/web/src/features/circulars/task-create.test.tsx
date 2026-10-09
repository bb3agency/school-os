import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setSchoolDateFormat, type DateFormat } from "@/lib/date-format";
import { installBffStub, page, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import type { Task } from "./data";
import { TasksScreen } from "./TasksScreen";

/**
 * Adding a task by hand (US-1603, FR-TASK-001; POST /tasks, `task.manage`): the due date is
 * typed in the school's date format, like the edit dialog, with a hint, a real-date check
 * and the API's ISO date in the request. Synthetic data only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/tasks",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en;
const n = en.tasks.new;
const OWNER = "0192f3a4-0000-7000-8000-0000000000b7";
const POST = "POST /bff/api/v1/tasks";
const MANAGER = ["task.read", "task.read_all", "task.manage"];

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "0192f3a4-0000-7000-8000-00000000f001",
    title: "Send the fee report",
    details: null,
    owner: { membership_id: OWNER, display_name: "Synthetic Clerk" },
    due_on: "2026-12-01",
    status: "open",
    overdue: false,
    source: "manual",
    document_id: null,
    citation: null,
    created_by: null,
    created_at: "2026-10-01T04:30:00Z",
    completed_at: null,
    cancelled_at: null,
    version: 1,
    ...overrides,
  };
}

let stub: BffStub;

function signedIn(dateFormat: DateFormat = "DD/MM/YYYY") {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(
      me(MANAGER, {
        settings: { idle_timeout_minutes: 15, date_format: dateFormat, languages: ["en"] },
      }),
    );
}

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/tasks"] = () => page([]);
  stub.routes[POST] = () => Response.json(task(), { status: 201 });
  stub.routes["GET /bff/api/v1/task-assignees"] = () =>
    Response.json([
      { membership_id: OWNER, display_name: "Synthetic Clerk", roles: ["office_staff"] },
    ]);
});
afterEach(() => {
  uninstallBffStub();
  setSchoolDateFormat(null);
  expect(intlErrors).toEqual([]);
});

async function fill(due: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(n.taskTitle), "Send the fee report");
  await user.selectOptions(await screen.findByLabelText(n.owner), OWNER);
  const field = screen.getByLabelText(n.dueOn);
  await user.type(field, due);
  await user.click(screen.getByRole("button", { name: n.submit }));
  return { user, field };
}

describe("add a task (US-1603, FR-TASK-001)", () => {
  it("takes the due date in the school's format and sends the API's ISO date", async () => {
    signedIn();
    renderWithIntl(<TasksScreen />);
    await waitFor(() =>
      expect(screen.getByLabelText(n.dueOn)).toHaveAttribute("placeholder", "DD/MM/YYYY"),
    );
    const field = screen.getByLabelText(n.dueOn);
    // A text field with the school's format, not the browser's own date picker.
    expect(field).not.toHaveAttribute("type", "date");
    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveAccessibleDescription(
      /Use DD\/MM\/YYYY, for example \d{2}\/\d{2}\/\d{4}\./,
    );
    await fill("01/12/2026");
    await waitFor(() => expect(stub.callsTo(POST)).toHaveLength(1));
    expect(JSON.parse(stub.callsTo(POST)[0]?.body ?? "{}")).toEqual({
      title: "Send the fee report",
      owner_membership_id: OWNER,
      due_on: "2026-12-01",
    });
    expect(await screen.findByText(n.saved)).toBeVisible();
  });

  it("follows the school's own format (DD-MM-YYYY)", async () => {
    signedIn("DD-MM-YYYY");
    renderWithIntl(<TasksScreen />);
    await waitFor(() =>
      expect(screen.getByLabelText(n.dueOn)).toHaveAttribute("placeholder", "DD-MM-YYYY"),
    );
    await fill("05-01-2027");
    await waitFor(() => expect(stub.callsTo(POST)).toHaveLength(1));
    expect(JSON.parse(stub.callsTo(POST)[0]?.body ?? "{}")).toMatchObject({ due_on: "2027-01-05" });
  });

  it("refuses a date that is not real with the plain message, before sending", async () => {
    signedIn();
    renderWithIntl(<TasksScreen />);
    await waitFor(() =>
      expect(screen.getByLabelText(n.dueOn)).toHaveAttribute("placeholder", "DD/MM/YYYY"),
    );
    await fill("31/02/2026");
    expect(
      await screen.findByText(
        /^Enter a real date as DD\/MM\/YYYY, for example \d{2}\/\d{2}\/\d{4}\.$/,
      ),
    ).toBeVisible();
    expect(stub.callsTo(POST)).toHaveLength(0);
  });

  it("refuses an empty due date with the same plain message", async () => {
    signedIn();
    renderWithIntl(<TasksScreen />);
    await waitFor(() =>
      expect(screen.getByLabelText(n.dueOn)).toHaveAttribute("placeholder", "DD/MM/YYYY"),
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(n.taskTitle), "Send the fee report");
    await user.selectOptions(await screen.findByLabelText(n.owner), OWNER);
    await user.click(screen.getByRole("button", { name: n.submit }));
    expect(await screen.findByText(/^Enter a real date as DD\/MM\/YYYY/)).toBeVisible();
    expect(stub.callsTo(POST)).toHaveLength(0);
  });
});
