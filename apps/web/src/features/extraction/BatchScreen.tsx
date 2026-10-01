"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge, Pill, type PillVariant } from "@/components/ui/Badge";
import { ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Card, cardClasses } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { ProgressRing } from "@/components/ui/ProgressRing";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
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
  type ExtractionBatchDetail,
  type ExtractionItem,
  type ExtractionPage,
  type ItemStatus,
  type ItemStatusFilter,
} from "./types";

export const batchKey = (id: string) => [...EXTRACTION_KEY, "batch", id] as const;
export const itemsKey = (batchId: string) => [...EXTRACTION_KEY, "items", batchId] as const;

const itemPill: Record<ItemStatus, PillVariant> = {
  pending_review: "review",
  confirmed: "done",
  rejected: "tag",
};

export function ItemStatusBadge({ status }: { status: ItemStatus }) {
  const t = useTranslations("extraction.itemStatus");
  return <Pill variant={itemPill[status]}>{t(status)}</Pill>;
}

/** PRV-016: what happened to a page that showed a full Aadhaar number. */
export function PageAadhaarState({ page }: { page: ExtractionPage }) {
  const t = useTranslations("extraction.pages");
  if (page.image_withheld) return <Pill variant="negative">{t("withheld")}</Pill>;
  if (page.image_redacted) return <Badge tone="info">{t("redacted")}</Badge>;
  return <Value>{null}</Value>;
}

const pagePill: Record<ExtractionPage["status"], PillVariant> = {
  queued: "progress",
  done: "done",
  failed: "negative",
};

function PagesCard({ batch }: { batch: ExtractionBatchDetail }) {
  const t = useTranslations("extraction.pages");
  const tf = useTranslations("extraction.pageFailure");
  const locale = useLocale() as Locale;
  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const columns: Column<ExtractionPage>[] = [
    {
      key: "page",
      header: t("colPage"),
      cell: (row) => <span className="font-mono text-xs text-ink-muted">{count(row.seq)}</span>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <Pill variant={pagePill[row.status]}>{t(`status.${row.status}`)}</Pill>
          {row.status === "failed" && row.error_code ? (
            <span className="text-xs text-ink-muted">
              {translateOr(tf, row.error_code, "generic")}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "rows",
      header: t("colRows"),
      numeric: true,
      cell: (row) => <span className="tabular-nums">{count(row.row_count)}</span>,
    },
    {
      key: "low",
      header: t("colLowConfidence"),
      cell: (row) =>
        row.low_confidence_count > 0 ? (
          <Badge tone="warning">{count(row.low_confidence_count)}</Badge>
        ) : (
          <span className="tabular-nums">{count(row.low_confidence_count)}</span>
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

/**
 * The lowest reading certainty among a row's values, as a percentage, or null when the reader
 * gave no certainty for any value (the ring is then left out).
 */
export function lowestConfidence(item: Pick<ExtractionItem, "fields">): number | null {
  const values = Object.values(item.fields)
    .map((field) => field.confidence)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (values.length === 0) return null;
  return Math.round(Math.min(...values) * 100);
}

/** One row of the verification queue as a card: where, what was read, certainty, open. */
function QueueItem({ row, batch }: { row: ExtractionItem; batch: ExtractionBatchDetail }) {
  const t = useTranslations("extraction.queue");
  const where = t("where", { page: pageNumber(batch.pages, row.page_id), row: row.row_index + 1 });
  const lowest = lowestConfidence(row);
  const pending = row.status === "pending_review";
  return (
    <li className={`${cardClasses({ padding: "sm", tone: "outline" })} flex flex-col gap-3`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Eyebrow as="span">{where}</Eyebrow>
        <ItemStatusBadge status={row.status} />
      </div>
      <div className="flex items-start justify-between gap-3">
        <dl className="min-w-0 space-y-2 text-sm">
          <div>
            <dt className="text-ink-muted">{t("colName")}</dt>
            <dd className="font-medium break-words text-ink">
              <Value>{fieldText(row, "full_name")}</Value>
            </dd>
          </div>
          <div>
            <dt className="text-ink-muted">{t("colAdmissionNo")}</dt>
            <dd className="font-mono break-words text-ink">
              <Value>{fieldText(row, "admission_no")}</Value>
            </dd>
          </div>
        </dl>
        {lowest !== null ? (
          <ProgressRing
            value={lowest}
            size="sm"
            color={row.low_confidence ? 2 : 3}
            label={t("lowestLabel", { where })}
          />
        ) : null}
      </div>
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
        {row.low_confidence ? (
          <Badge tone="warning">
            <Icon name="alert" className="size-3.5" />
            {t("lowConfidence", { count: row.low_confidence_fields.length })}
          </Badge>
        ) : (
          <span />
        )}
        <Link
          href={`/register-photos/items/${row.id}`}
          className={buttonClasses(pending ? "primary" : "secondary", "sm")}
        >
          {pending ? t("review") : t("view")}
          <span className="sr-only"> {where}</span>
        </Link>
      </div>
    </li>
  );
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

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3" data-print="hide">
          <SelectField
            className="w-full max-w-xs"
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
        {list.status !== "ready" ? (
          <LoadGate state={list} />
        ) : list.data.length === 0 ? (
          <EmptyState
            icon="checkCircle"
            title={filter === "pending_review" ? t("emptyPendingTitle") : t("emptyTitle")}
            body={filter === "pending_review" ? t("emptyPendingBody") : undefined}
          />
        ) : (
          <ul aria-label={t("title")} className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {list.data.map((row) => (
              <QueueItem key={row.id} row={row} batch={batch} />
            ))}
          </ul>
        )}
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
  const tl = useTranslations("extraction.list");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const crumbs = [
    { label: tn("home"), href: "/" },
    { label: tl("title"), href: "/register-photos" },
  ];
  if (batch.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("loadingTitle")}
          breadcrumb={[...crumbs, { label: t("loadingTitle") }]}
        />
        <LoadGate state={batch} />
      </div>
    );
  }
  const data = batch.data;
  const count = (value: number) => formatCount(value, locale) ?? String(value);
  const busy = BATCH_BUSY.has(data.status);
  const title = t("title", { date: formatDateTime(data.created_at) ?? "" });
  const percentRead = data.page_count > 0 ? (data.pages_done / data.page_count) * 100 : null;
  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        breadcrumb={[...crumbs, { label: title }]}
        badge={<BatchStatusBadge status={data.status} />}
        description={t(`next.${data.status}`)}
        actions={
          data.items_pending > 0 && permissions.has(PERM.importRun) ? (
            <ButtonLink href={`/register-photos/${data.id}/next`}>
              {t("start")}
              <Icon name="arrowRight" className="size-4" />
            </ButtonLink>
          ) : null
        }
      />
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
      <div className="grid gap-4 lg:grid-cols-[auto_1fr]">
        {percentRead !== null ? (
          <div className={`${cardClasses({ padding: "sm" })} flex items-center gap-4`}>
            <ProgressRing value={percentRead} size="lg" color={1} label={t("stat.pages")} />
            <div>
              <Eyebrow>{t("stat.pages")}</Eyebrow>
              <p className="mt-1 font-display text-3xl text-ink tabular-nums">
                {t("statPages", { done: count(data.pages_done), total: count(data.page_count) })}
              </p>
            </div>
          </div>
        ) : null}
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {(
            [
              ["rows", data.items_total],
              ["pending", data.items_pending],
              ["confirmed", data.items_confirmed],
              ["low", data.items_low_confidence],
            ] as const
          ).map(([key, value]) => (
            <StatCard
              key={key}
              label={t(`stat.${key}`)}
              value={count(value)}
              unavailableLabel={tc("notAvailable")}
            />
          ))}
        </dl>
      </div>
      <QueueCard batch={data} />
      <PagesCard batch={data} />
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
