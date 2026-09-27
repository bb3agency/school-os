"use client";

import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { ButtonLink } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabNav } from "@/components/ui/TabNav";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { useExportList, useExportProfiles } from "./data";
import type { ExportListFilters } from "./filters";
import { ExportStatusBadge, exportTitle, RequesterName } from "./parts";
import { EXPORT_PERM, EXPORT_SCREEN_PERMISSIONS, isExportBusy, type Export } from "./types";

/**
 * Exports (US-501 AC4, US-901, FR-EXP-001..004; ADR-0021): your own exports by default; the
 * whole school's only for `export.read_all` holders. Rows being made are polled until they
 * settle (not while the tab is hidden). Filters in the URL are codes only (`?view=all`).
 */
export function ExportsScreen({ filters }: { filters: ExportListFilters }) {
  const t = useTranslations("exports");
  const tl = useTranslations("exports.list");
  const tk = useTranslations("exports.kind");
  const tf = useTranslations("exports.format");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const pages = useCursorStack();
  const canSeeAll = can(EXPORT_PERM.readAll);
  // Without export.read_all the "whole school" view is never asked for (the API would say 403).
  const view = filters.view === "all" && canSeeAll ? "all" : "me";
  const allowed = can([...EXPORT_SCREEN_PERMISSIONS]);
  const list = useExportList(view, pages.cursor, allowed);
  const profiles = useExportProfiles(can([EXPORT_PERM.board, EXPORT_PERM.portal]));

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="warning" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const title = (row: Export) => exportTitle(row, profiles.data, locale, (kind) => tk(kind));
  const rows: Loadable<readonly Export[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const next = list.status === "ready" ? list.data.next_cursor : null;
  const busy = list.status === "ready" && list.data.data.some((row) => isExportBusy(row.status));

  const columns: Column<Export>[] = [
    {
      key: "what",
      header: tl("colWhat"),
      cell: (row) => (
        <Link href={`/exports/${row.id}`} className="font-semibold text-primary underline">
          {title(row)}
          <span className="sr-only">
            {", "}
            {formatDateTime(row.created_at)}
          </span>
        </Link>
      ),
    },
    { key: "by", header: tl("colBy"), cell: (row) => <RequesterName row={row} /> },
    { key: "students", header: tl("colStudents"), cell: (row) => count(row.student_count) },
    {
      key: "formats",
      header: tl("colFormats"),
      cell: (row) => row.formats.map((format) => tf(format)).join(", "),
    },
    {
      key: "status",
      header: tl("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <ExportStatusBadge status={row.status} />
          {row.include_sensitive ? (
            <span className="text-xs text-ink-muted">{t("restrictedIncluded")}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "created",
      header: tl("colCreated"),
      cell: (row) => <Value>{formatDateTime(row.created_at)}</Value>,
    },
    {
      key: "expires",
      header: tl("colExpires"),
      cell: (row) =>
        row.status === "expired" ? tl("deleted") : <Value>{formatDateTime(row.expires_at)}</Value>,
    },
  ];

  const canPrecheck = can([EXPORT_PERM.board, EXPORT_PERM.portal]);
  const canList = can(EXPORT_PERM.studentList);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            {canPrecheck ? (
              <ButtonLink href="/exports/new/precheck">{t("newPrecheck")}</ButtonLink>
            ) : null}
            {canList ? (
              <ButtonLink href="/exports/new/student-list" variant="secondary">
                {t("newStudentList")}
              </ButtonLink>
            ) : null}
          </>
        }
      />
      {canSeeAll ? (
        <TabNav
          label={tl("viewsLabel")}
          activeId={view}
          items={[
            { id: "me", href: "/exports", label: tl("mine") },
            { id: "all", href: "/exports?view=all", label: tl("wholeSchool") },
          ]}
        />
      ) : null}
      <p className="text-sm text-ink-muted">
        {view === "all" ? tl("wholeSchoolHint") : tl("mineHint")}
      </p>
      <div role="status" aria-live="polite" className="text-sm">
        {busy ? tl("updating") : null}
      </div>
      <DataTable
        caption={view === "all" ? tl("wholeSchool") : tl("mine")}
        captionHidden
        columns={columns}
        state={rows}
        rowKey={(row) => row.id}
        emptyTitle={tl("emptyTitle")}
        emptyBody={canPrecheck || canList ? tl("emptyBodyMaker") : tl("emptyBody")}
      />
      <Pager
        label={tl("pagesLabel")}
        page={pages.page}
        onPrevious={pages.hasPrevious ? pages.previous : undefined}
        onNext={next ? () => pages.next(next) : undefined}
      />
    </div>
  );
}
