"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Pager, useCursorStack } from "@/features/students/paging";
import { useStaffCan, useStaffMe, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { displayText } from "./answer";
import { ASK_PERM, useVerifiedAnswers, type VerifiedAnswer, type VerifiedStatus } from "./data";
import { AskTabs, SourceChip } from "./parts";
import { VerifiedAnswerDialog } from "./VerifiedAnswerDialog";

const STATUSES: readonly VerifiedStatus[] = ["active", "needs_review", "retired"];
const TONE: Record<VerifiedStatus, BadgeTone> = {
  active: "success",
  needs_review: "warning",
  retired: "neutral",
};

function VerifiedCard({ answer, mine }: { answer: VerifiedAnswer; mine: boolean }) {
  const t = useTranslations("ask.verified");
  const verifiedOn = formatDate(answer.verified_at);
  const reviewBy = formatDate(answer.review_due);
  return (
    <li>
      <Card
        headingLevel={3}
        title={answer.question}
        actions={<Badge tone={TONE[answer.status]}>{t(`status.${answer.status}`)}</Badge>}
      >
        <div className="space-y-3">
          {answer.status === "needs_review" ? (
            <Alert tone="warning">{t("needsReviewNote")}</Alert>
          ) : null}
          <p className="whitespace-pre-line">{displayText(answer.answer_text)}</p>
          <p className="text-sm text-ink-muted">
            {mine ? t("verifiedByYou") : t("verifiedByOther")}
            {verifiedOn ? ` · ${t("verifiedOn", { date: verifiedOn })}` : ""}
            {reviewBy ? ` · ${t("reviewDueOn", { date: reviewBy })}` : ""}
          </p>
          <div className="space-y-2">
            <p className="text-sm font-semibold">{t("citations")}</p>
            <ol className="space-y-2">
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
        </div>
      </Card>
    </li>
  );
}

/**
 * Verified answers (US-802, FR-KB-030): answers the school checked, newest first, with who
 * verified them and when, and a "check again" flag when a cited document changed. Adding one
 * needs `kb.verified_answer.manage`.
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
  const pages = useCursorStack();
  const allowed = can(ASK_PERM.ask);
  const list = useVerifiedAnswers(status, pages.cursor, allowed);

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
      <div className="max-w-xs">
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
        <section aria-label={t("title")}>
          <ul className="space-y-4">
            {list.data.data.map((answer) => (
              <VerifiedCard
                key={answer.id}
                answer={answer}
                mine={Boolean(me && answer.verified_by === me.membership_id)}
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
