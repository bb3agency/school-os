"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Pill } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { KEYS, READ, type Circular, useCirculars } from "./data";
import { ReadingPill } from "./parts";

type Show = "open" | "all";

/** Still needs a person: not read yet, reading failed, or suggestions not decided / not reviewed. */
export function needsAttention(circular: Circular): boolean {
  return !circular.reviewed;
}

/**
 * Circulars inbox (US-1601, US-1602): every circular the user can see, where its AI reading
 * stands and what is left to decide. Opening one shows the summary with source chips and the
 * suggested deadlines to confirm or dismiss.
 */
export function CircularsScreen() {
  const t = useTranslations("circulars");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(READ);
  const [show, setShow] = useState<Show>("open");
  const list = useCirculars(undefined, allowed);

  const rows: Loadable<readonly Circular[]> = !meLoaded
    ? { status: "loading" }
    : list.status === "ready"
      ? {
          status: "ready",
          data: show === "open" ? list.data.data.filter(needsAttention) : list.data.data,
        }
      : list;

  const columns: ColumnV2<Circular>[] = [
    {
      key: "circular",
      header: t("columns.circular"),
      cell: (row) => (
        <>
          <Link
            href={`/circulars/${row.document_id}`}
            className="font-semibold break-words text-primary underline underline-offset-4 hover:no-underline"
          >
            {row.title}
          </Link>
          {row.issuer ? <p className="text-xs text-ink-muted">{row.issuer}</p> : null}
        </>
      ),
    },
    {
      key: "issuedOn",
      header: t("columns.issuedOn"),
      className: "num whitespace-nowrap",
      cell: (row) => <Value>{formatDate(row.issued_on)}</Value>,
    },
    {
      key: "reading",
      header: t("columns.reading"),
      cell: (row) => (
        <span className="flex flex-wrap gap-1">
          <ReadingPill status={row.reading_status} />
          {row.reviewed ? <Pill variant="positive">{t("reviewed")}</Pill> : null}
        </span>
      ),
    },
    {
      key: "toDecide",
      header: t("columns.toDecide"),
      numeric: true,
      cell: (row) => row.open_suggestions,
    },
    { key: "tasks", header: t("columns.tasks"), numeric: true, cell: (row) => row.tasks },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<ButtonLink href="/documents/new">{t("upload")}</ButtonLink>}
      />
      {meLoaded && !allowed ? (
        <EmptyState icon="lock" title={t("noAccessTitle")} body={t("noAccessBody")} />
      ) : (
        <DataTableV2
          id="circulars"
          caption={t("inboxTitle")}
          columns={columns}
          state={rows}
          rowKey={(row) => row.document_id}
          stacked
          toolbar={
            <SegmentedControl
              legend={t("showLabel")}
              size="sm"
              value={show}
              onValueChange={(value) => setShow(value as Show)}
              options={[
                { value: "open", label: t("showOpen") },
                { value: "all", label: t("showAll") },
              ]}
            />
          }
          emptyTitle={show === "open" ? t("emptyOpenTitle") : t("emptyTitle")}
          emptyBody={t("emptyBody")}
          onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.circulars })}
        />
      )}
    </div>
  );
}
