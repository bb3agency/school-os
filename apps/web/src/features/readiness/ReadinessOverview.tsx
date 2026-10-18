"use client";

import { useId } from "react";
import { useLocale, useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import type { SectionOption } from "@/features/findings/data";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { formatCount } from "@/lib/format";
import { useReadinessOverview } from "./data";
import { ReadyBar } from "./parts";
import type { ReadinessSummary } from "./types";

interface Counts {
  ready: number;
  students: number;
}

interface OverviewRow {
  /** A section ID, or "all" for the school totals row. */
  id: string;
  label: string;
  cells: Record<string, Counts | null>;
}

/** Rows: the totals first, then every section any check covers, in the school's class order. */
export function overviewRows(
  summaries: readonly ReadinessSummary[],
  sections: readonly SectionOption[],
  labels: { all: string; section: string },
): OverviewRow[] {
  const order = new Map(sections.map((section, index) => [section.id, index]));
  const ids: string[] = [];
  for (const summary of summaries)
    for (const row of summary.sections) if (!ids.includes(row.section_id)) ids.push(row.section_id);
  ids.sort((a, b) => (order.get(a) ?? Infinity) - (order.get(b) ?? Infinity));
  const totals: OverviewRow = {
    id: "all",
    label: labels.all,
    cells: Object.fromEntries(summaries.map((s) => [s.profile.key, s.totals])),
  };
  return [
    totals,
    ...ids.map((id) => ({
      id,
      label: sections.find((section) => section.id === id)?.label ?? labels.section,
      cells: Object.fromEntries(
        summaries.map((s) => [
          s.profile.key,
          s.sections.find((row) => row.section_id === id) ?? null,
        ]),
      ),
    })),
  ];
}

/**
 * One column per board or portal (US-504 AC3, GET /dq/readiness): how many students of each
 * section are ready for every check the school has, so the office sees all windows at once.
 * Each figure opens that check for that section. Shown only with two or more checks.
 */
export function ReadinessOverview({
  enabled,
  sections,
  profileName,
}: {
  enabled: boolean;
  sections: readonly SectionOption[];
  profileName: (key: string) => string;
}) {
  const t = useTranslations("readiness");
  const locale = useLocale() as Locale;
  const headingId = useId();
  const overview = useReadinessOverview(enabled);
  if (!enabled) return null;
  const summaries = overview.data?.summaries ?? [];
  if (overview.isSuccess && summaries.length < 2) return null;
  const count = (value: number) => formatCount(value, locale) ?? "0";

  const columns: ColumnV2<OverviewRow>[] = [
    {
      key: "section",
      stack: "title",
      hideable: false,
      header: t("colSection"),
      cell: (row) => <span className="font-semibold">{row.label}</span>,
    },
    ...summaries.map((summary): ColumnV2<OverviewRow> => ({
      key: `profile-${summary.profile.key}`,
      header: profileName(summary.profile.key),
      name: profileName(summary.profile.key),
      cell: (row) => {
        const cell = row.cells[summary.profile.key];
        if (!cell)
          return (
            <span className="text-sm text-ink-muted" title={t("notCoveredHint")}>
              {t("notCovered")}
            </span>
          );
        const label = t("readyOf", { ready: count(cell.ready), students: count(cell.students) });
        const href =
          row.id === "all"
            ? `/findings/readiness?profile=${summary.profile.key}`
            : `/findings/readiness?profile=${summary.profile.key}&section=${row.id}`;
        return (
          <Link
            href={href}
            className="relative z-[1] block rounded-sm text-ink underline-offset-4 hover:underline"
          >
            <ReadyBar ready={cell.ready} students={cell.students} label={label} />
            <span className="sr-only">
              : {profileName(summary.profile.key)}, {row.label}
            </span>
          </Link>
        );
      },
    })),
  ];

  return (
    <section aria-labelledby={headingId} className="space-y-3 print:hidden">
      <div className="space-y-1">
        <h2 id={headingId} className="text-lg font-semibold text-ink">
          {t("overviewTitle")}
        </h2>
        <p className="text-sm text-ink-muted">{t("overviewHint")}</p>
      </div>
      {overview.isError ? (
        <ApiErrorAlert error={overview.error} />
      ) : (
        <DataTableV2
          id="readiness-overview"
          stacked
          caption={t("overviewTitle")}
          captionHidden
          columns={columns}
          state={
            overview.isPending
              ? { status: "loading" }
              : {
                  status: "ready",
                  data: overviewRows(summaries, sections, {
                    all: t("allSections"),
                    section: t("section"),
                  }),
                }
          }
          rowKey={(row) => row.id}
          emptyTitle={t("noSectionsTitle")}
          onRetry={() => void overview.refetch()}
        />
      )}
    </section>
  );
}
