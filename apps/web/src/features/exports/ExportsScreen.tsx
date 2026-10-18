"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { Menu } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabNav } from "@/components/ui/TabNav";
import { Value } from "@/components/ui/Value";
import { useRetentionDays } from "@/features/admin/data";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatCount, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { EXPORT_KEYS, useExportList, useExportProfiles } from "./data";
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
  const tn = useTranslations("school.nav");
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
  // FR-ADM-002: the school may keep export files for less than the default.
  const keptDays = useRetentionDays("exports", allowed);
  const queryClient = useQueryClient();

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!allowed) {
    return (
      <div className="space-y-5">
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

  const columns: ColumnV2<Export>[] = [
    {
      key: "what",
      header: tl("colWhat"),
      cell: (row) => (
        <Link
          href={`/exports/${row.id}`}
          className="font-semibold text-primary underline underline-offset-4"
        >
          {title(row)}
          <span className="sr-only">
            {", "}
            {formatDateTime(row.created_at)}
          </span>
        </Link>
      ),
    },
    { key: "by", header: tl("colBy"), cell: (row) => <RequesterName row={row} /> },
    {
      key: "students",
      header: tl("colStudents"),
      numeric: true,
      cell: (row) => <span className="num">{count(row.student_count)}</span>,
    },
    {
      key: "formats",
      header: tl("colFormats"),
      cell: (row) => (
        <span className="flex flex-wrap gap-1">
          {(row.formats ?? []).map((format) => (
            <Pill key={format} variant="tag">
              {tf(format)}
            </Pill>
          ))}
        </span>
      ),
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
      cell: (row) => (
        <span className="num whitespace-nowrap">
          <Value>{formatDateTime(row.created_at)}</Value>
        </span>
      ),
    },
    {
      key: "expires",
      header: tl("colExpires"),
      cell: (row) =>
        row.status === "expired" ? (
          tl("deleted")
        ) : (
          <span className="num whitespace-nowrap">
            <Value>{formatDateTime(row.expires_at)}</Value>
          </span>
        ),
    },
  ];

  const canPrecheck = can([EXPORT_PERM.board, EXPORT_PERM.portal]);
  const canList = can(EXPORT_PERM.studentList);

  // One primary: the pre-check (the job most offices come here for); the student list is the
  // primary only for members who may not make pre-checks, else it sits under "More".
  const actions = (
    <>
      {canPrecheck && canList ? (
        <Menu
          label={t("more")}
          variant="button"
          align="end"
          trigger={t("more")}
          entries={[
            {
              kind: "link",
              id: "student-list",
              label: t("newStudentList"),
              href: "/exports/new/student-list",
              icon: "plus",
            },
          ]}
        />
      ) : null}
      {canPrecheck ? (
        <ButtonLink href="/exports/new/precheck">
          <Icon name="plus" className="size-4" />
          {t("newPrecheck")}
        </ButtonLink>
      ) : canList ? (
        <ButtonLink href="/exports/new/student-list">
          <Icon name="plus" className="size-4" />
          {t("newStudentList")}
        </ButtonLink>
      ) : null}
    </>
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={
          keptDays === null ? t("descriptionSchoolPeriod") : t("description", { days: keptDays })
        }
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        actions={canPrecheck || canList ? actions : null}
      />
      <div role="status" aria-live="polite" className="text-sm text-ink-muted empty:hidden">
        {busy ? tl("updating") : null}
      </div>
      <DataTableV2
        id="exports"
        stacked
        caption={view === "all" ? tl("wholeSchool") : tl("mine")}
        captionHidden
        columns={columns}
        state={rows}
        rowKey={(row) => row.id}
        toolbar={
          <div className="flex w-full flex-wrap items-center gap-x-4 gap-y-2">
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
          </div>
        }
        emptyTitle={tl("emptyTitle")}
        emptyBody={canPrecheck || canList ? tl("emptyBodyMaker") : tl("emptyBody")}
        onRetry={() => void queryClient.invalidateQueries({ queryKey: EXPORT_KEYS.all })}
        footer={
          pages.hasPrevious || next ? (
            <Pager
              label={tl("pagesLabel")}
              page={pages.page}
              onPrevious={pages.hasPrevious ? pages.previous : undefined}
              onNext={next ? () => pages.next(next) : undefined}
            />
          ) : undefined
        }
      />
    </div>
  );
}
