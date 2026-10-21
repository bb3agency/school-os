"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { Field, Input, TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { Select, SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useDocumentList } from "@/features/documents/data";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  DISCLOSURE_KEYS,
  EDIT,
  PUBLISH,
  RESULT_CLASSES,
  draftBody,
  draftState,
  ifMatch,
  itemOfField,
  liveSnapshot,
  startDownload,
  useDisclosureDraft,
  useDisclosureFormat,
  useDisclosureLink,
  useDisclosureSnapshots,
  type DerivedValue,
  type DisclosureDraft,
  type DisclosureFormat,
  type DisclosureLink,
  type DraftState,
  type FormatItem,
  type ResultClass,
  type ResultRow,
  type Snapshot,
  type TableValue,
} from "./data";

const PUBLISHABLE_PURPOSES = new Set(["circular", "policy", "other"]);
const REASON_MIN = 10;
const REASON_MAX = 500;

/** A value SchoolOS fills: text, a table, or "not recorded". */
export function ShownValue({ value, table }: { value: string | null; table: TableValue | null }) {
  const t = useTranslations("disclosure");
  if (table && table.rows.length) {
    return (
      <TableScroll label={t("tableLabel")}>
        <Table density="compact">
          <THead>
            <Tr>
              {table.columns.map((column) => (
                <Th key={column}>{column}</Th>
              ))}
            </Tr>
          </THead>
          <TBody>
            {table.rows.map((row, i) => (
              <Tr key={i}>
                {row.map((cell, j) => (
                  <Td key={j}>{cell}</Td>
                ))}
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    );
  }
  if (value) return <p className="break-anywhere text-ink">{value}</p>;
  return <p className="text-ink-muted">{t("notRecorded")}</p>;
}

function FromSchoolOs({ item, shown }: { item: FormatItem; shown: DerivedValue | undefined }) {
  const t = useTranslations("disclosure");
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-semibold text-ink">{item.label}</p>
        <Pill variant="tag">{t("fromSchoolOs")}</Pill>
      </div>
      <ShownValue value={shown?.value ?? null} table={shown?.table ?? null} />
      <p className="text-sm text-ink-muted">
        {t(item.fill === "school" ? "fromSettingsHint" : "fromRecordsHint")}
      </p>
    </div>
  );
}

function ResultsEditor({
  item,
  rows,
  onChange,
  error,
}: {
  item: FormatItem;
  rows: ResultRow[];
  onChange: (rows: ResultRow[]) => void;
  error: string | undefined;
}) {
  const t = useTranslations("disclosure.results");
  const id = useId();
  const max = item.max_rows ?? 6;
  const set = (index: number, patch: Partial<ResultRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <fieldset className="space-y-2" aria-describedby={`${id}-hint`}>
      <legend className="font-semibold text-ink">{item.label}</legend>
      <p id={`${id}-hint`} className="text-sm text-ink-muted">
        {t("hint")}
      </p>
      {error ? (
        <p id={`${id}-error`} className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      {rows.map((row, index) => (
        <div
          key={index}
          className="grid grid-cols-2 gap-2 rounded-lg border border-border p-3 sm:grid-cols-[8rem_7rem_7rem_7rem_auto] sm:items-end"
        >
          <Field label={t("year")} id={`${id}-y${index}`}>
            {({ id: inputId, describedBy }) => (
              <Input
                id={inputId}
                aria-describedby={describedBy}
                value={row.year}
                placeholder="2025-26"
                maxLength={7}
                inputMode="numeric"
                onChange={(e) => set(index, { year: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("class")} id={`${id}-c${index}`}>
            {({ id: inputId }) => (
              <Select
                id={inputId}
                value={row.class_level}
                options={RESULT_CLASSES.map((c) => ({ value: c, label: t(`classes.${c}`) }))}
                onChange={(e) => set(index, { class_level: e.target.value as ResultClass })}
              />
            )}
          </Field>
          <Field label={t("appeared")} id={`${id}-a${index}`}>
            {({ id: inputId, describedBy }) => (
              <Input
                id={inputId}
                aria-describedby={describedBy}
                value={row.appeared}
                inputMode="numeric"
                maxLength={6}
                onChange={(e) => set(index, { appeared: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("passed")} id={`${id}-p${index}`}>
            {({ id: inputId, describedBy }) => (
              <Input
                id={inputId}
                aria-describedby={describedBy}
                value={row.passed}
                inputMode="numeric"
                maxLength={6}
                onChange={(e) => set(index, { passed: e.target.value })}
              />
            )}
          </Field>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
          >
            {t("remove")}
          </Button>
        </div>
      ))}
      {rows.length < max ? (
        <Button
          variant="secondary"
          size="sm"
          onClick={() =>
            onChange([...rows, { year: "", class_level: "X", appeared: "", passed: "" }])
          }
        >
          {t("add")}
        </Button>
      ) : null}
    </fieldset>
  );
}

function DocumentPicker({
  item,
  chosen,
  known,
  onChange,
  error,
}: {
  item: FormatItem;
  chosen: string[];
  known: Map<string, { title: string | null; publishable: boolean }>;
  onChange: (ids: string[]) => void;
  error: string | undefined;
}) {
  const t = useTranslations("disclosure.documents");
  const id = useId();
  const [pick, setPick] = useState("");
  const list = useDocumentList({ purpose: null, docType: null, status: "active" }, undefined, true);
  const options =
    list.status === "ready"
      ? list.data.data
          .filter((d) => PUBLISHABLE_PURPOSES.has(d.purpose) && !chosen.includes(d.id))
          .map((d) => ({ value: d.id, label: d.title }))
      : [];
  return (
    <div className="space-y-2">
      <p className="font-semibold text-ink" id={`${id}-label`}>
        {item.label}
      </p>
      {chosen.length ? (
        <ul className="space-y-1" aria-labelledby={`${id}-label`}>
          {chosen.map((docId) => {
            const doc = known.get(docId);
            return (
              <li key={docId} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 break-anywhere">{doc?.title ?? t("chosen")}</span>
                {doc && !doc.publishable ? <Pill variant="negative">{t("notReady")}</Pill> : null}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onChange(chosen.filter((x) => x !== docId))}
                >
                  {t("remove")}
                </Button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">{t("none")}</p>
      )}
      {error ? <p className="text-sm text-danger">{error}</p> : null}
      <div className="flex flex-wrap items-end gap-2">
        <SelectField
          label={t("add")}
          className="min-w-0 max-w-md flex-1"
          value={pick}
          placeholder={list.status === "ready" ? t("choose") : t("loading")}
          options={options}
          onChange={(e) => setPick(e.target.value)}
          hint={t("hint")}
        />
        <Button
          variant="secondary"
          disabled={!pick}
          onClick={() => {
            if (!pick) return;
            onChange([...chosen, pick]);
            setPick("");
          }}
        >
          {t("addButton")}
        </Button>
      </div>
    </div>
  );
}

function InputItem({
  item,
  state,
  setState,
  error,
}: {
  item: FormatItem;
  state: DraftState;
  setState: (next: DraftState) => void;
  error: string | undefined;
}) {
  const t = useTranslations("disclosure");
  const value = state.values[item.key] ?? "";
  const set = (text: string) =>
    setState({ ...state, values: { ...state.values, [item.key]: text } });
  const hint = item.contact ? t("contactHint") : item.fill === "link" ? t("linkHint") : undefined;
  if (item.type === "yes_no") {
    return (
      <SelectField
        label={item.label}
        name={item.key}
        value={value}
        placeholder={t("notAnswered")}
        options={[
          { value: "yes", label: t("yes") },
          { value: "no", label: t("no") },
        ]}
        error={error}
        onChange={(e) => set(e.target.value)}
        className="max-w-xs"
      />
    );
  }
  if (item.type === "text" && (item.max_length ?? 0) > 120) {
    return (
      <TextAreaField
        label={item.label}
        name={item.key}
        value={value}
        rows={3}
        maxLength={item.max_length ?? undefined}
        error={error}
        hint={t("plainText")}
        onChange={(e) => set(e.target.value)}
        className="max-w-2xl"
      />
    );
  }
  const numeric = item.type === "integer" || item.type === "number";
  return (
    <TextField
      label={
        <>
          {item.label}
          {item.required ? <span className="text-ink-muted"> {t("required")}</span> : null}
        </>
      }
      name={item.key}
      value={value}
      type={item.type === "email" ? "email" : item.fill === "link" ? "url" : "text"}
      inputMode={numeric ? "decimal" : item.type === "phone" ? "tel" : undefined}
      maxLength={item.max_length ?? (item.fill === "link" ? 300 : 20)}
      error={error}
      hint={hint}
      onChange={(e) => set(e.target.value)}
      className={numeric ? "max-w-xs" : "max-w-2xl"}
      required={item.required}
    />
  );
}

function PublishDialog({ draft, live }: { draft: DisclosureDraft; live: boolean }) {
  const t = useTranslations("disclosure.publish");
  const api = useBffClient("staff");
  const documents = Object.values(draft.documents).reduce((n, list) => n + list.length, 0);
  return (
    <ActionDialog
      triggerLabel={live ? t("again") : t("open")}
      triggerVariant="primary"
      title={t("title")}
      description={t("description", { documents })}
      confirmLabel={t("confirm")}
      stepUp
      consequence={t("consequence")}
      schema={z.object({
        confirm_no_student_data: z
          .string()
          .optional()
          .refine((v) => v === "on", { error: "required" }),
      })}
      invalidate={[DISCLOSURE_KEYS.all]}
      submit={(_input, idempotencyKey) =>
        unwrap(
          api.POST("/api/v1/disclosure/publish", {
            headers: { "Idempotency-Key": idempotencyKey },
            body: { confirm_no_student_data: true },
          }),
        )
      }
    >
      {(errors) => (
        <div className="space-y-3">
          {draft.missing.length ? (
            <Alert tone="warning" title={t("missingTitle")}>
              {t("missingBody", { count: draft.missing.length })}
            </Alert>
          ) : null}
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
            <li>{t("whatPublic1")}</li>
            <li>{t("whatPublic2", { documents })}</li>
            <li>{t("whatPublic3")}</li>
          </ul>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              name="confirm_no_student_data"
              className="mt-1 size-4 accent-primary"
              aria-invalid={errors.confirm_no_student_data ? true : undefined}
            />
            <span>{t("checkbox")}</span>
          </label>
          {errors.confirm_no_student_data ? (
            <p className="text-sm text-danger">{t("checkboxRequired")}</p>
          ) : null}
        </div>
      )}
    </ActionDialog>
  );
}

function PublishedCard({
  link,
  live,
  canPublish,
}: {
  link: DisclosureLink;
  live: Snapshot | null;
  canPublish: boolean;
}) {
  const t = useTranslations("disclosure.published");
  const api = useBffClient("staff");
  const [copied, setCopied] = useState(false);
  const [downloadError, setDownloadError] = useState<unknown>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  if (!link.url) return null;
  const url = link.url;
  async function download(snapshotId: string, format: "html" | "pdf") {
    setDownloadError(undefined);
    setBusy(format);
    try {
      const file = await unwrap(
        api.GET("/api/v1/disclosure/snapshots/{snapshot_id}/download-url", {
          params: { path: { snapshot_id: snapshotId }, query: { format } },
        }),
      );
      startDownload(file.url);
    } catch (failure) {
      setDownloadError(failure);
    } finally {
      setBusy(null);
    }
  }
  return (
    <Card
      title={t("title")}
      description={
        live ? t("liveSince", { date: formatDate(live.published_at) ?? "" }) : t("notLive")
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-0 break-anywhere rounded-md bg-surface-muted px-2 py-1 text-sm text-ink">
            {url}
          </code>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void navigator.clipboard?.writeText(url).then(() => setCopied(true));
            }}
          >
            {copied ? t("copied") : t("copy")}
          </Button>
        </div>
        <p className="text-sm text-ink-muted">{t("linkHint")}</p>
        {live ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink">{t("download")}</span>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy !== null}
              onClick={() => void download(live.id, "html")}
            >
              {busy === "html" ? t("working") : t("html")}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy !== null}
              onClick={() => void download(live.id, "pdf")}
            >
              {busy === "pdf" ? t("working") : t("pdf")}
            </Button>
          </div>
        ) : null}
        <ApiErrorAlert error={downloadError} />
        {canPublish ? (
          <div className="flex flex-wrap gap-2">
            <ActionDialog
              triggerLabel={t("rotate")}
              triggerVariant="secondary"
              triggerSize="sm"
              title={t("rotateTitle")}
              description={t("rotateBody")}
              confirmLabel={t("rotate")}
              stepUp
              consequence={t("rotateConsequence")}
              schema={z.object({})}
              invalidate={[DISCLOSURE_KEYS.all]}
              submit={() => unwrap(api.POST("/api/v1/disclosure/link/rotate"))}
            />
            {live ? (
              <ActionDialog
                triggerLabel={t("withdraw")}
                triggerVariant="danger"
                triggerSize="sm"
                title={t("withdrawTitle")}
                description={t("withdrawBody")}
                confirmLabel={t("withdraw")}
                confirmVariant="danger"
                stepUp
                consequence={t("withdrawConsequence")}
                schema={z.object({
                  reason: z
                    .string()
                    .trim()
                    .min(1, { error: "required" })
                    .min(REASON_MIN, { error: "reasonTooShort" })
                    .max(REASON_MAX, { error: "tooLong" }),
                })}
                invalidate={[DISCLOSURE_KEYS.all]}
                submit={(input) =>
                  unwrap(
                    api.POST("/api/v1/disclosure/snapshots/{snapshot_id}/withdraw", {
                      params: { path: { snapshot_id: live.id } },
                      body: { reason: input.reason },
                    }),
                  )
                }
              >
                {(errors) => (
                  <TextAreaField
                    name="reason"
                    label={t("reason")}
                    hint={t("reasonHint")}
                    error={errors.reason}
                    rows={3}
                    maxLength={REASON_MAX}
                  />
                )}
              </ActionDialog>
            ) : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function History({ snapshots }: { snapshots: Snapshot[] }) {
  const t = useTranslations("disclosure.history");
  if (!snapshots.length) return null;
  return (
    <Card title={t("title")} description={t("description")}>
      <TableScroll label={t("title")}>
        <Table reflow>
          <THead>
            <Tr>
              <Th>{t("published")}</Th>
              <Th>{t("by")}</Th>
              <Th>{t("state")}</Th>
              <Th numeric>{t("documents")}</Th>
              <Th numeric>{t("views")}</Th>
            </Tr>
          </THead>
          <TBody>
            {snapshots.map((s) => (
              <Tr key={s.id}>
                <Td data-label={t("published")}>{formatDateTime(s.published_at) ?? ""}</Td>
                <Td data-label={t("by")}>{s.published_by_name ?? t("formerStaff")}</Td>
                <Td data-label={t("state")}>
                  {s.live ? (
                    <Pill variant="done">{t("live")}</Pill>
                  ) : s.withdrawn_at ? (
                    <span className="space-y-0.5">
                      <Pill variant="negative">{t("withdrawn")}</Pill>
                      {s.withdraw_reason ? (
                        <span className="block text-sm break-anywhere text-ink-muted">
                          {s.withdraw_reason}
                        </span>
                      ) : null}
                    </span>
                  ) : (
                    <Pill variant="tag">{t("replaced")}</Pill>
                  )}
                </Td>
                <Td numeric data-label={t("documents")}>
                  {s.documents}
                </Td>
                <Td numeric data-label={t("views")}>
                  {s.views_total}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      </TableScroll>
    </Card>
  );
}

const ITEM_ERRORS = [
  "aadhaar_not_allowed",
  "phone_not_allowed",
  "markup_not_allowed",
  "https_only",
  "invalid_email",
  "invalid_phone",
  "too_long",
  "out_of_range",
  "required",
  "document_not_publishable",
  "document_not_found",
  "invalid_year",
  "passed_more_than_appeared",
  "duplicate_row",
  "too_precise",
  "invalid",
] as const;
type ItemErrorCode = (typeof ITEM_ERRORS)[number];

/** 422 item errors (`inputs.<key>`, `documents.<key>`) by item key, and the summary links. */
function errorMessages(
  error: unknown,
  message: (code: ItemErrorCode) => string,
): { byItem: Record<string, string>; summary: SummaryError[] } {
  const byItem: Record<string, string> = {};
  const summary: SummaryError[] = [];
  if (!(error instanceof ApiError) || error.status !== 422) return { byItem, summary };
  const list = (error.problem as { errors?: { field?: string; code?: string }[] }).errors ?? [];
  for (const entry of list) {
    const key = itemOfField(entry.field ?? "");
    if (!key || byItem[key]) continue;
    const code = (ITEM_ERRORS as readonly string[]).includes(entry.code ?? "")
      ? (entry.code as ItemErrorCode)
      : "invalid";
    byItem[key] = message(code);
    summary.push({ fieldId: `item-${key}`, message: byItem[key] });
  }
  return { byItem, summary };
}

function DraftEditor({
  format,
  draft,
  canPublish,
  live,
}: {
  format: DisclosureFormat;
  draft: DisclosureDraft;
  canPublish: boolean;
  live: boolean;
}) {
  const t = useTranslations("disclosure");
  const tc = useTranslations("common");
  const ts = useTranslations("schoolSettings");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const initial = useMemo(() => draftState(format, draft), [format, draft]);
  const [state, setState] = useState<DraftState>(initial);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [attempt, setAttempt] = useState(0);
  const [saved, setSaved] = useState(false);
  const known = useMemo(() => {
    const map = new Map<string, { title: string | null; publishable: boolean }>();
    for (const list of Object.values(draft.documents)) {
      for (const d of list) map.set(d.id, { title: d.title, publishable: d.publishable });
    }
    return map;
  }, [draft.documents]);
  const update = (next: DraftState) => {
    setState(next);
    setDirty(true);
    setSaved(false);
  };
  const ti = useTranslations("disclosure.itemErrors");
  const { byItem, summary } = errorMessages(error, (code) => ti(code));

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(undefined);
    try {
      await unwrap(
        api.PUT("/api/v1/disclosure/draft", {
          headers: { "If-Match": ifMatch(draft.version) },
          body: draftBody(format, state),
        }),
      );
      setDirty(false);
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: DISCLOSURE_KEYS.draft });
    } catch (failure) {
      setError(failure);
      setAttempt((n) => n + 1);
    } finally {
      setPending(false);
    }
  }

  const stale = error instanceof ApiError && error.status === 412;
  const itemBody = (item: FormatItem): ReactNode => {
    if (item.fill === "school" || item.fill === "derived") {
      return (
        <div className="space-y-3">
          <FromSchoolOs item={item} shown={draft.from_schoolos[item.key]} />
          {item.takes_documents ? (
            <DocumentPicker
              item={{ ...item, label: t("documentsAlso") }}
              chosen={state.documents[item.key] ?? []}
              known={known}
              error={byItem[item.key]}
              onChange={(ids) =>
                update({ ...state, documents: { ...state.documents, [item.key]: ids } })
              }
            />
          ) : null}
        </div>
      );
    }
    if (item.fill === "document") {
      return (
        <DocumentPicker
          item={item}
          chosen={state.documents[item.key] ?? []}
          known={known}
          error={byItem[item.key]}
          onChange={(ids) =>
            update({ ...state, documents: { ...state.documents, [item.key]: ids } })
          }
        />
      );
    }
    if (item.type === "results") {
      return (
        <ResultsEditor
          item={item}
          rows={state.results[item.key] ?? []}
          error={byItem[item.key]}
          onChange={(rows) => update({ ...state, results: { ...state.results, [item.key]: rows } })}
        />
      );
    }
    return <InputItem item={item} state={state} setState={update} error={byItem[item.key]} />;
  };

  return (
    <form noValidate onSubmit={(e) => void onSubmit(e)} className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-muted">
          {draft.updated_at
            ? t("lastSaved", {
                when: formatDateTime(draft.updated_at) ?? "",
                who: draft.updated_by_name ?? t("history.formerStaff"),
              })
            : t("neverSaved")}
        </p>
        {canPublish ? <PublishDialog draft={draft} live={live} /> : null}
      </div>
      <ErrorSummary errors={summary} attempt={attempt} />
      {format.sections.map((section) => (
        <Card key={section.key} title={section.title}>
          <ul className="divide-y divide-border">
            {section.items.map((item) => (
              <li key={item.key} id={`item-${item.key}`} className="py-4 scroll-mt-24">
                {itemBody(item)}
              </li>
            ))}
          </ul>
        </Card>
      ))}
      {stale ? (
        <Alert tone="warning" title={t("staleTitle")}>
          {t("staleBody")}
        </Alert>
      ) : (
        <ApiErrorAlert error={summary.length ? undefined : error} />
      )}
      {saved ? (
        <Alert tone="success" live>
          {t("saved")}
        </Alert>
      ) : null}
      <SaveBar dirty={dirty} placement="page">
        <Button
          variant="secondary"
          disabled={!dirty || pending}
          onClick={() => {
            setState(initial);
            setDirty(false);
            setError(undefined);
          }}
        >
          {ts("discard")}
        </Button>
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
          {pending ? tc("working") : t("save")}
        </Button>
      </SaveBar>
    </form>
  );
}

/**
 * CBSE disclosure (Appendix IX) editor (US-2901..US-2904; docs/17 §5.10): the format's sections
 * in CBSE's order, values SchoolOS fills marked "From SchoolOS", fields to type, documents to
 * choose and the video link; Save (If-Match); "Publish" (step-up) with the no-student-data
 * confirmation; after publishing the link, the files for the school's website, "Change the
 * link", "Withdraw" and the published history.
 */
export function DisclosureScreen() {
  const t = useTranslations("disclosure");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(EDIT);
  const canPublish = can(PUBLISH);
  const format = useDisclosureFormat(allowed);
  const draft = useDisclosureDraft(allowed);
  const snapshots = useDisclosureSnapshots(allowed);
  const link = useDisclosureLink(allowed);

  let body: ReactNode;
  if (!meLoaded) body = <LoadingState label={tc("loading")} />;
  else if (!allowed) {
    body = (
      <Alert tone="info" title={t("notAllowedTitle")}>
        {t("notAllowedBody")}
      </Alert>
    );
  } else if (format.status === "error" && format.reason === "not_found") {
    body = (
      <Alert tone="info" title={t("hiddenTitle")}>
        {t("hiddenBody")}
      </Alert>
    );
  } else if (format.status === "loading" || draft.status === "loading") {
    body = <LoadingState label={tc("loading")} />;
  } else if (format.status !== "ready" || draft.status !== "ready") {
    const reason =
      format.status === "error" ? format.reason : draft.status === "error" ? draft.reason : null;
    body = (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {reason ? te(`load.${reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  } else {
    const history = snapshots.status === "ready" ? snapshots.data : [];
    const live = liveSnapshot(history);
    body = (
      <div className="space-y-5">
        {!format.data.verified ? (
          <Alert tone="warning" title={t("unverifiedTitle")}>
            {t("unverifiedBody")}
          </Alert>
        ) : null}
        {link.status === "ready" ? (
          <PublishedCard link={link.data} live={live} canPublish={canPublish} />
        ) : null}
        <DraftEditor
          key={draft.data.version}
          format={format.data}
          draft={draft.data}
          canPublish={canPublish}
          live={live !== null}
        />
        <History snapshots={history} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader title={t("title")} description={t("description")} />
      {body}
    </div>
  );
}
