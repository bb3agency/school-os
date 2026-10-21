"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { FilterChips, type FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { NarrowSwitch } from "@/components/ui/NarrowSwitch";
import {
  StackedRows,
  Table,
  TableScroll,
  TBody,
  Td,
  Th,
  THead,
  Tr,
  type Column,
} from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { useDateInput } from "@/lib/date-format";
import { formatDate } from "@/lib/format";
import { zodErrorKeys } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  KEYS,
  TASK_ALL,
  TASK_MANAGE,
  TASK_READ,
  dueState,
  ifMatch,
  taskSchema,
  todayIst,
  useAssignees,
  useTasks,
  type Task,
  type TaskFilters,
  type TaskStatus,
} from "./data";
import { DuePill, LoadGate, TaskStatusPill } from "./parts";
import { canEditTask, TaskEditDialog } from "./TaskEditDialog";
import { LinkPortalEventDialog } from "@/features/portal-calendar/LinkPortalEventDialog";

const CALENDAR_MANAGE = "readiness.calendar.manage";

/** The inbox groups, in the order they are shown (research report §8.4: grouped by due date). */
export const TASK_GROUPS = ["overdue", "today", "week", "later", "earlier", "noDate"] as const;
export type TaskGroup = (typeof TASK_GROUPS)[number];

const DEFAULT_FILTERS: TaskFilters = { view: "mine", status: "active", due: "any" };

/**
 * Which inbox group a task belongs to: overdue (still open and past its date), due today,
 * this week (within 7 days), later; done or cancelled tasks from before today are "earlier".
 * The API sorts soonest due first; grouping keeps that order inside each group.
 */
export function taskGroup(task: Pick<Task, "due_on" | "status">, today: string): TaskGroup {
  if (!task.due_on) return "noDate";
  if (task.due_on < today) return dueState(task, today) === "closed" ? "earlier" : "overdue";
  if (task.due_on === today) return "today";
  const days = (Date.parse(task.due_on) - Date.parse(today)) / 86_400_000;
  return days <= 7 ? "week" : "later";
}

/** The page's tasks split into the inbox groups (empty groups left out). */
export function groupTasks(
  tasks: readonly Task[],
  today: string,
): { group: TaskGroup; tasks: Task[] }[] {
  const byGroup = new Map<TaskGroup, Task[]>();
  for (const task of tasks) {
    const group = taskGroup(task, today);
    byGroup.set(group, [...(byGroup.get(group) ?? []), task]);
  }
  return TASK_GROUPS.flatMap((group) => {
    const rows = byGroup.get(group);
    return rows ? [{ group, tasks: rows }] : [];
  });
}

/**
 * The moves a row offers: start, done, reopen; cancel only for task.manage holders, who may
 * also edit open and in-progress tasks.
 */
function nextMoves(task: Task, manager: boolean): TaskStatus[] {
  switch (task.status) {
    case "open":
      return manager ? ["in_progress", "done", "cancelled"] : ["in_progress", "done"];
    case "in_progress":
      return manager ? ["done", "open", "cancelled"] : ["done", "open"];
    case "done":
      return ["open"];
    case "cancelled":
      return [];
  }
}

function RowActions({
  task,
  manager,
  onEdited,
}: {
  task: Task;
  manager: boolean;
  onEdited: () => void;
}) {
  const t = useTranslations("tasks");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

  // Quick complete goes through the same status call as before; the row changes only after the
  // API answers (no optimistic update on a record task).
  async function move(status: TaskStatus) {
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.POST("/api/v1/tasks/{task_id}/status", {
          params: { path: { task_id: task.id } },
          headers: { "If-Match": ifMatch(task.version) },
          body: { status },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: KEYS.tasks });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {nextMoves(task, manager).map((status) => (
          <Button
            key={status}
            size="sm"
            variant={status === "cancelled" ? "ghost" : "secondary"}
            disabled={pending}
            aria-disabled={pending || undefined}
            onClick={() => void move(status)}
            aria-label={`${t(`move.${status}`)}: ${task.title}`}
          >
            {status === "done" ? <Icon name="check" className="size-4" /> : null}
            {t(`move.${status}`)}
          </Button>
        ))}
        {manager && canEditTask(task) ? <TaskEditDialog task={task} onSaved={onEdited} /> : null}
        {/* R3-1D (FR-RDY-010): a circular's deadline can confirm a portal window's date. */}
        {manager && task.source === "circular" && canEditTask(task) && can(CALENDAR_MANAGE) ? (
          <LinkPortalEventDialog task={task} />
        ) : null}
      </div>
      <ApiErrorAlert error={error} namespace="tasks" />
    </div>
  );
}

const FIELD_IDS = {
  title: "new-task-title",
  owner_membership_id: "new-task-owner",
  due_on: "new-task-due",
  details: "new-task-details",
} as const;

/**
 * Add a task by hand (task.manage): title, owner, due date, details. The due date is typed in
 * the school's format, as in the edit dialog, and sent as `YYYY-MM-DD`. After a failed submit
 * an error summary at the top of the form links to each field (docs/17 §4.4).
 */
function NewTaskCard() {
  const t = useTranslations("tasks.new");
  const tv = useTranslations("validation");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const assignees = useAssignees(true);
  const dates = useDateInput();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form)) as Record<string, string>;
    const parsed = taskSchema.safeParse(values);
    setSaved(false);
    if (!parsed.success) {
      setErrors(zodErrorKeys(parsed.error));
      setAttempt((value) => value + 1);
      return;
    }
    setErrors({});
    setError(undefined);
    setPending(true);
    try {
      const { details, ...rest } = parsed.data;
      await unwrap(
        api.POST("/api/v1/tasks", {
          headers: { "Idempotency-Key": newIdempotencyKey() },
          body: { ...rest, ...(details ? { details } : {}) },
        }),
      );
      form.reset();
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: KEYS.tasks });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const fieldError = (name: string) =>
    errors[name] ? translateOr(tv, errors[name] ?? "invalid", "invalid") : undefined;
  const options = assignees.status === "ready" ? assignees.data : [];
  const hint = dates.hint(todayIst());
  const dueError = errors.due_on ? t("dueInvalid", hint) : undefined;
  const summary: SummaryError[] = (
    [
      ["title", fieldError("title")],
      ["owner_membership_id", fieldError("owner_membership_id")],
      ["due_on", dueError],
      ["details", fieldError("details")],
    ] as const
  ).flatMap(([name, message]) => (message ? [{ fieldId: FIELD_IDS[name], message }] : []));

  return (
    <div id="new-task" className="scroll-mt-20">
      <Card title={t("title")} description={t("description")}>
        <form onSubmit={(event) => void submit(event)} noValidate className="space-y-3">
          <ErrorSummary errors={summary} attempt={attempt} />
          <div className="grid gap-3 md:grid-cols-3">
            <TextField
              id={FIELD_IDS.title}
              name="title"
              label={t("taskTitle")}
              maxLength={200}
              error={fieldError("title")}
              className="min-w-0"
            />
            <SelectField
              id={FIELD_IDS.owner_membership_id}
              name="owner_membership_id"
              label={t("owner")}
              placeholder={t("chooseOwner")}
              defaultValue=""
              error={fieldError("owner_membership_id")}
              options={options.map((a) => ({ value: a.membership_id, label: a.display_name }))}
              className="min-w-0"
            />
            <TextField
              id={FIELD_IDS.due_on}
              name="due_on"
              label={t("dueOn")}
              hint={t("dueHint", hint)}
              inputMode="numeric"
              autoComplete="off"
              placeholder={dates.placeholder}
              error={dueError}
              className="min-w-0"
            />
          </div>
          <TextAreaField
            id={FIELD_IDS.details}
            name="details"
            label={t("details")}
            rows={2}
            maxLength={2000}
            error={fieldError("details")}
          />
          <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
            {t("submit")}
          </Button>
          {saved ? (
            <Alert tone="success" live>
              {t("saved")}
            </Alert>
          ) : null}
          <ApiErrorAlert error={error} namespace="tasks" />
        </form>
      </Card>
    </div>
  );
}

/**
 * Tasks (US-1603, US-1604): "My tasks" for everyone with `task.read`; the whole school's tasks
 * for `task.read_all`. An inbox grouped by due date (overdue, due today, this week, later),
 * soonest due first, overdue also said in words; owners mark tasks in progress or done;
 * `task.manage` holders add, edit (open and in progress) and cancel tasks. Reminders come in
 * the bell.
 */
export function TasksScreen() {
  const t = useTranslations("tasks");
  const tu = useTranslations("ui");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(TASK_READ);
  const manager = can(TASK_MANAGE);
  const filtersId = useId();
  const [filters, setFilterState] = useState<TaskFilters>(DEFAULT_FILTERS);
  // Cursor paging (docs/09 §2): a page holds 100 tasks; new filters start at the first page.
  const pages = useCursorStack();
  const setFilters = (next: TaskFilters) => {
    pages.reset();
    setFilterState(next);
  };
  const list = useTasks(filters, allowed, pages.cursor);
  const [edited, setEdited] = useState(false);
  const today = todayIst();

  const chips: FilterChip[] = [
    ...(filters.status !== DEFAULT_FILTERS.status
      ? [
          {
            id: "status",
            label: t("statusLabel"),
            value: t(`filter.status.${filters.status}`),
          },
        ]
      : []),
    ...(filters.due !== DEFAULT_FILTERS.due
      ? [{ id: "due", label: t("dueLabel"), value: t(`filter.due.${filters.due}`) }]
      : []),
  ];
  const removeChip = (id: string) =>
    setFilters({
      ...filters,
      ...(id === "status" ? { status: DEFAULT_FILTERS.status } : {}),
      ...(id === "due" ? { due: DEFAULT_FILTERS.due } : {}),
    });
  const clearFilters = () =>
    setFilters({ ...filters, status: DEFAULT_FILTERS.status, due: DEFAULT_FILTERS.due });

  const dueCell = (task: Task) => (
    <span className="flex flex-wrap items-center gap-1">
      <span className="num whitespace-nowrap">
        <Value>{formatDate(task.due_on)}</Value>
      </span>
      <DuePill state={dueState(task, today)} />
    </span>
  );
  const taskCell = (task: Task) => (
    <>
      <p className="font-semibold break-words text-ink">{task.title}</p>
      {task.details ? <p className="text-sm break-words text-ink-muted">{task.details}</p> : null}
      {task.document_id && task.citation ? (
        <Link
          href={`/circulars/${task.document_id}`}
          className="text-xs text-primary underline underline-offset-4"
        >
          {t("fromCircular")}
        </Link>
      ) : null}
    </>
  );
  const actionsCell = (task: Task) => (
    <RowActions task={task} manager={manager} onEdited={() => setEdited(true)} />
  );
  const columns: Column<Task>[] = [
    { key: "task", header: t("columns.task"), cell: taskCell },
    { key: "due", header: t("columns.due"), cell: dueCell },
    {
      key: "owner",
      header: t("columns.owner"),
      cell: (task) => task.owner.display_name ?? t("formerStaff"),
    },
    {
      key: "status",
      header: t("columns.status"),
      cell: (task) => <TaskStatusPill status={task.status} />,
    },
    { key: "actions", header: t("columns.actions"), stack: "actions", cell: actionsCell },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          allowed && manager ? (
            // The add form is on this page, under the inbox: the action jumps to it.
            <a href="#new-task" className={buttonClasses("secondary")}>
              <Icon name="plus" className="size-4" />
              {t("addTask")}
            </a>
          ) : null
        }
      />
      {meLoaded && !allowed ? (
        <EmptyState icon="lock" title={t("noAccessTitle")} body={t("noAccessBody")} />
      ) : (
        <>
          <section
            aria-labelledby={filtersId}
            className="min-w-0 rounded-xl border border-border bg-surface"
            data-print="hide"
          >
            <h2 id={filtersId} className="sr-only">
              {t("filtersLabel")}
            </h2>
            <div className="flex flex-wrap items-end gap-4 px-3 py-3 sm:px-4">
              {can(TASK_ALL) ? (
                <SegmentedControl
                  legend={t("viewLabel")}
                  legendVisible
                  size="sm"
                  value={filters.view}
                  onValueChange={(view) =>
                    setFilters({ ...filters, view: view as TaskFilters["view"] })
                  }
                  options={[
                    { value: "mine", label: t("view.mine") },
                    { value: "all", label: t("view.all") },
                  ]}
                />
              ) : null}
              <SelectField
                label={t("statusLabel")}
                value={filters.status}
                onChange={(event) =>
                  setFilters({ ...filters, status: event.target.value as TaskFilters["status"] })
                }
                options={(["active", "open", "in_progress", "done", "cancelled"] as const).map(
                  (value) => ({
                    value,
                    label: t(`filter.status.${value}`),
                  }),
                )}
              />
              <SelectField
                label={t("dueLabel")}
                value={filters.due}
                onChange={(event) =>
                  setFilters({ ...filters, due: event.target.value as TaskFilters["due"] })
                }
                options={(["any", "overdue", "week", "later"] as const).map((value) => ({
                  value,
                  label: t(`filter.due.${value}`),
                }))}
              />
            </div>
            <FilterChips
              chips={chips}
              onRemove={removeChip}
              onClear={clearFilters}
              className="border-t border-border px-3 py-2 sm:px-4"
            />
          </section>
          {edited ? (
            <Alert tone="success" live>
              {t("edit.updated")}
            </Alert>
          ) : null}
          <LoadGate data={meLoaded ? list : { status: "loading" }}>
            {(page) => (
              <div className="space-y-5">
                {page.data.length === 0 ? (
                  chips.length > 0 ? (
                    <EmptyState
                      icon="search"
                      title={t("noResultsTitle")}
                      body={t("noResultsBody")}
                      action={
                        <Button variant="secondary" size="sm" onClick={clearFilters}>
                          {tu("table.clearFilters")}
                        </Button>
                      }
                    />
                  ) : (
                    <EmptyState icon="calendar" title={t("emptyTitle")} body={t("emptyBody")} />
                  )
                ) : (
                  groupTasks(page.data, today).map(({ group, tasks }) => (
                    <TaskGroupSection key={group} group={group} tasks={tasks} columns={columns} />
                  ))
                )}
                <Pager
                  label={t("pagesLabel")}
                  page={pages.page}
                  onPrevious={pages.hasPrevious ? pages.previous : undefined}
                  onNext={
                    page.next_cursor ? () => pages.next(page.next_cursor as string) : undefined
                  }
                />
              </div>
            )}
          </LoadGate>
          {manager ? <NewTaskCard /> : null}
        </>
      )}
    </div>
  );
}

/** One inbox group: its heading with the count, then the tasks (cards below 640px). */
function TaskGroupSection({
  group,
  tasks,
  columns,
}: {
  group: TaskGroup;
  tasks: readonly Task[];
  columns: readonly Column<Task>[];
}) {
  const t = useTranslations("tasks");
  const headingId = useId();
  const label = t(`groups.${group}`);
  return (
    <section aria-labelledby={headingId} className="min-w-0 space-y-2">
      <h2 id={headingId} className="flex flex-wrap items-baseline gap-2 text-base font-semibold">
        <span className={group === "overdue" ? "text-danger" : "text-ink"}>{label}</span>
        <span className="num text-sm font-normal text-ink-muted">
          {t("groupCount", { count: tasks.length })}
        </span>
      </h2>
      {/* Below 640px the tasks are cards (docs/17 §5.7); the table from there. */}
      <NarrowSwitch
        narrow={
          <StackedRows
            caption={label}
            captionHidden
            rows={tasks}
            rowKey={(task) => task.id}
            columns={columns}
          />
        }
        wide={
          <TableScroll label={label} framed>
            <Table>
              <THead>
                <Tr>
                  <Th>{t("columns.task")}</Th>
                  <Th>{t("columns.due")}</Th>
                  <Th>{t("columns.owner")}</Th>
                  <Th>{t("columns.status")}</Th>
                  <Th>
                    <span className="sr-only">{t("columns.actions")}</span>
                  </Th>
                </Tr>
              </THead>
              <TBody>
                {tasks.map((task) => (
                  <Tr key={task.id}>
                    {columns.map((column) => (
                      <Td key={column.key}>{column.cell(task)}</Td>
                    ))}
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableScroll>
        }
      />
    </section>
  );
}
