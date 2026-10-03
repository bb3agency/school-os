"use client";

import { useTranslations } from "next-intl";
import { useId, useRef, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Toggle } from "@/components/ui/Toggle";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import { ConfirmDialog } from "./ConfirmDialog";
import { ASK_PERM, type MemoryItem } from "./data";
import {
  MEMORY_LIMIT,
  MEMORY_MAX,
  memoryOn,
  useAddMemory,
  useConfirmMemory,
  useDeleteMemory,
  useEditMemory,
  useForgetAllMemories,
  useMemories,
  useMemorySettings,
  useSetMemoryEnabled,
} from "./memory";
import { MemoryError } from "./MemoryNotes";
import { ActionButton } from "./MessageActions";
import { AskTabs } from "./parts";

function localCheck(
  text: string,
  tv: (key: "required" | "tooLong" | "noAadhaar") => string,
): string | null {
  const value = text.trim();
  if (!value) return tv("required");
  if (value.length > MEMORY_MAX) return tv("tooLong");
  if (containsAadhaarNumber(value)) return tv("noAadhaar");
  return null;
}

/**
 * One item. While memory is off (the member's or the school's switch) it can be deleted but
 * not edited or confirmed (the API answers 409 memory_off).
 */
function MemoryRow({ item, off }: { item: MemoryItem; off: boolean }) {
  const t = useTranslations("ask.memory");
  const tv = useTranslations("validation");
  const edit = useEditMemory();
  const remove = useDeleteMemory();
  const confirm = useConfirmMemory();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text);
  const [error, setError] = useState<string | null>(null);
  const fieldId = useId();
  const errorId = useId();
  const temporary = item.id.startsWith("pending-");
  const created = formatDate(item.created_at);

  if (editing) {
    return (
      <li className="px-4 py-3">
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            const problem = localCheck(draft, tv);
            if (problem) return setError(problem);
            setEditing(false);
            if (draft.trim() !== item.text) edit.mutate({ item, text: draft.trim() });
          }}
        >
          <label htmlFor={fieldId} className="sr-only">
            {t("editLabel")}
          </label>
          <textarea
            id={fieldId}
            value={draft}
            maxLength={MEMORY_MAX}
            rows={2}
            // Opened on request, so it takes focus.
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                setEditing(false);
              }
            }}
            className="block w-full rounded-md border border-border-control bg-surface-muted px-3 py-2 text-base"
          />
          {error ? (
            <p id={errorId} role="alert" className="text-sm font-semibold text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" size="sm">
              {t("save")}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setEditing(false)}>
              {t("cancel")}
            </Button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className={cn("chat-enter flex items-start gap-3 px-4 py-3", temporary && "opacity-70")}>
      <Icon name="memory" className="mt-0.5 size-4 shrink-0 text-violet-ink" />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-ink break-anywhere">{item.text}</p>
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink-subtle">
          {created ? <span>{created}</span> : null}
          {item.source === "suggested" ? <span>{t("fromChat")}</span> : null}
          {item.status === "pending" ? <Pill variant="review">{t("pending")}</Pill> : null}
        </p>
        {item.status === "pending" ? (
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              size="sm"
              disabled={off || confirm.isPending}
              aria-label={t("confirmFor", { text: item.text })}
              onClick={() => confirm.mutate(item.id)}
            >
              {t("confirm")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={remove.isPending}
              aria-label={t("dismissFor", { text: item.text })}
              onClick={() => remove.mutate(item.id)}
            >
              {t("dismiss")}
            </Button>
          </div>
        ) : null}
        <MemoryError error={edit.error ?? remove.error ?? confirm.error} />
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <ActionButton
          icon="pencil"
          label={t("editFor", { text: item.text })}
          disabled={temporary || off}
          onClick={() => {
            setDraft(item.text);
            setError(null);
            setEditing(true);
          }}
        />
        <ActionButton
          icon="trash"
          label={t("deleteFor", { text: item.text })}
          disabled={temporary}
          onClick={() => remove.mutate(item.id)}
        />
      </div>
    </li>
  );
}

/**
 * Manage memory: what memory is and is not, the "Use memory in Ask" switch (off and disabled
 * with the reason when the school has switched memory off), the remembered items (edit,
 * delete, save or dismiss a suggestion), "Add something to remember" and "Forget everything".
 * Every change shows at once and is undone if the API refuses; a refused text (422 memory_*)
 * says why and what to write instead (your own preferences and work context, not facts about
 * students or staff). While memory is off, or holds MEMORY_LIMIT items, nothing can be added.
 */
export function MemoryScreen() {
  const t = useTranslations("ask.memory");
  const ta = useTranslations("ask");
  const tc = useTranslations("common");
  const tv = useTranslations("validation");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const allowed = can(ASK_PERM.ask);
  const settings = useMemorySettings(allowed);
  const items = useMemories(allowed);
  const toggle = useSetMemoryEnabled();
  const add = useAddMemory();
  const forget = useForgetAllMemories();
  const [draft, setDraft] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState(false);
  const [notice, setNotice] = useState("");
  const addField = useRef<HTMLTextAreaElement>(null);
  const addId = useId();
  const addHintId = useId();
  const addErrorId = useId();
  const introId = useId();

  if (me.isPending) return <LoadingState label={tc("loading")} />;

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: ta("home"), href: "/" },
        { label: ta("title"), href: "/ask" },
        { label: t("title") },
      ]}
    />
  );

  if (!allowed) {
    return (
      <div className="space-y-6">
        {header}
        <Alert tone="warning" title={ta("noAccessTitle")}>
          {ta("noAccessBody")}
        </Alert>
      </div>
    );
  }

  const schoolOff = settings.data ? !settings.data.school_enabled : false;
  // Off for this member (their own switch, or the school's): the API refuses adding, editing
  // and confirming (409 memory_off); deleting still works.
  const off = settings.data ? !memoryOn(settings.data) : false;
  const list = items.data ?? [];
  const full = list.length >= MEMORY_LIMIT;

  return (
    <div className="space-y-6">
      {header}
      <AskTabs active="ask" />
      <p role="status" className="sr-only">
        {notice}
      </p>

      <Card title={t("introTitle")} headingLevel={2}>
        <div id={introId} className="space-y-2 text-sm text-ink-muted">
          <p>{t("introIs")}</p>
          <p>{t("introIsNot")}</p>
        </div>
        <div className="mt-4 border-t border-border pt-4">
          {settings.isPending ? (
            <LoadingState label={tc("loading")} rows={1} />
          ) : settings.isError ? (
            <ApiErrorAlert error={settings.error} namespace="ask" />
          ) : (
            <div className="space-y-3">
              <Toggle
                label={t("toggle")}
                description={t("toggleHint")}
                checked={Boolean(settings.data?.enabled) && !schoolOff}
                disabled={schoolOff || toggle.isPending}
                onCheckedChange={(enabled) => toggle.mutate(enabled)}
                labelFirst
              />
              {schoolOff ? <Alert tone="info">{t("schoolOff")}</Alert> : null}
              {off && !schoolOff ? <Alert tone="info">{t("userOff")}</Alert> : null}
              {toggle.isError ? <MemoryError error={toggle.error} /> : null}
            </div>
          )}
        </div>
      </Card>

      <Card
        title={t("listTitle")}
        headingLevel={2}
        actions={
          list.length > 0 ? (
            <Button variant="secondary" size="sm" onClick={() => setForgetting(true)}>
              <Icon name="trash" className="size-4" />
              {t("forgetAll")}
            </Button>
          ) : null
        }
        padding="none"
      >
        {items.isPending ? (
          <div className="p-4">
            <LoadingState label={tc("loading")} rows={3} />
          </div>
        ) : items.isError ? (
          <div className="p-4">
            <ApiErrorAlert error={items.error} namespace="ask" />
          </div>
        ) : list.length === 0 ? (
          <div className="p-4">
            <EmptyState icon="memory" title={t("emptyTitle")} body={t("emptyBody")} />
          </div>
        ) : (
          <>
            <p className="px-4 pt-1 text-sm text-ink-subtle">
              {t("count", { count: list.length })}
            </p>
            <ul className="divide-y divide-border">
              {list.map((item) => (
                <MemoryRow key={item.id} item={item} off={off} />
              ))}
            </ul>
          </>
        )}
        <MemoryError error={forget.error} />
        <form
          noValidate
          className="space-y-2 border-t border-border p-4"
          onSubmit={(event) => {
            event.preventDefault();
            const problem = localCheck(draft, tv);
            if (problem) {
              setAddError(problem);
              addField.current?.focus();
              return;
            }
            const text = draft.trim();
            setDraft("");
            setAddError(null);
            add.mutate(text, {
              onSuccess: () => setNotice(t("saved")),
              // Put the text back so nothing typed is lost.
              onError: () => setDraft(text),
            });
          }}
        >
          <label htmlFor={addId} className="block text-sm font-semibold text-ink">
            {t("add")}
          </label>
          <p id={addHintId} className="text-xs text-ink-subtle">
            {t("addHint")}
          </p>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
            <textarea
              ref={addField}
              id={addId}
              value={draft}
              rows={2}
              maxLength={MEMORY_MAX}
              aria-invalid={addError ? true : undefined}
              aria-describedby={[addError ? addErrorId : null, addHintId].filter(Boolean).join(" ")}
              onChange={(event) => {
                setDraft(event.target.value);
                setAddError(null);
              }}
              disabled={off || full}
              className="block min-h-16 w-full flex-1 rounded-md border border-border-control bg-surface-muted px-3 py-2 text-base disabled:opacity-60"
            />
            <Button type="submit" disabled={off || full || add.isPending}>
              <Icon name="plus" className="size-4" />
              {t("addButton")}
            </Button>
          </div>
          {full && !off ? <p className="text-sm text-ink-muted">{t("fullHint")}</p> : null}
          {addError ? (
            <p id={addErrorId} role="alert" className="text-sm font-semibold text-danger">
              {addError}
            </p>
          ) : null}
          <MemoryError error={add.error} />
        </form>
      </Card>

      <ConfirmDialog
        open={forgetting}
        title={t("forgetTitle")}
        body={t("forgetBody", { count: list.length })}
        confirmLabel={t("forgetConfirm")}
        onClose={() => setForgetting(false)}
        onConfirm={() => {
          forget.mutate(undefined, { onSuccess: () => setNotice(t("forgotten")) });
          return true;
        }}
      />
    </div>
  );
}
