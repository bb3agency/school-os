"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
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

/** The moves a row offers: start, done, reopen; cancel only for task.manage holders. */
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

function RowActions({ task, manager }: { task: Task; manager: boolean }) {
  const t = useTranslations("tasks");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);

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
            variant={status === "done" ? "primary" : "secondary"}
            disabled={pending}
            aria-disabled={pending || undefined}
            onClick={() => void move(status)}
            aria-label={`${t(`move.${status}`)}: ${task.title}`}
          >
            {t(`move.${status}`)}
          </Button>
        ))}
      </div>
      <ApiErrorAlert error={error} namespace="tasks" />
    </div>
  );
}

/** Add a task by hand (task.manage): title, owner, due date, details. */
function NewTaskCard() {
  const t = useTranslations("tasks.new");
  const tv = useTranslations("validation");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const assignees = useAssignees(true);
  const [errors, setErrors] = useState<Record<string, string>>({});
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

  return (
    <Card title={t("title")} description={t("description")}>
      <form onSubmit={(event) => void submit(event)} noValidate className="space-y-3">
        <div className="grid gap-3 md:grid-cols-3">
          <TextField
            name="title"
            label={t("taskTitle")}
            maxLength={200}
            error={fieldError("title")}
          />
          <SelectField
            name="owner_membership_id"
            label={t("owner")}
            placeholder={t("chooseOwner")}
            defaultValue=""
            error={fieldError("owner_membership_id")}
            options={options.map((a) => ({ value: a.membership_id, label: a.display_name }))}
          />
          <TextField name="due_on" type="date" label={t("dueOn")} error={fieldError("due_on")} />
        </div>
        <TextAreaField
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
  );
}

/**
 * Tasks (US-1603, US-1604): "My tasks" for everyone with `task.read`; the whole school's tasks
 * for `task.read_all`. Soonest due first, overdue marked in words; owners mark tasks in progress
 * or done; `task.manage` holders add, and cancel tasks. Reminders come in the bell.
 */
export function TasksScreen() {
  const t = useTranslations("tasks");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(TASK_READ);
  const manager = can(TASK_MANAGE);
  const [filters, setFilters] = useState<TaskFilters>({
    view: "mine",
    status: "active",
    due: "any",
  });
  const list = useTasks(filters, allowed);
  const today = todayIst();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <>
          <Card title={t("listTitle")}>
            <div className="mb-4 flex flex-wrap items-end gap-4" data-print="hide">
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
            <LoadGate data={meLoaded ? list : { status: "loading" }}>
              {(page) =>
                page.data.length === 0 ? (
                  <EmptyState icon="calendar" title={t("emptyTitle")} body={t("emptyBody")} />
                ) : (
                  <TableScroll label={t("listTitle")}>
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
                        {page.data.map((task) => (
                          <Tr key={task.id}>
                            <Td>
                              <p className="font-semibold break-words text-ink">{task.title}</p>
                              {task.details ? (
                                <p className="text-sm break-words text-ink-muted">{task.details}</p>
                              ) : null}
                              {task.document_id && task.citation ? (
                                <Link
                                  href={`/circulars/${task.document_id}`}
                                  className="text-xs text-primary underline underline-offset-4"
                                >
                                  {t("fromCircular")}
                                </Link>
                              ) : null}
                            </Td>
                            <Td>
                              <span className="flex flex-wrap items-center gap-1">
                                <Value>{formatDate(task.due_on)}</Value>
                                <DuePill state={dueState(task, today)} />
                              </span>
                            </Td>
                            <Td>{task.owner.display_name ?? t("formerStaff")}</Td>
                            <Td>
                              <TaskStatusPill status={task.status} />
                            </Td>
                            <Td>
                              <RowActions task={task} manager={manager} />
                            </Td>
                          </Tr>
                        ))}
                      </TBody>
                    </Table>
                  </TableScroll>
                )
              }
            </LoadGate>
          </Card>
          {manager ? <NewTaskCard /> : null}
        </>
      )}
    </div>
  );
}
