"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent, type ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { SourceChip } from "@/features/ask/parts";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { Link, useRouter } from "@/i18n/navigation";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { useDateInput } from "@/lib/date-format";
import { formatDate, formatDateTime } from "@/lib/format";
import { zodErrorKeys } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  KEYS,
  NOTICE_DRAFT,
  REVIEW,
  confirmSchema,
  ifMatch,
  isReadingBusy,
  useAssignees,
  useCircular,
  type Assignee,
  type CircularDetail,
  type Citation,
  type Reading,
  type Suggestion,
} from "./data";
import { AiNote, LoadGate, ReadingPill } from "./parts";

function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1 rounded-lg border border-border bg-surface-muted px-4 py-3">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="font-semibold break-words text-ink">{children}</dd>
    </div>
  );
}

function Chips({ citations, title }: { citations: readonly Citation[]; title: string }) {
  const t = useTranslations("circulars");
  if (citations.length === 0) return null;
  return (
    <ul aria-label={t("sources")} className="space-y-2">
      {citations.map((citation) => (
        <SourceChip
          key={`${citation.source}-${citation.passage}`}
          source={citation.source}
          title={title}
          quote={citation.quote}
        />
      ))}
    </ul>
  );
}

/**
 * Confirm one suggestion as a task: owner (required), title and due date (editable). The title
 * starts as a neutral text, never the AI summary (audit DL-08). The due date is shown and typed
 * in the school's format and sent as `YYYY-MM-DD`.
 */
function ConfirmForm({
  suggestion,
  assignees,
  onDone,
}: {
  suggestion: Suggestion;
  assignees: readonly Assignee[];
  onDone: () => void;
}) {
  const t = useTranslations("circulars.suggestion");
  const tv = useTranslations("validation");
  const api = useBffClient("staff");
  const dates = useDateInput();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>;
    const parsed = confirmSchema.safeParse(values);
    if (!parsed.success) {
      setErrors(zodErrorKeys(parsed.error));
      return;
    }
    setErrors({});
    setError(undefined);
    setPending(true);
    try {
      await unwrap(
        api.POST("/api/v1/circular-suggestions/{suggestion_id}/confirm", {
          params: { path: { suggestion_id: suggestion.id } },
          headers: { "If-Match": ifMatch(suggestion.version) },
          body: parsed.data,
        }),
      );
      onDone();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  async function dismiss() {
    setError(undefined);
    setPending(true);
    try {
      await unwrap(
        api.POST("/api/v1/circular-suggestions/{suggestion_id}/dismiss", {
          params: { path: { suggestion_id: suggestion.id } },
          headers: { "If-Match": ifMatch(suggestion.version) },
          body: {},
        }),
      );
      onDone();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const fieldError = (name: string) =>
    errors[name] ? translateOr(tv, errors[name] ?? "invalid", "invalid") : undefined;
  const hint = dates.hint(suggestion.due_on);

  return (
    <form onSubmit={(event) => void confirm(event)} noValidate className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <SelectField
          name="owner_membership_id"
          label={t("owner")}
          placeholder={t("chooseOwner")}
          defaultValue=""
          error={fieldError("owner_membership_id")}
          options={assignees.map((a) => ({ value: a.membership_id, label: a.display_name }))}
        />
        <TextField
          name="title"
          label={t("taskTitle")}
          // DL-08: never the AI summary; whoever holds the task may not see the circular.
          defaultValue={t("defaultTaskTitle")}
          maxLength={200}
          error={fieldError("title")}
        />
        <TextField
          // Remounts with the suggested date in the school's format once GET /me brings it.
          key={dates.format}
          name="due_on"
          label={t("dueOn")}
          hint={t("dueHint", hint)}
          inputMode="numeric"
          autoComplete="off"
          placeholder={dates.placeholder}
          defaultValue={dates.fromIso(suggestion.due_on)}
          error={errors.due_on ? t("dueInvalid", hint) : undefined}
        />
      </div>
      <div className="flex flex-wrap gap-3">
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
          {t("confirm")}
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={pending}
          aria-disabled={pending || undefined}
          onClick={() => void dismiss()}
        >
          {t("dismiss")}
        </Button>
      </div>
      <ApiErrorAlert error={error} namespace="circulars" />
    </form>
  );
}

function SuggestionItem({
  suggestion,
  title,
  canReview,
  assignees,
  onDone,
}: {
  suggestion: Suggestion;
  title: string;
  canReview: boolean;
  assignees: readonly Assignee[];
  onDone: () => void;
}) {
  const t = useTranslations("circulars.suggestion");
  return (
    <li className="space-y-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <p className="font-semibold break-words text-ink">{suggestion.title}</p>
          <p className="text-sm text-ink-muted">
            {t("due", { date: formatDate(suggestion.due_on) ?? suggestion.due_on })}
          </p>
        </div>
        <Pill
          variant={
            suggestion.status === "suggested"
              ? "review"
              : suggestion.status === "confirmed"
                ? "done"
                : "sample"
          }
        >
          {t(`status.${suggestion.status}`)}
        </Pill>
      </div>
      <Chips citations={[suggestion.citation]} title={title} />
      {suggestion.status === "confirmed" && suggestion.task_id ? (
        <Link href="/tasks" className="text-sm text-primary underline underline-offset-4">
          {t("openTasks")}
        </Link>
      ) : null}
      {suggestion.status === "suggested" && canReview ? (
        <ConfirmForm suggestion={suggestion} assignees={assignees} onDone={onDone} />
      ) : null}
    </li>
  );
}

function ReadingCard({ circular, reading }: { circular: CircularDetail; reading: Reading }) {
  const t = useTranslations("circulars");
  const telugu = useTeluguEnabled();
  const nothing = t("notFound");
  return (
    <Card title={t("readingTitle")} actions={<ReadingPill status={circular.reading_status} />}>
      <div className="space-y-4">
        <AiNote />
        <dl className="grid gap-3 sm:grid-cols-2">
          <Fact label={t("fields.issuer")}>{reading.issuer ?? nothing}</Fact>
          <Fact label={t("fields.reference")}>{reading.reference_no ?? nothing}</Fact>
          <Fact label={t("fields.issuedOn")}>
            <Value>{formatDate(reading.issued_on)}</Value>
          </Fact>
          <Fact label={t("fields.subject")}>{reading.subject ?? nothing}</Fact>
        </dl>
        <div className={telugu ? "grid gap-4 lg:grid-cols-2" : undefined}>
          <section aria-label={t("summaryEn")} className="space-y-1">
            <h3 className="text-sm font-semibold text-ink">{t("summaryEn")}</h3>
            <p lang="en" className="text-ink">
              {reading.summary_en ?? nothing}
            </p>
          </section>
          {/* ADR-0036: the Telugu summary only while Telugu is switched on. */}
          {telugu ? (
            <section aria-label={t("summaryTe")} className="space-y-1">
              <h3 className="text-sm font-semibold text-ink">{t("summaryTe")}</h3>
              <p lang="te" className="leading-loose text-ink">
                {reading.summary_te ?? nothing}
              </p>
            </section>
          ) : null}
        </div>
        <Chips citations={reading.summary_sources} title={circular.title} />
        {reading.passages_total !== null &&
        reading.passages_sent !== null &&
        reading.passages_sent < reading.passages_total ? (
          <Alert tone="warning">{t("partial")}</Alert>
        ) : null}
        {reading.suggestions_dropped > 0 ? (
          <p className="text-sm text-ink-muted">
            {t("dropped", { count: reading.suggestions_dropped })}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

/**
 * One circular (US-1601, US-1602): the AI reading of its current version (metadata, English
 * and Telugu summary with source chips) and the suggested deadlines, each quoting its sentence.
 * A person confirms each one into a task (with an owner) or dismisses it; then marks the circular
 * reviewed. From a C1 circular a parent notice can be drafted.
 */
export function CircularDetailScreen({ documentId }: { documentId: string }) {
  const t = useTranslations("circulars");
  const tn = useTranslations("school.nav");
  const tr = useTranslations("circulars.reason");
  const can = useStaffCan();
  const canReview = can(REVIEW);
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const router = useRouter();
  const detail = useCircular(documentId, true);
  const assignees = useAssignees(canReview);
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: KEYS.circulars });
    await queryClient.invalidateQueries({ queryKey: KEYS.tasks });
  }

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(undefined);
    try {
      await action();
      await refresh();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const read = () =>
    run(() =>
      unwrap(
        api.POST("/api/v1/circulars/{document_id}/read", {
          params: { path: { document_id: documentId } },
        }),
      ),
    );
  const review = (reading: Reading) =>
    run(() =>
      unwrap(
        api.POST("/api/v1/circulars/{document_id}/review", {
          params: { path: { document_id: documentId } },
          headers: { "If-Match": ifMatch(reading.version) },
          body: {},
        }),
      ),
    );
  const draftNotice = () =>
    run(async () => {
      const notice = await unwrap(
        api.POST("/api/v1/notices", {
          headers: { "Idempotency-Key": newIdempotencyKey() },
          body: { source: "circular", document_id: documentId },
        }),
      );
      router.push(`/notices/${notice.id}`);
    });

  return (
    <div className="space-y-6">
      <LoadGate data={detail}>
        {(circular) => {
          const reading = circular.reading;
          const open = reading?.suggestions.filter((s) => s.status === "suggested") ?? [];
          return (
            <>
              <PageHeader
                title={circular.title}
                description={circular.issuer ?? undefined}
                breadcrumb={[
                  { label: tn("home"), href: "/" },
                  { label: t("title"), href: "/circulars" },
                  { label: circular.title },
                ]}
                actions={
                  <Link
                    href={`/documents/${circular.document_id}`}
                    className="text-sm text-primary underline underline-offset-4"
                  >
                    {t("openDocument")}
                  </Link>
                }
              />
              {circular.reading_status === "not_read" ? (
                <Alert tone="info" title={t("notReadTitle")}>
                  <p>{t("notReadBody")}</p>
                </Alert>
              ) : null}
              {isReadingBusy(circular.reading_status) ? (
                <Alert tone="info" live title={t("busyTitle")}>
                  {t("busyBody")}
                </Alert>
              ) : null}
              {reading?.status === "needs_review" ? (
                <Alert tone="warning" title={t("needsReviewTitle")}>
                  <p>{translateOr(tr, reading.error_code ?? "other", "other")}</p>
                  <p className="mt-1">{t("needsReviewBody")}</p>
                </Alert>
              ) : null}
              {canReview &&
              (circular.reading_status === "not_read" || reading?.can_retry === true) ? (
                <div>
                  <Button
                    onClick={() => void read()}
                    disabled={pending}
                    aria-disabled={pending || undefined}
                  >
                    {circular.reading_status === "not_read" ? t("readNow") : t("tryAgain")}
                  </Button>
                </div>
              ) : null}
              {reading?.status === "ready" ? (
                <ReadingCard circular={circular} reading={reading} />
              ) : null}
              {reading?.status === "ready" ? (
                <Card title={t("suggestionsTitle")} description={t("suggestionsDescription")}>
                  {reading.suggestions.length === 0 ? (
                    <p className="text-sm text-ink-muted">{t("noSuggestions")}</p>
                  ) : (
                    <ul className="space-y-3">
                      {reading.suggestions.map((suggestion) => (
                        <SuggestionItem
                          key={suggestion.id}
                          suggestion={suggestion}
                          title={circular.title}
                          canReview={canReview}
                          assignees={assignees.status === "ready" ? assignees.data : []}
                          onDone={() => void refresh()}
                        />
                      ))}
                    </ul>
                  )}
                </Card>
              ) : null}
              {reading && (reading.status === "ready" || reading.status === "needs_review") ? (
                <Card title={t("reviewTitle")}>
                  {reading.reviewed_at ? (
                    <p className="text-sm">
                      {t("reviewedBy", {
                        name: reading.reviewed_by?.display_name ?? t("formerStaff"),
                        when: formatDateTime(reading.reviewed_at) ?? "",
                      })}
                    </p>
                  ) : canReview ? (
                    <div className="space-y-2">
                      <p className="text-sm text-ink-muted">
                        {open.length > 0
                          ? t("reviewPending", { count: open.length })
                          : t("reviewReady")}
                      </p>
                      <Button
                        variant="secondary"
                        onClick={() => void review(reading)}
                        disabled={pending || open.length > 0}
                        aria-disabled={pending || open.length > 0 || undefined}
                      >
                        {t("markReviewed")}
                      </Button>
                    </div>
                  ) : (
                    <p className="text-sm text-ink-muted">{t("notReviewed")}</p>
                  )}
                </Card>
              ) : null}
              {can(NOTICE_DRAFT) ? (
                <Card title={t("noticeTitle")} description={t("noticeDescription")}>
                  {circular.sensitivity === "C1" ? (
                    <Button
                      variant="brand"
                      onClick={() => void draftNotice()}
                      disabled={pending}
                      aria-disabled={pending || undefined}
                    >
                      {t("draftNotice")}
                    </Button>
                  ) : (
                    <Alert tone="info">{t("noticePersonal")}</Alert>
                  )}
                </Card>
              ) : null}
              <ApiErrorAlert error={error} namespace="circulars" />
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
