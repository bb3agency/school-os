"use client";

import { useTranslations } from "next-intl";
import { useDeferredValue, useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Card, cardClasses } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { SearchInput } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Pager, useCursorStack } from "@/features/students/paging";
import { useStaffCan, useStaffMe, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { displayText } from "./answer";
import {
  ASK_KEYS,
  ASK_PERM,
  useKnowledgeApi,
  useVerifiedAnswers,
  type VerifiedAnswer,
  type VerifiedStatus,
} from "./data";
import { AskTabs, SourceChip } from "./parts";
import { VerifiedAnswerDialog } from "./VerifiedAnswerDialog";

const STATUSES: readonly VerifiedStatus[] = ["active", "needs_review", "retired"];
const PILL: Record<VerifiedStatus, PillVariant> = {
  active: "done",
  needs_review: "review",
  retired: "tag",
};

/** Case-insensitive match on the question and answer of the answers already loaded. */
export function matchesFilter(answer: VerifiedAnswer, filter: string): boolean {
  const wanted = filter.trim().toLocaleLowerCase();
  if (!wanted) return true;
  return `${answer.question}
${answer.answer_text}`
    .toLocaleLowerCase()
    .includes(wanted);
}

const noInput = z.object({});

/**
 * Check and confirm / Retire (FR-KB-030; `kb.verified_answer.manage`, If-Match with the version
 * listed). Review sends an empty body: the stored text and sources are checked again against
 * the current document versions (422 as on create). Neither is offered on a retired answer.
 */
function VerifiedActions({ answer }: { answer: VerifiedAnswer }) {
  const t = useTranslations("ask.verified");
  const api = useKnowledgeApi();
  if (answer.status === "retired") return null;
  return (
    <div className="flex flex-wrap gap-2" data-print="hide">
      <ActionDialog
        triggerLabel={t("review")}
        triggerSize="sm"
        triggerDescription={t("actionsFor", { question: answer.question })}
        title={t("reviewTitle")}
        description={t("reviewBody")}
        confirmLabel={t("reviewConfirm")}
        schema={noInput}
        invalidate={[ASK_KEYS.verifiedAll]}
        errorNamespace="ask.verified"
        submit={() => api.reviewVerified(answer, {})}
      />
      <ActionDialog
        triggerLabel={t("retire")}
        triggerSize="sm"
        triggerVariant="danger"
        triggerDescription={t("actionsFor", { question: answer.question })}
        title={t("retireTitle")}
        description={t("retireBody")}
        confirmLabel={t("retireConfirm")}
        confirmVariant="danger"
        schema={noInput}
        invalidate={[ASK_KEYS.verifiedAll]}
        errorNamespace="ask.verified"
        submit={() => api.retireVerified(answer)}
      />
    </div>
  );
}

function VerifiedCard({
  answer,
  mine,
  canManage,
}: {
  answer: VerifiedAnswer;
  mine: boolean;
  canManage: boolean;
}) {
  const t = useTranslations("ask.verified");
  const name = answer.verified_by_name?.trim();
  const verifiedOn = formatDate(answer.verified_at);
  const reviewBy = formatDate(answer.review_due);
  return (
    <li>
      <Card
        headingLevel={3}
        title={answer.question}
        actions={
          <Pill variant={PILL[answer.status]} size="md">
            {t(`status.${answer.status}`)}
          </Pill>
        }
      >
        <div className="space-y-4">
          {answer.status === "needs_review" ? (
            <Alert tone="warning">{t("needsReviewNote")}</Alert>
          ) : null}
          <p className="whitespace-pre-line">{displayText(answer.answer_text)}</p>
          <p className="font-mono text-xs text-ink-subtle">
            {mine
              ? t("verifiedByYou")
              : name
                ? t("verifiedByName", { name })
                : t("verifiedByOther")}
            {verifiedOn ? ` · ${t("verifiedOn", { date: verifiedOn })}` : ""}
            {reviewBy ? ` · ${t("reviewDueOn", { date: reviewBy })}` : ""}
          </p>
          <div className="space-y-2">
            <p className="text-sm font-semibold text-ink">{t("citations")}</p>
            <ol className="grid gap-3 lg:grid-cols-2">
              {answer.citations.map((citation, i) => (
                <SourceChip
                  key={`${citation.source}-${i}`}
                  index={i + 1}
                  source={citation.source}
                  title={t("sourceTitle", { number: i + 1 })}
                  quote={citation.cited_text}
                />
              ))}
            </ol>
          </div>
          {canManage ? <VerifiedActions answer={answer} /> : null}
        </div>
      </Card>
    </li>
  );
}

/**
 * Verified answers (US-802, FR-KB-030): answers the school checked, newest first, with who
 * verified them (their name in this school) and when, and a "check again" flag when a cited
 * document changed. Adding, confirming again and retiring need `kb.verified_answer.manage`.
 */
export function VerifiedAnswersScreen() {
  const t = useTranslations("ask.verified");
  const ta = useTranslations("ask");
  const tc = useTranslations("common");
  const tl = useTranslations("students.list");
  const meQuery = useStaffMeQuery();
  const me = useStaffMe();
  const can = useStaffCan();
  const [status, setStatus] = useState<VerifiedStatus | null>(null);
  const [filter, setFilter] = useState("");
  const deferredFilter = useDeferredValue(filter);
  const pages = useCursorStack();
  const allowed = can(ASK_PERM.ask);
  const list = useVerifiedAnswers(status, pages.cursor, allowed);

  const shown =
    list.status === "ready" ? list.data.data.filter((a) => matchesFilter(a, deferredFilter)) : [];

  if (meQuery.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-6">
        <PageHeader title={ta("title")} />
        <AskTabs active="verified" />
        <Alert tone="warning" title={ta("noAccessTitle")}>
          {ta("noAccessBody")}
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={ta("title")}
        description={t("description")}
        actions={
          can(ASK_PERM.manageVerified) ? (
            <VerifiedAnswerDialog draft={null} triggerLabel={t("new")} triggerVariant="primary" />
          ) : null
        }
      />
      <AskTabs active="verified" />
      <div className={cardClasses({ padding: "sm" })}>
        <div className="grid items-end gap-4 md:grid-cols-[2fr_1fr]">
          {/* Filters only the answers on this page (no request): the list is short and paged. */}
          <SearchInput
            label={t("filterLabel")}
            labelVisible
            placeholder={t("filterPlaceholder")}
            value={filter}
            onChange={(event) => setFilter(event.currentTarget.value)}
            autoComplete="off"
          />
          <SelectField
            label={t("statusFilter")}
            placeholder={tc("all")}
            value={status ?? ""}
            onChange={(event) => {
              const value = event.currentTarget.value;
              pages.reset();
              setStatus(STATUSES.find((s) => s === value) ?? null);
            }}
            options={STATUSES.map((value) => ({ value, label: t(`status.${value}`) }))}
          />
        </div>
      </div>
      {list.status === "loading" ? <LoadingState label={tc("loading")} /> : null}
      {list.status === "error" || list.status === "unavailable" ? (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {tc("loadErrorBody")}
        </Alert>
      ) : null}
      {list.status === "ready" && list.data.data.length === 0 ? (
        <EmptyState title={t("emptyTitle")} body={t("emptyBody")} />
      ) : null}
      {list.status === "ready" && list.data.data.length > 0 ? (
        <section aria-label={t("title")} className="space-y-3">
          <p role="status" className="text-sm text-ink-muted">
            {deferredFilter.trim()
              ? t("filterCount", { count: shown.length, total: list.data.data.length })
              : ""}
          </p>
          {shown.length === 0 ? (
            <EmptyState title={t("filterEmptyTitle")} body={t("filterEmptyBody")} icon="search" />
          ) : null}
          <ul className="space-y-4">
            {shown.map((answer) => (
              <VerifiedCard
                key={answer.id}
                answer={answer}
                mine={Boolean(me && answer.verified_by === me.membership_id)}
                canManage={can(ASK_PERM.manageVerified)}
              />
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
