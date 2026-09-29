"use client";

import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon, type IconName } from "@/components/ui/Icon";
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
        <Link
          href={`/exports/${row.id}`}
          className="font-medium text-primary underline underline-offset-4"
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
      cell: (row) => <span className="tabular-nums">{count(row.student_count)}</span>,
    },
    {
      key: "formats",
      header: tl("colFormats"),
      cell: (row) => (
        <span className="flex flex-wrap gap-1">
          {row.formats.map((format) => (
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

  const types: { key: string; icon: IconName; href: string; action: string; primary: boolean }[] = [
    ...(canPrecheck
      ? [
          {
            key: "precheck",
            icon: "clipboard" as const,
            href: "/exports/new/precheck",
            action: t("newPrecheck"),
            primary: true,
          },
        ]
      : []),
    ...(canList
      ? [
          {
            key: "studentList",
            icon: "users" as const,
            href: "/exports/new/student-list",
            action: t("newStudentList"),
            primary: !canPrecheck,
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {types.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2">
          {types.map((type) => (
            <Card
              key={type.key}
              eyebrow={tl(`types.${type.key as "precheck"}.eyebrow`)}
              title={tl(`types.${type.key as "precheck"}.title`)}
              description={tl(`types.${type.key as "precheck"}.body`)}
              actions={
                <span
                  aria-hidden="true"
                  className="flex size-10 items-center justify-center rounded-full bg-primary-soft text-primary"
                >
                  <Icon name={type.icon} />
                </span>
              }
            >
              <ButtonLink href={type.href} variant={type.primary ? "primary" : "secondary"}>
                <Icon name="plus" className="size-4" />
                {type.action}
              </ButtonLink>
            </Card>
          ))}
        </div>
      ) : null}
      <Card
        title={tl("historyTitle")}
        description={view === "all" ? tl("wholeSchoolHint") : tl("mineHint")}
      >
        <div className="space-y-4">
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
          <div role="status" aria-live="polite" className="text-sm text-ink-muted">
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
      </Card>
    </div>
  );
}
