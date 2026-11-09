"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { KpiCard } from "@/components/ui/StatCard";
import { Value } from "@/components/ui/Value";
import type { Locale } from "@/i18n/routing";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatCount, formatDate } from "@/lib/format";
import {
  READINESS_MANAGE,
  TODO_KEYS,
  fetchTodo,
  markTodo,
  reopenTodo,
  type TodoItem,
  type TodoState,
} from "./data";

type View = "open" | "closed";
type Change = { item: TodoItem; to: TodoState };

/**
 * The UDISE+ to-do list (FO-03; FR-UDI-031..FR-UDI-040): what the clerk must repeat in the
 * UDISE+ Student Module after something happened in SchoolOS (a TC issued, a child who left, an
 * admission, a promotion). SchoolOS never touches the portal: the clerk does the step there and
 * ticks it here.
 */
export function UdiseTodoScreen() {
  const t = useTranslations("udiseTodo");
  const tf = useTranslations("findings");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>("open");
  const list = useQuery({
    queryKey: TODO_KEYS.list,
    queryFn: () => fetchTodo(api),
    retry: false,
  });
  const change = useMutation({
    mutationFn: async ({ item, to }: Change): Promise<void> => {
      if (to === "open") await reopenTodo(api, item);
      else await markTodo(api, item, to);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: TODO_KEYS.list }),
  });
  const manage = can(READINESS_MANAGE);
  const count = (value: number | undefined) => formatCount(value, locale) ?? "0";
  const rows = (list.data?.items ?? []).filter((row) =>
    view === "open" ? row.state === "open" : row.state !== "open",
  );

  const columns: ColumnV2<TodoItem>[] = [
    {
      key: "what",
      stack: "title",
      header: t("colWhat"),
      className: "min-w-64",
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold text-ink">{t(`action.${row.action}`)}</span>
          {row.certificate_serial ? (
            <span className="font-mono text-xs text-ink-muted">
              {t("tcSerial", { serial: row.certificate_serial })}
            </span>
          ) : null}
          {row.progression ? (
            <span className="text-xs text-ink-muted">
              {t("progressionCounts", {
                promoted: count(row.progression.promoted),
                heldBack: count(row.progression.held_back),
                graduated: count(row.progression.graduated),
              })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "student",
      header: tf("colStudent"),
      cell: (row) =>
        row.student ? (
          <span className="block min-w-36">
            <span className="block font-semibold">
              <Value>{row.student.display_name}</Value>
            </span>
            <span className="block text-xs text-ink-muted">
              {[
                row.class_section,
                row.student.admission_no
                  ? tf("admissionNo", { number: row.student.admission_no })
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </span>
        ) : (
          <span className="text-ink-muted">{t("wholeSchool")}</span>
        ),
    },
    {
      key: "when",
      header: t("colWhen"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <span className="num text-sm whitespace-nowrap">{formatDate(row.event_on)}</span>
          {row.overdue ? (
            <Pill variant="negative" icon="alert">
              {t("waitingLong")}
            </Pill>
          ) : null}
          {row.state !== "open" ? <Pill variant="done">{t(`state.${row.state}`)}</Pill> : null}
        </span>
      ),
    },
    {
      key: "actions",
      stack: "actions",
      header: tc("actions"),
      className: "print:hidden",
      cell: (row) =>
        manage ? (
          <span className="flex flex-wrap gap-2">
            {row.state === "open" ? (
              <>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={change.isPending}
                  onClick={() => change.mutate({ item: row, to: "done" })}
                  aria-label={t("doneAria", { what: t(`action.${row.action}`) })}
                >
                  {t("markDone")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={change.isPending}
                  onClick={() => change.mutate({ item: row, to: "not_needed" })}
                >
                  {t("markNotNeeded")}
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                disabled={change.isPending}
                onClick={() => change.mutate({ item: row, to: "open" })}
              >
                {t("undo")}
              </Button>
            )}
          </span>
        ) : null,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tf("crumbHome"), href: "/" },
          { label: tf("title"), href: "/findings" },
          { label: t("title") },
        ]}
      />
      {list.data && !list.data.verified ? (
        <Alert tone="warning" title={t("unverifiedTitle")}>
          <p>{t("unverifiedBody")}</p>
        </Alert>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-3">
        <KpiCard
          label={t("kpiOpen")}
          value={list.data ? count(list.data.totals.open) : null}
          unavailableLabel={tc("notAvailable")}
        />
        <KpiCard
          label={t("kpiWaitingLong", { days: list.data?.overdue_after_days ?? 14 })}
          value={list.data ? count(list.data.totals.overdue) : null}
          unavailableLabel={tc("notAvailable")}
        />
        <KpiCard
          label={t("kpiDone")}
          value={list.data ? count(list.data.totals.done + list.data.totals.not_needed) : null}
          unavailableLabel={tc("notAvailable")}
        />
      </div>
      {change.isError ? <ApiErrorAlert error={change.error} /> : null}
      <SegmentedControl
        legend={t("view")}
        name="udise-todo-view"
        value={view}
        onValueChange={(value) => setView(value === "closed" ? "closed" : "open")}
        options={[
          { value: "open", label: t("viewOpen") },
          { value: "closed", label: t("viewClosed") },
        ]}
      />
      <DataTableV2
        id="udise-todo"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.item_key}
        emptyTitle={view === "open" ? t("emptyOpenTitle") : t("emptyClosedTitle")}
        emptyBody={view === "open" ? t("emptyOpenBody") : undefined}
        onRetry={() => void list.refetch()}
      />
    </div>
  );
}
