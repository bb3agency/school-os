import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setSchoolDateFormat, type DateFormat } from "@/lib/date-format";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import type { Task } from "./data";
import { TasksScreen } from "./TasksScreen";

/**
 * Editing a task (US-1603, FR-TASK-003; PATCH /tasks/{id}): `task.manage` only, open and in
 * progress tasks only, only the changed fields with If-Match, the due date typed in the
 * school's date format, plain-language errors. Synthetic data only.
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
const e = en.tasks.edit;
const TASK = "0192f3a4-0000-7000-8000-00000000f001";
const TASK2 = "0192f3a4-0000-7000-8000-00000000f002";
const OWNER = "0192f3a4-0000-7000-8000-0000000000b7";
const OWNER2 = "0192f3a4-0000-7000-8000-0000000000b8";
const PATCH = `PATCH /bff/api/v1/tasks/${TASK}`;

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: TASK,
    title: "Submit the UDISE+ sheets",
    details: null,
    owner: { membership_id: OWNER, display_name: "Synthetic Clerk" },
    due_on: "2026-10-15",
    status: "open",
    overdue: false,
    source: "manual",
    document_id: null,
    citation: null,
    created_by: null,
    created_at: "2026-10-01T04:30:00Z",
    completed_at: null,
    cancelled_at: null,
    version: 3,
    ...overrides,
  };
}

let stub: BffStub;

function signedIn(permissions: string[], dateFormat: DateFormat = "DD/MM/YYYY") {
  stub.routes["GET /bff/api/v1/me"] = () =>
    Response.json(
      me(permissions, {
        settings: { idle_timeout_minutes: 15, date_format: dateFormat, languages: ["en"] },
      }),
    );
}

const MANAGER = ["task.read", "task.read_all", "task.manage"];

beforeEach(() => {
  stub = installBffStub("staff");
  stub.routes["GET /bff/api/v1/task-assignees"] = () =>
    Response.json([
      { membership_id: OWNER, display_name: "Synthetic Clerk", roles: ["office_staff"] },
      { membership_id: OWNER2, display_name: "Synthetic Teacher", roles: ["teacher"] },
    ]);
});
afterEach(() => {
  uninstallBffStub();
  setSchoolDateFormat(null);
  expect(intlErrors).toEqual([]);
});

async function openEdit(title = task().title) {
  const user = userEvent.setup();
  const edit = await screen.findByRole("button", { name: e.action, description: title });
  await user.click(edit);
  const dialog = await screen.findByRole("dialog", { name: e.title });
  return { user, edit, dialog: within(dialog), element: dialog };
}

function bodyOf(key: string, index = 0) {
  return JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;
}

describe("edit a task (US-1603, FR-TASK-003)", () => {
  it("sends only the changed fields with If-Match; the due date is typed in the school's format", async () => {
    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task()]);
    stub.routes[PATCH] = () => Response.json(task({ version: 4 }));
    renderWithIntl(<TasksScreen />);
    const { user, edit, dialog, element } = await openEdit();
    expect(dialog.getByLabelText(e.taskTitle)).toHaveValue("Submit the UDISE+ sheets");
    expect(dialog.getByLabelText(e.dueOn)).toHaveValue("15/10/2026");
    await waitFor(() => expect(dialog.getByLabelText(e.owner)).toHaveValue(OWNER));
    expect(dialog.getByLabelText(e.details)).toHaveValue("");

    await user.clear(dialog.getByLabelText(e.taskTitle));
    await user.type(dialog.getByLabelText(e.taskTitle), "Submit the UDISE+ sheets (signed)");
    await user.selectOptions(dialog.getByLabelText(e.owner), OWNER2);
    await user.clear(dialog.getByLabelText(e.dueOn));
    await user.type(dialog.getByLabelText(e.dueOn), "20/10/2026");
    await user.click(dialog.getByRole("button", { name: e.save }));

    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(1));
    expect(stub.callsTo(PATCH)[0]?.headers.get("If-Match")).toBe('W/"3"');
    expect(bodyOf(PATCH)).toEqual({
      title: "Submit the UDISE+ sheets (signed)",
      owner_membership_id: OWNER2,
      due_on: "2026-10-20",
    });
    await waitFor(() => expect(element).not.toHaveAttribute("open"));
    expect(edit).toHaveFocus();
  });

  it("clearing the details sends them empty; no change sends nothing", async () => {
    signedIn(MANAGER, "YYYY-MM-DD");
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task({ details: "Two copies" })]);
    stub.routes[PATCH] = () => Response.json(task({ version: 4 }));
    renderWithIntl(<TasksScreen />);
    let opened = await openEdit();
    expect(opened.dialog.getByLabelText(e.dueOn)).toHaveValue("2026-10-15");
    await opened.user.click(opened.dialog.getByRole("button", { name: e.save }));
    await waitFor(() => expect(opened.element).not.toHaveAttribute("open"));
    expect(stub.callsTo(PATCH)).toHaveLength(0);

    opened = await openEdit();
    await opened.user.clear(opened.dialog.getByLabelText(e.details));
    await opened.user.click(opened.dialog.getByRole("button", { name: e.save }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(1));
    expect(bodyOf(PATCH)).toEqual({ details: "" });
  });

  it("refuses a date that is not real, and an empty title, before sending", async () => {
    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task()]);
    renderWithIntl(<TasksScreen />);
    const { user, dialog } = await openEdit();
    await user.clear(dialog.getByLabelText(e.taskTitle));
    await user.clear(dialog.getByLabelText(e.dueOn));
    await user.type(dialog.getByLabelText(e.dueOn), "31/02/2026");
    await user.click(dialog.getByRole("button", { name: e.save }));
    expect(
      await dialog.findByText("Enter a real date as DD/MM/YYYY, for example 15/10/2026."),
    ).toBeInTheDocument();
    expect(dialog.getByText(en.validation.required)).toBeInTheDocument();
    expect(dialog.getByLabelText(e.taskTitle)).toHaveAttribute("maxLength", "200");
    expect(stub.callsTo(PATCH)).toHaveLength(0);
  });

  it("no Edit without task.manage, and none on done or cancelled tasks", async () => {
    signedIn(["task.read"]);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task()]);
    const first = renderWithIntl(<TasksScreen />);
    expect(
      await screen.findByRole("button", { name: `${en.tasks.move.done}: ${task().title}` }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: e.action })).toBeNull();
    first.unmount();

    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/tasks"] = () =>
      page([
        task({ status: "in_progress" }),
        task({ id: TASK2, title: "Old task", status: "done" }),
        task({ id: "0192f3a4-0000-7000-8000-00000000f003", title: "Gone", status: "cancelled" }),
      ]);
    renderWithIntl(<TasksScreen />);
    expect(
      await screen.findByRole("button", { name: e.action, description: task().title }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: e.action })).toHaveLength(1);
  });

  it("someone else's change (412) says so and reloads the list", async () => {
    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task()]);
    stub.routes[PATCH] = () => problem(412, "version_mismatch");
    renderWithIntl(<TasksScreen />);
    const { user, dialog } = await openEdit();
    const before = stub.callsTo("GET /bff/api/v1/tasks").length;
    await user.clear(dialog.getByLabelText(e.taskTitle));
    await user.type(dialog.getByLabelText(e.taskTitle), "New title");
    await user.click(dialog.getByRole("button", { name: e.save }));
    expect(await dialog.findByText(en.tasks.errors.version_mismatch.title)).toBeInTheDocument();
    await waitFor(() =>
      expect(stub.callsTo("GET /bff/api/v1/tasks").length).toBeGreaterThan(before),
    );
  });

  it("a closed task (409), a refusal (403) and an inactive owner (422) are explained", async () => {
    signedIn(MANAGER);
    stub.routes["GET /bff/api/v1/tasks"] = () => page([task()]);
    stub.routes[PATCH] = () => problem(409, "task_closed");
    renderWithIntl(<TasksScreen />);
    const { user, dialog } = await openEdit();
    await user.selectOptions(await dialog.findByLabelText(e.owner), OWNER2);
    await user.click(dialog.getByRole("button", { name: e.save }));
    expect(await dialog.findByText(en.tasks.errors.task_closed.title)).toBeInTheDocument();

    stub.routes[PATCH] = () => problem(403, "forbidden");
    await user.click(dialog.getByRole("button", { name: e.save }));
    expect(await dialog.findByText(en.errors.api.forbidden.title)).toBeInTheDocument();

    stub.routes[PATCH] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "owner_membership_id",
            code: "owner_not_active",
            message_key: "errors.owner_not_active",
          },
        ],
      });
    await user.click(dialog.getByRole("button", { name: e.save }));
    expect(await dialog.findByText(en.errors.field.owner_not_active)).toBeInTheDocument();
    expect(dialog.getByLabelText(e.owner)).toHaveAttribute("aria-invalid", "true");
  });
});
