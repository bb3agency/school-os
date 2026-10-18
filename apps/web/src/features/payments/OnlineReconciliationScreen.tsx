"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { FilterChip } from "@/components/ui/FilterChips";
import { TextAreaField } from "@/components/ui/Input";
import { Menu } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";
import { isFeesOff } from "@/features/fees/data";
import { Amount, FeesGate, NoAccess } from "@/features/fees/parts";
import {
  FINANCE_READ,
  ITEM_KINDS,
  ITEM_STATUSES,
  KEYS,
  RECONCILE,
  NOTE_MAX,
  explainSchema,
  ifMatch,
  useReconSummary,
  useReconciliation,
  type ItemKind,
  type ItemStatus,
  type ReconItem,
} from "./data";

const STATUS_PILL: Record<string, PillVariant> = {
  open: "negative",
  explained: "done",
  resolved: "positive",
};

function ExplainButton({ item }: { item: ReconItem }) {
  const t = useTranslations("payments.recon");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("explain")}
      triggerSize="sm"
      triggerDescription={t("explainFor", { kind: t(`kinds.${item.kind}`) })}
      title={t("explainTitle")}
      description={t("explainBody")}
      confirmLabel={t("explain")}
      consequence={t("explainConsequence")}
      schema={explainSchema}
      invalidate={[KEYS.all]}
      errorNamespace="payments"
      submit={(input) =>
        unwrap(
          api.POST("/api/v1/fees/reconciliation/online/items/{recon_item_id}/explain", {
            params: { path: { recon_item_id: item.id } },
            headers: { "If-Match": ifMatch(item.version) },
            body: { note: input.note },
          }),
        )
      }
    >
      {(errors) => (
        <TextAreaField
          name="note"
          label={t("note")}
          hint={t("noteHint")}
          error={errors.note}
          maxLength={NOTE_MAX}
          rows={3}
        />
      )}
    </ActionDialog>
  );
}

function Summary() {
  const t = useTranslations("payments.recon");
  const tc = useTranslations("common");
  const locale = useLocale() as Locale;
  const summary = useReconSummary();
  if (summary.status !== "ready") return null;
  const s = summary.data;
  const last = s.runs[0];
  return (
    <section aria-label={t("summaryTitle")} className="space-y-3">
      {!s.connected ? (
        <Alert tone="info" title={t("notConnectedTitle")}>
          {t("notConnectedBody")}
        </Alert>
      ) : null}
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label={t("open")}
          value={formatCount(s.open_count, locale)}
          unavailableLabel={tc("notAvailable")}
        />
        <StatCard
          label={t("waiting")}
          value={formatCount(s.pending_events, locale)}
          unavailableLabel={tc("notAvailable")}
        />
        <StatCard
          label={t("failedEvents")}
          value={formatCount(s.failed_events, locale)}
          unavailableLabel={tc("notAvailable")}
        />
        <StatCard
          label={t("lastRun")}
          value={last ? (formatDate(last.run_date) ?? null) : t("noRun")}
          unavailableLabel={tc("notAvailable")}
          hint={last ? t(`runStatuses.${last.status}`) : undefined}
        />
      </dl>
    </section>
  );
}

/**
 * Online reconciliation (US-2111; FR-PAY-009, FR-PAY-010; docs/17 §5.8): every online payment
 * that did not become a receipt the normal way, each morning's comparison with Razorpay's
 * payments, refunds and settlements, and webhook events that could not be applied. Nothing is
 * changed automatically: a person explains each item with a note (`fee.reconcile`); a late webhook
 * that issues the receipt resolves its item by itself.
 */
export function OnlineReconciliationScreen() {
  const t = useTranslations("payments.recon");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const tu = useTranslations("ui");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([RECONCILE, FINANCE_READ]);
  const canExplain = can(RECONCILE);
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ItemStatus | "">("open");
  const [kind, setKind] = useState<ItemKind | "">("");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const list = useReconciliation(status, kind, cursor);
  const columns = useMemo<ColumnV2<ReconItem>[]>(
    () => [
      {
        key: "found",
        name: t("columns.found"),
        header: t("columns.found"),
        hideable: false,
        cell: (item) => (
          <span className="num whitespace-nowrap">{formatDateTime(item.created_at)}</span>
        ),
      },
      {
        key: "kind",
        name: t("columns.kind"),
        header: t("columns.kind"),
        stack: "wide",
        cell: (item) => (
          <>
            <span className="font-semibold">{t(`kinds.${item.kind}`)}</span>
            <span className="block text-xs text-ink-muted">
              {t(`reasons.${item.reason_code}` as "reasons.no_receipt")}
            </span>
          </>
        ),
      },
      {
        key: "payment",
        name: t("columns.payment"),
        header: t("columns.payment"),
        cell: (item) => (
          <>
            <code className="font-mono text-xs whitespace-nowrap">
              {item.provider_payment_id ?? "—"}
            </code>
            {item.intent_code ? (
              <span className="block text-xs whitespace-nowrap text-ink-muted">
                {t("link", { code: item.intent_code })}
              </span>
            ) : null}
          </>
        ),
      },
      {
        key: "receipt",
        name: t("columns.receipt"),
        header: t("columns.receipt"),
        cell: (item) =>
          item.receipt_id ? (
            <Link
              href={`/fees/receipts/${item.receipt_id}`}
              className="num font-mono whitespace-nowrap text-primary underline underline-offset-4"
            >
              {item.receipt_number ?? t("receipt")}
            </Link>
          ) : item.account_id ? (
            <Link
              href={`/fees/accounts/${item.account_id}`}
              className="text-primary underline underline-offset-4"
            >
              {t("account")}
            </Link>
          ) : (
            "—"
          ),
      },
      {
        key: "amount",
        name: t("columns.amount"),
        header: t("columns.amountInr"),
        numeric: true,
        cell: (item) => (
          <>
            <Amount value={item.amount} />
            {item.expected_amount && item.expected_amount !== item.amount ? (
              <span className="block text-xs text-ink-muted">
                {t("expected")} <Amount value={item.expected_amount} />
              </span>
            ) : null}
          </>
        ),
      },
      {
        key: "status",
        name: t("columns.status"),
        header: t("columns.status"),
        stack: "wide",
        cell: (item) => (
          <>
            <Pill variant={STATUS_PILL[item.status] ?? "tag"}>{t(`statuses.${item.status}`)}</Pill>
            {item.note ? (
              <span className="block max-w-xs text-xs break-words">
                {item.note}
                {item.explained_by_name ? ` — ${item.explained_by_name}` : ""}
              </span>
            ) : null}
          </>
        ),
      },
      {
        key: "action",
        name: t("columns.action"),
        header: t("columns.action"),
        hideable: false,
        stack: "actions",
        cell: (item) =>
          item.status === "open" && canExplain ? <ExplainButton item={item} /> : null,
      },
    ],
    [t, canExplain],
  );
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        me !== undefined && canExplain ? (
          <Menu
            label={tu("moreActions")}
            variant="button"
            trigger={tu("more")}
            align="end"
            entries={[
              {
                kind: "link",
                id: "bank",
                label: t("bankLink"),
                href: "/fees/reconcile",
                icon: "building",
              },
            ]}
          />
        ) : null
      }
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-5">
        {header}
        <NoAccess />
      </div>
    );
  }
  const resetTo = (next: { status?: ItemStatus | ""; kind?: ItemKind | "" }) => {
    if (next.status !== undefined) setStatus(next.status);
    if (next.kind !== undefined) setKind(next.kind);
    setCursors([null]);
  };
  const chips: FilterChip[] = [
    ...(status ? [{ id: "status", label: t("filterStatus"), value: t(`statuses.${status}`) }] : []),
    ...(kind ? [{ id: "kind", label: t("filterKind"), value: t(`kinds.${kind}`) }] : []),
  ];
  const page = list.status === "ready" ? list.data : null;
  return (
    <div className="space-y-5">
      {header}
      <Summary />
      {isFeesOff(list) ? (
        <FeesGate data={list}>{() => null}</FeesGate>
      ) : (
        <DataTableV2
          id="payments-online-recon"
          caption={t("listTitle")}
          columns={columns}
          state={list.status === "ready" ? { status: "ready", data: list.data.items } : list}
          rowKey={(item) => item.id}
          toolbar={
            <form
              role="search"
              aria-label={t("filterLabel")}
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => e.preventDefault()}
            >
              <SelectField
                name="status"
                label={t("filterStatus")}
                placeholder={t("any")}
                value={status}
                onChange={(e) => resetTo({ status: e.target.value as ItemStatus | "" })}
                options={ITEM_STATUSES.map((s) => ({ value: s, label: t(`statuses.${s}`) }))}
              />
              <SelectField
                name="kind"
                label={t("filterKind")}
                placeholder={t("any")}
                value={kind}
                onChange={(e) => resetTo({ kind: e.target.value as ItemKind | "" })}
                options={ITEM_KINDS.map((k) => ({ value: k, label: t(`kinds.${k}`) }))}
              />
            </form>
          }
          chips={chips}
          onRemoveChip={(id) => resetTo(id === "status" ? { status: "" } : { kind: "" })}
          onClearFilters={() => resetTo({ status: "", kind: "" })}
          emptyTitle={t("emptyTitle")}
          emptyBody={t("emptyBody")}
          noResultsTitle={t("noResultsTitle")}
          onRetry={() => void queryClient.invalidateQueries({ queryKey: KEYS.all })}
          stacked
          {...(page && (cursors.length > 1 || page.next_cursor)
            ? {
                footer: (
                  <div className="flex flex-wrap justify-end gap-3">
                    {cursors.length > 1 ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setCursors((c) => c.slice(0, -1))}
                      >
                        {tf("previous")}
                      </Button>
                    ) : null}
                    {page.next_cursor ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setCursors((c) => [...c, page.next_cursor ?? null])}
                      >
                        {tf("next")}
                      </Button>
                    ) : null}
                  </div>
                ),
              }
            : {})}
        />
      )}
    </div>
  );
}
