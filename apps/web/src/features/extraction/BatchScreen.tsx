"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { POLL_MS, usePolledQuery } from "@/features/imports/poll";
import { PERM, useStaffPermissions, type Permissions } from "@/features/students/me";
import { Pager, useCursorStack } from "@/features/students/paging";
import { LoadGate } from "@/features/students/parts";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatCount, formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import type { Loadable } from "@/lib/loadable";
import { BatchStatusBadge, EXTRACTION_KEY } from "./RegisterPhotosScreen";
import {
  BATCH_BUSY,
  ITEM_STATUSES,
  itemTone,
  type ExtractionBatchDetail,
  type ExtractionItem,
  type ExtractionPage,
  type ItemStatus,
  type ItemStatusFilter,
} from "./types";

export const batchKey = (id: string) => [...EXTRACTION_KEY, "batch", id] as const;
export const itemsKey = (batchId: string) => [...EXTRACTION_KEY, "items", batchId] as const;

export function ItemStatusBadge({ status }: { status: ItemStatus }) {
  const t = useTranslations("extraction.itemStatus");
  return <Badge tone={itemTone[status]}>{t(status)}</Badge>;
}

/** PRV-016: what happened to a page that showed a full Aadhaar number. */
export function PageAadhaarState({ page }: { page: ExtractionPage }) {
  const t = useTranslations("extraction.pages");
  if (page.image_withheld) return <Badge tone="danger">{t("withheld")}</Badge>;
  if (page.image_redacted) return <Badge tone="info">{t("redacted")}</Badge>;
  return <Value>{null}</Value>;
}

function PagesCard({ batch }: { batch: ExtractionBatchDetail }) {
  const t = useTranslations("extraction.pages");
  const tf = useTranslations("extraction.pageFailure");
  const locale = useLocale() as Locale;
  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const columns: Column<ExtractionPage>[] = [
    { key: "page", header: t("colPage"), cell: (row) => count(row.seq) },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="space-y-1">
          <Badge
            tone={row.status === "failed" ? "danger" : row.status === "done" ? "success" : "info"}
          >
            {t(`status.${row.status}`)}
          </Badge>
          {row.status === "failed" && row.error_code ? (
            <span className="block text-xs">{translateOr(tf, row.error_code, "generic")}</span>
          ) : null}
        </span>
      ),
    },
    { key: "rows", header: t("colRows"), cell: (row) => count(row.row_count) },
    {
      key: "low",
      header: t("colLowConfidence"),
      cell: (row) =>
        row.low_confidence_count > 0 ? (
          <Badge tone="warning">{count(row.low_confidence_count)}</Badge>
        ) : (
          count(row.low_confidence_count)
        ),
    },
    { key: "aadhaar", header: t("colAadhaar"), cell: (row) => <PageAadhaarState page={row} /> },
  ];
  return (
    <Card title={t("title")} description={t("description")}>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={{ status: "ready", data: batch.pages }}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
      />
    </Card>
  );
}

const FILTERS = ["pending_review", "confirmed", "rejected", "all"] as const;
type FilterChoice = (typeof FILTERS)[number];

function isFilter(value: string): value is ItemStatusFilter {
  return (ITEM_STATUSES as readonly string[]).includes(value);
}

/** Admission number and name of a row as read (already masked by the API where needed). */
function fieldText(item: ExtractionItem, key: string): string | null {
  return item.fields[key]?.value ?? null;
}

/** Photo number in the batch (every photo is page 1 of its own document, so use `seq`). */
export function pageNumber(pages: readonly ExtractionPage[], pageId: string): number {
  return pages.find((page) => page.id === pageId)?.seq ?? 1;
}

function QueueCard({ batch }: { batch: ExtractionBatchDetail }) {
  const batchId = batch.id;
  const version = batch.version;
  const t = useTranslations("extraction.queue");
  const api = useBffClient("staff");
  const [filter, setFilter] = useState<FilterChoice>("pending_review");
  const pages = useCursorStack();
  const query = {
    batch_id: batchId,
    limit: 50,
    ...(isFilter(filter) ? { status: filter } : {}),
    ...(pages.cursor ? { cursor: pages.cursor } : {}),
  };
  const items = useApiQuery([...itemsKey(batchId), version, query], () =>
    unwrap(api.GET("/api/v1/extraction-items", { params: { query } })),
  );
  const list: Loadable<readonly ExtractionItem[]> =
    items.status === "ready" ? { status: "ready", data: items.data.data } : items;
  const next = items.status === "ready" ? items.data.next_cursor : null;

  const columns: Column<ExtractionItem>[] = [
    {
      key: "where",
      header: t("colWhere"),
      cell: (row) =>
        t("where", { page: pageNumber(batch.pages, row.page_id), row: row.row_index + 1 }),
    },
    {
      key: "admission",
      header: t("colAdmissionNo"),
      cell: (row) => <Value>{fieldText(row, "admission_no")}</Value>,
    },
    {
      key: "name",
      header: t("colName"),
      cell: (row) => <Value>{fieldText(row, "full_name")}</Value>,
    },
    {
      key: "check",
      header: t("colCheck"),
      cell: (row) =>
        row.low_confidence ? (
          <Badge tone="warning">
            {t("lowConfidence", { count: row.low_confidence_fields.length })}
          </Badge>
        ) : (
          <Value>{null}</Value>
        ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <ItemStatusBadge status={row.status} />,
    },
    {
      key: "open",
      header: t("colOpen"),
      cell: (row) => (
        <Link
          href={`/register-photos/items/${row.id}`}
          className="font-semibold text-primary underline"
        >
          {row.status === "pending_review" ? t("review") : t("view")}
          <span className="sr-only">
            {" "}
            {t("where", { page: pageNumber(batch.pages, row.page_id), row: row.row_index + 1 })}
          </span>
        </Link>
      ),
    },
  ];

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-3">
        <div className="max-w-xs" data-print="hide">
          <SelectField
            label={t("filter")}
            value={filter}
            onChange={(event) => {
              const value = FILTERS.find((item) => item === event.currentTarget.value);
              pages.reset();
              setFilter(value ?? "pending_review");
            }}
            options={FILTERS.map((value) => ({ value, label: t(`filters.${value}`) }))}
          />
        </div>
        <DataTable
          caption={t("title")}
          captionHidden
          columns={columns}
          state={list}
          rowKey={(row) => row.id}
          emptyTitle={filter === "pending_review" ? t("emptyPendingTitle") : t("emptyTitle")}
          emptyBody={filter === "pending_review" ? t("emptyPendingBody") : undefined}
        />
        <Pager
          label={t("pagesLabel")}
          page={pages.page}
          onPrevious={pages.hasPrevious ? pages.previous : undefined}
          onNext={next ? () => pages.next(next) : undefined}
        />
      </div>
    </Card>
  );
}

export interface BatchViewProps {
  batch: Loadable<ExtractionBatchDetail>;
  permissions: Permissions;
}

/** US-402 AC4: progress per page and the verification queue of one batch. */
export function BatchView({ batch, permissions }: BatchViewProps) {
  const t = useTranslations("extraction.batch");
  const tf = useTranslations("extraction.batchFailure");
  const locale = useLocale() as Locale;
  if (batch.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader title={t("loadingTitle")} />
        <LoadGate state={batch} />
      </div>
    );
  }
  const data = batch.data;
  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const busy = BATCH_BUSY.has(data.status);
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title", { date: formatDateTime(data.created_at) ?? "" })}
        badge={<BatchStatusBadge status={data.status} />}
        description={t(`next.${data.status}`)}
        actions={
          data.items_pending > 0 && permissions.has(PERM.importRun) ? (
            <ButtonLink href={`/register-photos/${data.id}/next`}>{t("start")}</ButtonLink>
          ) : null
        }
      />
      <nav aria-label={t("relatedLabel")} data-print="hide">
        <Link href="/register-photos" className="text-sm text-primary underline">
          {t("backToList")}
        </Link>
      </nav>
      <div role="status" aria-live="polite">
        {busy ? (
          <Alert tone="info" title={t("busyTitle")}>
            {t("busyBody", { done: count(data.pages_done), total: count(data.page_count) })}
          </Alert>
        ) : null}
      </div>
      {data.status === "failed" ? (
        <Alert tone="danger" title={t("failedTitle")}>
          {translateOr(tf, data.error_code ?? "generic", "generic")}
        </Alert>
      ) : null}
      {data.pages_withheld > 0 ? (
        <Alert tone="warning" title={t("withheldTitle", { count: data.pages_withheld })}>
          {t("withheldBody")}
        </Alert>
      ) : null}
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {(
          [
            [
              "pages",
              t("statPages", { done: count(data.pages_done), total: count(data.page_count) }),
            ],
            ["rows", count(data.items_total)],
            ["pending", count(data.items_pending)],
            ["confirmed", count(data.items_confirmed)],
            ["low", count(data.items_low_confidence)],
          ] as const
        ).map(([key, value]) => (
          <div key={key} className="rounded-md border border-border p-3">
            <dt className="text-sm text-ink-muted">{t(`stat.${key}`)}</dt>
            <dd className="text-xl font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
      <PagesCard batch={data} />
      <QueueCard batch={data} />
    </div>
  );
}

/** GET /extraction-batches/{id}, asked again every few seconds while pages are being read. */
export function BatchScreen({ batchId }: { batchId: string }) {
  const api = useBffClient("staff");
  const permissions = useStaffPermissions();
  const batch = usePolledQuery(
    batchKey(batchId),
    () =>
      unwrap(
        api.GET("/api/v1/extraction-batches/{batch_id}", {
          params: { path: { batch_id: batchId } },
        }),
      ),
    (data) => (data && BATCH_BUSY.has(data.status) ? POLL_MS : false),
  );
  return <BatchView batch={batch} permissions={permissions} />;
}
