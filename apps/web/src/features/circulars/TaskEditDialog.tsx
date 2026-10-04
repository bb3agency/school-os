"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import type { FieldErrors } from "@/lib/forms";
import { KEYS, ifMatch, useAssignees, type Task } from "./data";

/** The edit form (same limits as adding a task); the due date is typed in the school's format. */
export const taskEditSchema = z.object({
  version: z.coerce.number().int().min(1),
  title: z.string().trim().min(1, { error: "required" }).max(200, { error: "tooLong" }),
  owner_membership_id: z.string().uuid({ error: "required" }),
  due_on: z
    .string()
    .trim()
    .refine((value) => typedDateToIso(value) !== null, { error: "invalidDate" })
    .transform((value) => typedDateToIso(value) ?? ""),
  details: z.string().trim().max(2000, { error: "tooLong" }),
});
export type TaskEdit = z.output<typeof taskEditSchema>;

export type TaskChanges = Partial<
  Pick<TaskEdit, "title" | "owner_membership_id" | "due_on" | "details">
>;

/**
 * Only what changed, for PATCH /tasks/{id}. Empty details clear them (the API stores null);
 * an untouched field is left out, so a change someone else made to it is not overwritten.
 */
export function taskChanges(task: Task, edit: TaskEdit): TaskChanges {
  const changes: TaskChanges = {};
  if (edit.title !== task.title) changes.title = edit.title;
  if (edit.owner_membership_id !== task.owner.membership_id) {
    changes.owner_membership_id = edit.owner_membership_id;
  }
  if (edit.due_on !== task.due_on) changes.due_on = edit.due_on;
  if (edit.details !== (task.details ?? "")) changes.details = edit.details;
  return changes;
}

/** Tasks the API lets `task.manage` holders change: open and in progress (409 otherwise). */
export function canEditTask(task: Task): boolean {
  return task.status === "open" || task.status === "in_progress";
}

/**
 * The dialog's fields, mounted when it opens: the version and starting values are the ones
 * the user saw then, even if the list refreshes behind the dialog.
 */
function EditFields({ task, errors }: { task: Task; errors: FieldErrors }) {
  const t = useTranslations("tasks");
  const e = useTranslations("tasks.edit");
  const dates = useDateInput();
  const assignees = useAssignees(true);
  const [opened] = useState(task);
  const owner = opened.owner;
  const active = assignees.status === "ready" ? assignees.data : [];
  const options = active.map((a) => ({ value: a.membership_id, label: a.display_name }));
  if (!options.some((option) => option.value === owner.membership_id)) {
    options.unshift({
      value: owner.membership_id,
      label: owner.display_name ? e("formerOwner", { name: owner.display_name }) : t("formerStaff"),
    });
  }
  const hint = dates.hint(opened.due_on);

  return (
    <>
      <input type="hidden" name="version" value={opened.version} readOnly />
      <TextField
        name="title"
        label={e("taskTitle")}
        maxLength={200}
        autoComplete="off"
        defaultValue={opened.title}
        error={errors.title}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          name="owner_membership_id"
          label={e("owner")}
          defaultValue={owner.membership_id}
          error={errors.owner_membership_id}
          options={options}
        />
        <TextField
          name="due_on"
          label={e("dueOn")}
          hint={e("dueHint", hint)}
          inputMode="numeric"
          autoComplete="off"
          placeholder={dates.placeholder}
          defaultValue={dates.fromIso(opened.due_on)}
          error={errors.due_on ? e("dueInvalid", hint) : undefined}
        />
      </div>
      <TextAreaField
        name="details"
        label={e("details")}
        rows={3}
        maxLength={2000}
        defaultValue={opened.details ?? ""}
        error={errors.details}
      />
    </>
  );
}

/**
 * Edit an open or in-progress task (US-1604; PATCH /tasks/{id}, `task.manage`, If-Match):
 * title, owner, due date and details. Only changed fields are sent; nothing changed sends
 * nothing. Someone else's change (412) or a task closed meanwhile (409) reloads the list.
 */
export function TaskEditDialog({ task, onSaved }: { task: Task; onSaved?: () => void }) {
  const e = useTranslations("tasks.edit");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();

  return (
    <ActionDialog
      triggerLabel={e("action")}
      triggerSize="sm"
      triggerVariant="secondary"
      triggerDescription={task.title}
      title={e("title")}
      description={e("description")}
      confirmLabel={e("save")}
      schema={taskEditSchema}
      errorNamespace="tasks"
      invalidate={[KEYS.tasks]}
      onSuccess={(saved) => {
        if (saved !== task) onSaved?.();
      }}
      submit={async (edit) => {
        const body = taskChanges(task, edit);
        if (Object.keys(body).length === 0) return task;
        try {
          return await unwrap(
            api.PATCH("/api/v1/tasks/{task_id}", {
              params: { path: { task_id: task.id } },
              headers: { "If-Match": ifMatch(edit.version) },
              body,
            }),
          );
        } catch (failure) {
          if (failure instanceof ApiError && (failure.status === 409 || failure.status === 412)) {
            void queryClient.invalidateQueries({ queryKey: KEYS.tasks });
          }
          throw failure;
        }
      }}
    >
      {(errors) => <EditFields task={task} errors={errors} />}
    </ActionDialog>
  );
}
