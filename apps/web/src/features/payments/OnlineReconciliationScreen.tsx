"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextAreaField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import { Amount, FeesGate, NoAccess } from "@/features/fees/parts";
import {
  FINANCE_READ,
  ITEM_KINDS,
  ITEM_STATUSES,
  KEYS,
  MANAGE,
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
  const summary = useReconSummary();
  if (summary.status !== "ready") return null;
  const s = summary.data;
  const last = s.runs[0];
  return (
    <Card title={t("summaryTitle")}>
      {!s.connected ? (
        <Alert tone="info" title={t("notConnectedTitle")}>
          {t("notConnectedBody")}
        </Alert>
      ) : null}
      <dl className="grid gap-4 sm:grid-cols-4">
        <div>
          <dt className="text-sm text-ink-muted">{t("open")}</dt>
          <dd className="text-2xl font-semibold tabular-nums">{s.open_count}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("waiting")}</dt>
          <dd className="text-2xl font-semibold tabular-nums">{s.pending_events}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("failedEvents")}</dt>
          <dd className="text-2xl font-semibold tabular-nums">{s.failed_events}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("lastRun")}</dt>
          <dd>
            {last
              ? t("lastRunValue", {
                  date: formatDate(last.run_date) ?? "",
                  status: t(`runStatuses.${last.status}`),
                })
              : t("noRun")}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

/**
 * Online reconciliation (US-2111; FR-PAY-009, FR-PAY-010; docs/17 §5.8): every online payment
 * that did not become a receipt the normal way, each morning's comparison with Razorpay's
 * payments, refunds and settlements, and webhook events that could not be applied. Nothing is
 * changed automatically: a person explains each item with a note (`fee.manage`); a late webhook
 * that issues the receipt resolves its item by itself.
 */
export function OnlineReconciliationScreen() {
  const t = useTranslations("payments.recon");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([FINANCE_READ, MANAGE]);
  const canExplain = can(MANAGE);
  const [status, setStatus] = useState<ItemStatus | "">("open");
  const [kind, setKind] = useState<ItemKind | "">("");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const list = useReconciliation(status, kind, cursor);
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (me !== undefined && !allowed) {
    return (
      <div className="space-y-6">
        {header}
        <NoAccess />
      </div>
    );
  }
  return (
    <div className="space-y-6">
      {header}
      <Summary />
      <form
        role="search"
        className="grid gap-4 md:grid-cols-2"
        onSubmit={(e) => e.preventDefault()}
      >
        <SelectField
          name="status"
          label={t("filterStatus")}
          placeholder={t("any")}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as ItemStatus | "");
            setCursors([null]);
          }}
          options={ITEM_STATUSES.map((s) => ({ value: s, label: t(`statuses.${s}`) }))}
        />
        <SelectField
          name="kind"
          label={t("filterKind")}
          placeholder={t("any")}
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as ItemKind | "");
            setCursors([null]);
          }}
          options={ITEM_KINDS.map((k) => ({ value: k, label: t(`kinds.${k}`) }))}
        />
      </form>
      <FeesGate data={list}>
        {(page) => (
          <Card title={t("listTitle")}>
            {page.items.length === 0 ? (
              <EmptyState icon="check" title={t("emptyTitle")} body={t("emptyBody")} />
            ) : (
              <>
                <TableScroll label={t("listTitle")}>
                  <Table>
                    <THead>
                      <Tr>
                        <Th>{t("columns.found")}</Th>
                        <Th>{t("columns.kind")}</Th>
                        <Th>{t("columns.payment")}</Th>
                        <Th>{t("columns.receipt")}</Th>
                        <Th className="text-right">{t("columns.amount")}</Th>
                        <Th>{t("columns.status")}</Th>
                        <Th>{t("columns.action")}</Th>
                      </Tr>
                    </THead>
                    <TBody>
                      {page.items.map((item) => (
                        <Tr key={item.id}>
                          <Td>{formatDateTime(item.created_at)}</Td>
                          <Td>
                            <span className="font-semibold">{t(`kinds.${item.kind}`)}</span>
                            <span className="block text-xs text-ink-muted">
                              {t(`reasons.${item.reason_code}` as "reasons.no_receipt")}
                            </span>
                          </Td>
                          <Td>
                            <code className="font-mono text-xs">
                              {item.provider_payment_id ?? "—"}
                            </code>
                            {item.intent_code ? (
                              <span className="block text-xs text-ink-muted">
                                {t("link", { code: item.intent_code })}
                              </span>
                            ) : null}
                          </Td>
                          <Td>
                            {item.receipt_id ? (
                              <Link
                                href={`/fees/receipts/${item.receipt_id}`}
                                className="text-primary underline underline-offset-4"
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
                            )}
                          </Td>
                          <Td numeric>
                            <Amount value={item.amount} />
                            {item.expected_amount && item.expected_amount !== item.amount ? (
                              <span className="block text-xs text-ink-muted">
                                {t("expected")} <Amount value={item.expected_amount} />
                              </span>
                            ) : null}
                          </Td>
                          <Td>
                            <Pill variant={STATUS_PILL[item.status] ?? "tag"}>
                              {t(`statuses.${item.status}`)}
                            </Pill>
                            {item.note ? (
                              <span className="block max-w-xs text-xs">
                                {item.note}
                                {item.explained_by_name ? ` — ${item.explained_by_name}` : ""}
                              </span>
                            ) : null}
                          </Td>
                          <Td>
                            {item.status === "open" && canExplain ? (
                              <ExplainButton item={item} />
                            ) : null}
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </TableScroll>
                <div className="mt-4 flex gap-3">
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
              </>
            )}
          </Card>
        )}
      </FeesGate>
    </div>
  );
}
