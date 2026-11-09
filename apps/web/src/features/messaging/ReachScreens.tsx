"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Pill } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { CATEGORIES, READ, RECORD, isUnavailable, type Category } from "./data";
import {
  missingSlipsHref,
  reachedPercent,
  useCoverage,
  useFollowUp,
  useNotReached,
  type FollowUpStudent,
  type GuardianReach,
  type NotReachedStudent,
  type SectionCoverage,
} from "./reach-data";

/**
 * FP-01: who WhatsApp reaches (FR-MSG-039..046). Coverage per section for one category, the
 * students no message reaches (names only within the reader's scope, never a phone number),
 * printable join slips for exactly those families, and per broadcast the families to follow
 * up. A list to act on, never a score or an automatic resend.
 */

function useCount(): (n: number | undefined) => string {
  const locale = useLocale() as Locale;
  return (n) => formatCount(n ?? 0, locale) ?? "0";
}

function useReason(): (code: string) => string {
  const tr = useTranslations("messaging.log.reasons");
  const ts = useTranslations("messaging.log.statuses");
  return (code) => {
    if (ts.has(code as "sent")) return ts(code as "sent");
    if (tr.has(code as "no_optin")) return tr(code as "no_optin");
    return tr("other");
  };
}

function GuardianList({ guardians }: { guardians: readonly GuardianReach[] }) {
  const t = useTranslations("messaging.reach");
  const reason = useReason();
  if (guardians.length === 0) return <span className="text-ink-muted">{t("noGuardian")}</span>;
  return (
    <ul className="space-y-1">
      {guardians.map((g) => (
        <li key={g.guardian_id}>
          <span className="font-semibold text-ink">{g.relationship}</span>
          {g.is_primary ? <span className="text-ink-muted"> ({t("primary")})</span> : null}:{" "}
          {reason(g.reason)}
        </li>
      ))}
    </ul>
  );
}

function NotReachedCard({ section, category }: { section: SectionCoverage; category: Category }) {
  const t = useTranslations("messaging.reach");
  const can = useStaffCan();
  const list = useNotReached(section.section_id, category);
  const rows: Loadable<readonly NotReachedStudent[]> =
    list.status === "ready" ? { status: "ready", data: list.data.items } : list;
  const columns: ColumnV2<NotReachedStudent>[] = [
    {
      key: "student",
      header: t("columns.student"),
      cell: (row) => row.name ?? <span className="text-ink-muted">{t("nameHidden")}</span>,
    },
    {
      key: "guardians",
      header: t("columns.guardians"),
      cell: (row) => <GuardianList guardians={row.guardians} />,
    },
  ];
  return (
    <Card
      title={t("missingTitle", { section: section.label })}
      description={t("missingDescription")}
    >
      <div className="space-y-4">
        {can(RECORD) && section.students > section.students_reached ? (
          <a
            href={missingSlipsHref(section.section_id, category)}
            target="_blank"
            rel="noopener"
            className={buttonClasses("secondary")}
          >
            <Icon name="file" className="size-4" />
            {t("printMissing")}
          </a>
        ) : null}
        <DataTableV2
          id="reach-missing"
          caption={t("missingCaption", { section: section.label })}
          columns={columns}
          state={rows}
          rowKey={(row) => row.student_id}
          stacked
          emptyTitle={t("missingEmptyTitle")}
          emptyBody={t("missingEmptyBody")}
        />
        {list.status === "ready" && list.data.truncated ? (
          <p className="text-sm text-ink-muted">{t("truncated")}</p>
        ) : null}
      </div>
    </Card>
  );
}

/** /messages/reach: opt-in coverage per section and the families no message reaches. */
export function ReachScreen() {
  const t = useTranslations("messaging.reach");
  const tm = useTranslations("messaging");
  const ts = useTranslations("messaging.slips");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const count = useCount();
  const [category, setCategory] = useState<Category>("notices");
  const [selected, setSelected] = useState<string>("");
  const allowed = can(READ);
  const coverage = useCoverage(category, me !== undefined && allowed);
  const rows: Loadable<readonly SectionCoverage[]> =
    coverage.status === "ready" ? { status: "ready", data: coverage.data.sections } : coverage;
  const chosen =
    coverage.status === "ready"
      ? coverage.data.sections.find((s) => s.section_id === selected)
      : undefined;
  const columns: ColumnV2<SectionCoverage>[] = [
    { key: "section", header: t("columns.section"), cell: (row) => row.label },
    {
      key: "students",
      header: t("columns.students"),
      numeric: true,
      cell: (row) => <Value>{count(row.students)}</Value>,
    },
    {
      key: "reached",
      header: t("columns.reached"),
      numeric: true,
      cell: (row) => (
        <Value>
          {t("reachedCell", {
            reached: count(row.students_reached),
            percent: reachedPercent(row.students_reached, row.students),
          })}
        </Value>
      ),
    },
    {
      key: "optedIn",
      header: t("columns.optedIn"),
      numeric: true,
      cell: (row) => <Value>{count(row.guardians_opted_in)}</Value>,
    },
    {
      key: "noMobile",
      header: t("columns.noMobile"),
      numeric: true,
      cell: (row) => <Value>{count(row.guardians_without_mobile)}</Value>,
    },
    {
      key: "action",
      header: t("columns.action"),
      cell: (row) =>
        row.students > row.students_reached ? (
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={selected === row.section_id}
            onClick={() => setSelected(row.section_id)}
          >
            {t("seeMissing", { section: row.label })}
          </Button>
        ) : (
          <Pill variant="done">{t("allReached")}</Pill>
        ),
    },
  ];
  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tm("logTitle"), href: "/messages" },
          { label: t("title") },
        ]}
      />
      {me !== undefined && !allowed ? (
        <EmptyState icon="lock" title={tm("noAccessTitle")} body={tm("noAccessBody")} />
      ) : isUnavailable(coverage) ? (
        <EmptyState icon="search" title={t("unavailableTitle")} body={t("unavailableBody")} />
      ) : (
        <>
          <Card>
            <div className="space-y-3">
              <SelectField
                label={t("category")}
                value={category}
                onChange={(event) => {
                  setCategory(event.target.value as Category);
                  setSelected("");
                }}
                options={CATEGORIES.map((c) => ({ value: c, label: ts(`category.${c}`) }))}
              />
              {coverage.status === "ready" ? (
                <p className="text-ink">
                  {t("summary", {
                    reached: count(coverage.data.students_reached),
                    students: count(coverage.data.students),
                    percent: reachedPercent(coverage.data.students_reached, coverage.data.students),
                  })}
                </p>
              ) : null}
              <p className="text-sm text-ink-muted">{t("listNote")}</p>
            </div>
          </Card>
          <DataTableV2
            id="reach-coverage"
            caption={t("coverageCaption")}
            columns={columns}
            state={rows}
            rowKey={(row) => row.section_id}
            stacked
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
          />
          {chosen ? (
            <NotReachedCard key={chosen.section_id} section={chosen} category={category} />
          ) : null}
        </>
      )}
    </div>
  );
}

/**
 * On a broadcast's report: the students the notice did not reach (not sent, failed, or not
 * opened 48 hours after sending), to follow up by phone or a printed copy (FR-MSG-044,
 * FR-MSG-045). Nothing is resent.
 */
export function BroadcastFollowUpCard({ broadcastId }: { broadcastId: string }) {
  const t = useTranslations("messaging.reach.followUp");
  const tr = useTranslations("messaging.reach");
  const count = useCount();
  const followUp = useFollowUp(broadcastId);
  if (followUp.status === "error") return null;
  const rows: Loadable<readonly FollowUpStudent[]> =
    followUp.status === "ready" ? { status: "ready", data: followUp.data.items } : followUp;
  const columns: ColumnV2<FollowUpStudent>[] = [
    {
      key: "student",
      header: tr("columns.student"),
      cell: (row) => (
        <>
          {row.name ?? <span className="text-ink-muted">{tr("nameHidden")}</span>}
          {row.class_section ? (
            <span className="block text-sm text-ink-muted">{row.class_section}</span>
          ) : null}
        </>
      ),
    },
    {
      key: "outcome",
      header: t("outcome"),
      cell: (row) => (
        <Pill variant={row.outcome === "not_opened" ? "review" : "negative"}>
          {t(`outcomes.${row.outcome}`)}
        </Pill>
      ),
    },
    {
      key: "guardians",
      header: tr("columns.guardians"),
      cell: (row) => <GuardianList guardians={row.guardians} />,
    },
  ];
  return (
    <Card
      title={t("title")}
      description={
        followUp.status === "ready"
          ? t("description", { hours: followUp.data.not_opened_hours })
          : undefined
      }
    >
      <div className="space-y-4">
        {followUp.status === "ready" ? (
          <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-ink">
            {(["opened", "pending", "not_sent", "failed", "not_opened"] as const).map((key) => (
              <li key={key}>
                {t(`outcomes.${key}`)}:{" "}
                <span className="num font-semibold">{count(followUp.data.outcomes[key])}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <DataTableV2
          id="broadcast-follow-up"
          caption={t("caption")}
          columns={columns}
          state={rows}
          rowKey={(row) => row.student_id}
          stacked
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
        />
      </div>
    </Card>
  );
}
