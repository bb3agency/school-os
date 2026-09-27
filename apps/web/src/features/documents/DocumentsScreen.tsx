"use client";

import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link } from "@/i18n/navigation";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { useDocumentList } from "./data";
import type { DocumentListFilters } from "./filters";
import { VersionStatusBadge } from "./parts";
import {
  DOC_STATUSES,
  DOC_TYPES,
  DOCUMENT_PERM,
  isVersionBusy,
  PURPOSES,
  type DocumentRow,
} from "./types";

/**
 * Documents (US-701, FR-DOC-005..008): what the member may see (the API filters by the
 * document's "who can see it" list and the member's classes/sections), newest first, with the
 * processing status of the current version. Rows being checked are polled until they settle
 * (not while the tab is hidden). Filters live in the URL as codes only (GET form).
 */
export function DocumentsScreen({
  filters,
  deleted = false,
}: {
  filters: DocumentListFilters;
  deleted?: boolean;
}) {
  const t = useTranslations("documents");
  const tl = useTranslations("documents.list");
  const ttype = useTranslations("documents.docType");
  const tpurpose = useTranslations("documents.purpose");
  const tstatus = useTranslations("documents.docStatus");
  const tsens = useTranslations("documents.sensitivity");
  const tc = useTranslations("common");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const pages = useCursorStack();
  const allowed = can(DOCUMENT_PERM.read);
  const list = useDocumentList(filters, pages.cursor, allowed);

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

  const rows: Loadable<readonly DocumentRow[]> =
    list.status === "ready" ? { status: "ready", data: list.data.data } : list;
  const next = list.status === "ready" ? list.data.next_cursor : null;
  const busy =
    list.status === "ready" &&
    list.data.data.some((row) => row.current_version && isVersionBusy(row.current_version.status));

  const columns: Column<DocumentRow>[] = [
    {
      key: "title",
      header: tl("colTitle"),
      cell: (row) => (
        <Link href={`/documents/${row.id}`} className="font-semibold text-primary underline">
          {row.title}
        </Link>
      ),
    },
    { key: "type", header: tl("colType"), cell: (row) => ttype(row.doc_type) },
    {
      key: "status",
      header: tl("colStatus"),
      cell: (row) =>
        row.current_version ? (
          <span className="flex flex-col items-start gap-1">
            <VersionStatusBadge version={row.current_version} />
            {row.status === "archived" ? (
              <span className="text-xs text-ink-muted">{tstatus("archived")}</span>
            ) : null}
          </span>
        ) : (
          tl("noVersion")
        ),
    },
    {
      key: "version",
      header: tl("colVersion"),
      cell: (row) => (row.current_version ? String(row.current_version.version_no) : "—"),
    },
    {
      key: "sensitivity",
      header: tl("colSensitivity"),
      cell: (row) => tsens(`${row.sensitivity}.short`),
    },
    {
      key: "visibility",
      header: tl("colVisibility"),
      cell: (row) => (row.acl.length === 0 ? tl("wholeSchool") : tl("limited")),
    },
    {
      key: "updated",
      header: tl("colUpdated"),
      cell: (row) => <Value>{formatDateTime(row.updated_at)}</Value>,
    },
  ];

  const canUpload = can(DOCUMENT_PERM.upload);
  const filtered = Boolean(filters.purpose || filters.docType || filters.status);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={canUpload ? <ButtonLink href="/documents/new">{t("upload")}</ButtonLink> : null}
      />
      {deleted ? (
        <Alert tone="success" live title={tl("deletedTitle")}>
          {tl("deletedBody")}
        </Alert>
      ) : null}
      <Card title={tl("filtersTitle")}>
        {/* GET form: filters live in the URL (codes only), work without JavaScript. */}
        <form method="get" className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SelectField
            name="doc_type"
            label={tl("filterType")}
            placeholder={tc("all")}
            defaultValue={filters.docType ?? ""}
            options={DOC_TYPES.map((value) => ({ value, label: ttype(value) }))}
          />
          <SelectField
            name="purpose"
            label={tl("filterPurpose")}
            placeholder={tc("all")}
            defaultValue={filters.purpose ?? ""}
            options={PURPOSES.map((value) => ({ value, label: tpurpose(value) }))}
          />
          <SelectField
            name="status"
            label={tl("filterStatus")}
            placeholder={tc("all")}
            defaultValue={filters.status ?? ""}
            options={DOC_STATUSES.map((value) => ({ value, label: tstatus(value) }))}
          />
          <div className="flex flex-wrap gap-3">
            <Button type="submit" variant="secondary">
              {tc("applyFilters")}
            </Button>
            {filtered ? (
              <Link href="/documents" className="inline-flex min-h-10 items-center underline">
                {tl("clearFilters")}
              </Link>
            ) : null}
          </div>
        </form>
      </Card>
      <div role="status" aria-live="polite" className="text-sm">
        {busy ? tl("updating") : null}
      </div>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={rows}
        rowKey={(row) => row.id}
        emptyTitle={filtered ? tl("emptyFilteredTitle") : tl("emptyTitle")}
        emptyBody={
          filtered ? tl("emptyFilteredBody") : canUpload ? tl("emptyBodyUploader") : tl("emptyBody")
        }
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
