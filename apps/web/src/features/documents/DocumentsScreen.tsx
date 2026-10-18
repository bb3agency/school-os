"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";
import { Button, ButtonLink } from "@/components/ui/Button";
import type { FilterChip } from "@/components/ui/FilterChips";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { Pager, useCursorStack } from "@/features/students/paging";
import { Link, useRouter } from "@/i18n/navigation";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formatBytes, formatDateTime } from "@/lib/format";
import type { Locale } from "@/i18n/routing";
import { useLocale } from "next-intl";
import type { Loadable } from "@/lib/loadable";
import { DOCUMENT_KEYS, useDocumentList } from "./data";
import type { DocumentListFilters } from "./filters";
import { VersionStatusBadge } from "./parts";
import {
  DOC_TYPES,
  DOCUMENT_PERM,
  fileKind,
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
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const pages = useCursorStack();
  const allowed = can(DOCUMENT_PERM.read);
  const list = useDocumentList(filters, pages.cursor, allowed);
  const router = useRouter();
  const queryClient = useQueryClient();

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

  const columns: ColumnV2<DocumentRow>[] = [
    {
      key: "title",
      header: tl("colTitle"),
      cell: (row) => {
        return (
          <span className="flex min-w-48 items-start gap-3">
            <span
              aria-hidden="true"
              className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary"
            >
              <Icon name="file" className="size-4" />
            </span>
            <span className="min-w-0 space-y-1">
              <Link
                href={`/documents/${row.id}`}
                className="block font-semibold text-primary underline underline-offset-4"
              >
                {row.title}
              </Link>
              <span className="block text-xs text-ink-muted">{ttype(row.doc_type)}</span>
            </span>
          </span>
        );
      },
    },
    {
      key: "file",
      header: tl("colFile"),
      cell: (row) =>
        row.current_version ? (
          <span className="flex flex-col items-start gap-1">
            <Pill variant="command">
              {fileKind(row.current_version.mime_type) ?? tl("otherKind")}
            </Pill>
            <span className="font-mono text-xs whitespace-nowrap text-ink-muted">
              {formatBytes(row.current_version.size_bytes, locale) ?? ""}
              {" · "}
              {tl("versionShort", { version: row.current_version.version_no })}
            </span>
          </span>
        ) : (
          <span className="text-sm text-ink-muted">{tl("noVersion")}</span>
        ),
    },
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
          <span className="text-sm text-ink-muted">{tl("noVersion")}</span>
        ),
    },
    {
      key: "visibility",
      header: tl("colVisibility"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <Pill variant="tag">
            <Icon name={row.acl.length === 0 ? "users" : "lock"} className="size-3" />
            {row.acl.length === 0 ? tl("wholeSchool") : tl("limited")}
          </Pill>
          <span className="text-xs text-ink-muted">{tsens(`${row.sensitivity}.short`)}</span>
        </span>
      ),
    },
    {
      key: "updated",
      header: tl("colUpdated"),
      cell: (row) => (
        <span className="num text-xs whitespace-nowrap">
          <Value>{formatDateTime(row.updated_at)}</Value>
        </span>
      ),
    },
  ];

  const canUpload = can(DOCUMENT_PERM.upload);
  const filtered = Boolean(filters.purpose || filters.docType || filters.status === "archived");

  // The active filters as chips. Removing one opens the list without that URL parameter (the
  // same as choosing "All" in the filter form and applying it).
  const chips: FilterChip[] = [
    ...(filters.docType
      ? [{ id: "doc_type", label: tl("filterType"), value: ttype(filters.docType) }]
      : []),
    ...(filters.purpose
      ? [{ id: "purpose", label: tl("filterPurpose"), value: tpurpose(filters.purpose) }]
      : []),
    ...(filters.status === "archived"
      ? [{ id: "status", label: tl("filterStatus"), value: tl("statusArchived") }]
      : []),
  ];
  const hrefWithout = (drop: string | null): string => {
    const params = new URLSearchParams();
    if (filters.docType && drop !== "doc_type") params.set("doc_type", filters.docType);
    if (filters.purpose && drop !== "purpose") params.set("purpose", filters.purpose);
    if (filters.status === "archived" && drop !== "status") params.set("status", "archived");
    const query = params.toString();
    return query ? `/documents?${query}` : "/documents";
  };
  const hasPages = pages.hasPrevious || Boolean(next);

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        actions={
          canUpload ? (
            <ButtonLink href="/documents/new">
              <Icon name="upload" className="size-4" />
              {t("upload")}
            </ButtonLink>
          ) : null
        }
      />
      {deleted ? (
        <Alert tone="success" live title={tl("deletedTitle")}>
          {tl("deletedBody")}
        </Alert>
      ) : null}
      <div role="status" aria-live="polite" className="text-sm text-ink-muted empty:hidden">
        {busy ? tl("updating") : null}
      </div>
      <DataTableV2
        id="documents"
        stacked
        caption={tl("libraryTitle")}
        captionHidden
        columns={columns}
        state={rows}
        rowKey={(row) => row.id}
        toolbar={
          /* GET form: filters live in the URL (codes only), work without JavaScript. */
          <form
            method="get"
            aria-label={tl("filtersTitle")}
            className="flex w-full flex-wrap items-end gap-3"
          >
            <SelectField
              name="doc_type"
              label={tl("filterType")}
              placeholder={tc("all")}
              defaultValue={filters.docType ?? ""}
              options={DOC_TYPES.map((value) => ({ value, label: ttype(value) }))}
              className="min-w-40 flex-1 sm:flex-none"
            />
            <SelectField
              name="purpose"
              label={tl("filterPurpose")}
              placeholder={tc("all")}
              defaultValue={filters.purpose ?? ""}
              options={PURPOSES.map((value) => ({ value, label: tpurpose(value) }))}
              className="min-w-40 flex-1 sm:flex-none"
            />
            {/* Without a status the API lists documents in use; archived ones only on request. */}
            <SelectField
              name="status"
              label={tl("filterStatus")}
              placeholder={tl("statusInUse")}
              defaultValue={filters.status === "archived" ? "archived" : ""}
              options={[{ value: "archived", label: tl("statusArchived") }]}
              className="min-w-40 flex-1 sm:flex-none"
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" variant="secondary">
                <Icon name="filter" className="size-4" />
                {tc("applyFilters")}
              </Button>
              {/* Kept next to the chips: it works without JavaScript and with a single filter. */}
              {filtered ? (
                <Link
                  href="/documents"
                  className="inline-flex min-h-10 items-center text-sm text-primary underline underline-offset-4"
                >
                  {tl("clearFilters")}
                </Link>
              ) : null}
            </div>
          </form>
        }
        chips={chips}
        onRemoveChip={(id) => router.replace(hrefWithout(id))}
        onClearFilters={() => router.replace("/documents")}
        emptyTitle={tl("emptyTitle")}
        emptyBody={canUpload ? tl("emptyBodyUploader") : tl("emptyBody")}
        noResultsTitle={tl("emptyFilteredTitle")}
        onRetry={() => void queryClient.invalidateQueries({ queryKey: DOCUMENT_KEYS.all })}
        footer={
          hasPages ? (
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
