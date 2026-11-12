"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { ASK_PERM } from "./data";
import {
  GAP_KEYS,
  documentIdFrom,
  useGapApi,
  useGapSummary,
  useKnowledgeGaps,
  type GapStatus,
  type KnowledgeGap,
} from "./gaps";
import { AskTabs } from "./parts";
import { VerifiedAnswerDialog } from "./VerifiedAnswerDialog";

const STATUSES: readonly GapStatus[] = ["open", "closed", "dismissed"];
const PILL: Record<GapStatus, PillVariant> = { open: "review", closed: "done", dismissed: "tag" };

const noInput = z.object({});
const documentInput = z.object({
  document: z
    .string()
    .trim()
    .refine((value) => documentIdFrom(value) !== null, { error: "invalid" }),
});

/** Write a verified answer / Mark fixed by a document / Dismiss (If-Match, FR-KB-087..089). */
function GapActions({ gap }: { gap: KnowledgeGap }) {
  const t = useTranslations("ask.gaps");
  const api = useGapApi();
  const question = gap.question ?? "";
  return (
    <div className="flex flex-wrap gap-2" data-print="hide">
      <VerifiedAnswerDialog
        draft={{ question, language: "en", answer_text: "", citations: [] }}
        triggerLabel={t("writeAnswer")}
        triggerVariant="primary"
        triggerSize="sm"
        submit={(data) => api.answer(gap, data)}
        invalidate={[GAP_KEYS.all]}
      />
      <ActionDialog
        triggerLabel={t("linkDocument")}
        triggerSize="sm"
        triggerDescription={t("actionsFor", { question })}
        title={t("linkDocumentTitle")}
        description={t("linkDocumentBody")}
        confirmLabel={t("linkDocumentConfirm")}
        schema={documentInput}
        invalidate={[GAP_KEYS.all]}
        errorNamespace="ask.gaps"
        fieldMap={(field) => (field === "document_id" ? "document" : field)}
        submit={(data) =>
          api.close(gap, {
            resolution: "document",
            document_id: documentIdFrom(data.document) ?? "",
          })
        }
      >
        {(errors) => (
          <TextField
            name="document"
            label={t("documentField")}
            hint={t("documentHint")}
            autoComplete="off"
            spellCheck={false}
            error={errors.document}
          />
        )}
      </ActionDialog>
      <ActionDialog
        triggerLabel={t("dismiss")}
        triggerSize="sm"
        triggerVariant="ghost"
        triggerDescription={t("actionsFor", { question })}
        title={t("dismissTitle")}
        description={t("dismissBody")}
        confirmLabel={t("dismissConfirm")}
        schema={noInput}
        invalidate={[GAP_KEYS.all]}
        errorNamespace="ask.gaps"
        submit={() => api.close(gap, { resolution: "dismissed" })}
      />
    </div>
  );
}

function GapCard({ gap, canManage }: { gap: KnowledgeGap; canManage: boolean }) {
  const t = useTranslations("ask.gaps");
  const signals = [
    gap.not_found_count ? t("signal.not_found", { count: gap.not_found_count }) : null,
    gap.unchecked_count ? t("signal.unchecked", { count: gap.unchecked_count }) : null,
    gap.not_helpful_count ? t("signal.not_helpful", { count: gap.not_helpful_count }) : null,
  ].filter((s): s is string => s !== null);
  const closedOn = formatDate(gap.closed_at);
  return (
    <li>
      <Card
        title={gap.question ?? t("questionUnreadable")}
        actions={
          <Pill variant={PILL[gap.status]} size="md">
            {t(`status.${gap.status}`)}
          </Pill>
        }
      >
        <div className="space-y-3">
          <p className="num text-sm text-ink">
            {t("asked", { count: gap.occurrences })} · {signals.join(" · ")}
          </p>
          <p className="num text-xs text-ink-muted">
            {t("seen", {
              first: formatDate(gap.first_seen_at) ?? "",
              last: formatDate(gap.last_seen_at) ?? "",
            })}
          </p>
          {gap.status !== "open" ? (
            <p className="num text-xs text-ink-muted">
              {gap.resolution === "verified_answer"
                ? t("closedWithAnswer")
                : gap.resolution === "document"
                  ? t("closedWithDocument")
                  : t("dismissedNote")}
              {gap.closed_by_name ? ` · ${t("closedBy", { name: gap.closed_by_name })}` : ""}
              {closedOn ? ` · ${closedOn}` : ""}
              {gap.document_id ? (
                <>
                  {" · "}
                  <Link
                    href={`/documents/${gap.document_id}`}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {t("openDocument")}
                  </Link>
                </>
              ) : null}
            </p>
          ) : null}
          {canManage && gap.status === "open" ? <GapActions gap={gap} /> : null}
        </div>
      </Card>
    </li>
  );
}

function Summary() {
  const t = useTranslations("ask.gaps");
  const tc = useTranslations("common");
  const summary = useGapSummary(true);
  if (summary.status !== "ready") return null;
  const s = summary.data;
  const thisMonth = s.months[0];
  const closed = thisMonth ? thisMonth.closed_with_answer + thisMonth.closed_with_document : null;
  return (
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={t("summaryLabel")}>
      <StatCard
        label={t("openGaps")}
        value={String(s.open_gaps)}
        unavailableLabel={tc("notAvailable")}
      />
      <StatCard
        label={t("notFound")}
        value={String(s.not_found)}
        unavailableLabel={tc("notAvailable")}
      />
      <StatCard
        label={t("notHelpful")}
        value={String(s.not_helpful + s.unchecked)}
        unavailableLabel={tc("notAvailable")}
        hint={t("uncheckedHint", { count: s.unchecked })}
      />
      <StatCard
        label={t("closedThisMonth")}
        value={closed === null ? null : String(closed)}
        unavailableLabel={tc("notAvailable")}
        hint={t("retention", { days: s.retention_days })}
      />
    </dl>
  );
}

/**
 * Knowledge gaps (FA-06; FR-KB-085..FR-KB-090): questions Ask answered "not found", could not
 * check, or that people marked "not helpful", grouped, most asked first. Each shows one sample
 * question with numbers, phone numbers and emails masked, never who asked. Closing one writes
 * a verified answer (checked like any other) or links the document that was missing.
 * Needs `kb.verified_answer.manage`; the API checks every call.
 */
export function KnowledgeGapsScreen() {
  const t = useTranslations("ask.gaps");
  const ta = useTranslations("ask");
  const tc = useTranslations("common");
  const tl = useTranslations("students.list");
  const meQuery = useStaffMeQuery();
  const can = useStaffCan();
  const [status, setStatus] = useState<GapStatus>("open");
  const pages = useCursorStack();
  const allowed = can(ASK_PERM.manageVerified);
  const list = useKnowledgeGaps(status, pages.cursor, allowed);
  const queryClient = useQueryClient();
  const retry = () => void queryClient.invalidateQueries({ queryKey: GAP_KEYS.all });

  if (meQuery.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-5">
        <PageHeader title={ta("title")} />
        <AskTabs active="gaps" />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title={ta("title")}
        description={t("description")}
        actions={
          <Link
            href="/documents"
            className="text-sm font-semibold text-primary underline-offset-4 hover:underline"
          >
            {t("uploadDocument")}
          </Link>
        }
      />
      <AskTabs active="gaps" />
      <Summary />
      <p className="text-sm text-ink-muted">{t("privacyNote")}</p>
      <div className={cardClasses({ padding: "sm" })}>
        <SelectField
          label={t("statusFilter")}
          value={status}
          onChange={(event) => {
            const value = event.currentTarget.value;
            pages.reset();
            setStatus(STATUSES.find((s) => s === value) ?? "open");
          }}
          options={STATUSES.map((value) => ({ value, label: t(`status.${value}`) }))}
        />
      </div>
      {list.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
      {list.status === "error" || list.status === "unavailable" ? (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          <p>{tc("loadErrorBody")}</p>
          {list.status === "error" ? (
            <Button variant="secondary" size="sm" className="mt-3" onClick={retry}>
              <Icon name="refresh" className="size-4" />
              {tc("retry")}
            </Button>
          ) : null}
        </Alert>
      ) : null}
      {list.status === "ready" && list.data.data.length === 0 ? (
        <EmptyState
          icon="checkCircle"
          title={t(`empty.${status}.title`)}
          body={t(`empty.${status}.body`)}
        />
      ) : null}
      {list.status === "ready" && list.data.data.length > 0 ? (
        <section aria-label={t("listLabel")}>
          <ul className="space-y-3">
            {list.data.data.map((gap) => (
              <GapCard key={gap.id} gap={gap} canManage={allowed} />
            ))}
          </ul>
        </section>
      ) : null}
      <Pager
        label={tl("pagesLabel")}
        page={pages.page}
        onPrevious={pages.hasPrevious ? pages.previous : undefined}
        onNext={
          list.status === "ready" && list.data.next_cursor
            ? () => pages.next(list.data.next_cursor as string)
            : undefined
        }
      />
    </div>
  );
}
