"use client";

import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import * as z from "@/lib/zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatInr } from "@/lib/format";
import {
  CHEQUE_KEYS,
  CHEQUE_READERS,
  CHEQUE_STATUSES,
  chequeApi,
  depositable,
  depositOutSchema,
  DEPOSIT_SLIP_PATH,
  parsed,
  returnable,
  useCheques,
  type Cheque,
  type ChequeStatus,
  type DepositOut,
} from "./cheques";
import { REASON_MAX } from "./data";
import { FeesGate, NoAccess, PrintLink } from "./parts";

const STATUS_PILL: Record<ChequeStatus, PillVariant> = {
  received: "review",
  deposited: "progress",
  cleared: "done",
  returned: "sample",
};

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, { error: "invalidDate" });

function DepositDialog({
  chosen,
  today,
  onDone,
}: {
  chosen: readonly Cheque[];
  today: string;
  onDone: (text: string) => void;
}) {
  const t = useTranslations("fees.cheques");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("markDeposited", { count: chosen.length })}
      triggerVariant="primary"
      triggerDisabled={chosen.length === 0}
      title={t("depositTitle", { count: chosen.length })}
      description={t("depositBody")}
      consequence={t("depositConsequence")}
      confirmLabel={t("markDeposited", { count: chosen.length })}
      schema={z.object({ deposited_on: isoDate })}
      invalidate={[CHEQUE_KEYS.all]}
      errorNamespace="fees"
      onSuccess={(out: DepositOut) =>
        onDone(t("depositDone", { count: out.deposited, skipped: out.skipped.length }))
      }
      submit={(input) =>
        parsed<DepositOut>(
          depositOutSchema,
          chequeApi(api).POST("/api/v1/fees/cheques/deposited", {
            body: {
              receipt_ids: chosen.map((c) => c.receipt_id),
              deposited_on: input.deposited_on,
            },
          }),
        )
      }
    >
      {(errors) => (
        <TextField
          name="deposited_on"
          type="date"
          label={t("depositedOn")}
          defaultValue={today}
          max={today}
          error={errors.deposited_on}
        />
      )}
    </ActionDialog>
  );
}

function ReturnDialog({ cheque, today }: { cheque: Cheque; today: string }) {
  const t = useTranslations("fees.cheques");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("markReturned")}
      triggerSize="sm"
      triggerDescription={t("chequeLabel", { number: cheque.cheque_number })}
      title={t("returnTitle", { number: cheque.cheque_number })}
      description={t("returnBody")}
      consequence={t("returnConsequence")}
      confirmLabel={t("returnConfirm")}
      confirmVariant="danger"
      schema={z.object({
        returned_on: isoDate,
        reason: z
          .string()
          .trim()
          .min(1, { error: "required" })
          .max(REASON_MAX, { error: "tooLong" }),
      })}
      invalidate={[CHEQUE_KEYS.all, ["staff", "fees", "receipt"], ["staff", "fees", "approvals"]]}
      errorNamespace="fees"
      submit={(input) =>
        parsed(
          z.object({ cancellation: z.object({ id: z.string() }) }),
          chequeApi(api).POST(`/api/v1/fees/cheques/${cheque.receipt_id}/returned`, {
            body: input,
          }),
        )
      }
    >
      {(errors) => (
        <>
          <TextField
            name="returned_on"
            type="date"
            label={t("returnedOn")}
            defaultValue={today}
            max={today}
            error={errors.returned_on}
          />
          <TextAreaField
            name="reason"
            label={t("returnReason")}
            hint={t("returnReasonHint")}
            defaultValue={t("returnReasonDefault")}
            error={errors.reason}
            maxLength={REASON_MAX}
            rows={3}
          />
        </>
      )}
    </ActionDialog>
  );
}

function ChequesTable({
  items,
  selected,
  onToggle,
  canReturn,
  today,
}: {
  items: readonly Cheque[];
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
  canReturn: boolean;
  today: string;
}) {
  const t = useTranslations("fees.cheques");
  const locale = useLocale() as Locale;
  return (
    <TableScroll label={t("tableLabel")}>
      <Table>
        <caption className="sr-only">{t("tableCaption")}</caption>
        <THead>
          <Tr>
            <Th>
              <span className="sr-only">{t("select")}</span>
            </Th>
            <Th>{t("chequeNo")}</Th>
            <Th>{t("bank")}</Th>
            <Th>{t("receipt")}</Th>
            <Th>{t("student")}</Th>
            <Th numeric>{t("amount")}</Th>
            <Th>{t("status")}</Th>
            <Th>
              <span className="sr-only">{t("actions")}</span>
            </Th>
          </Tr>
        </THead>
        <TBody>
          {items.map((c) => {
            const canDeposit = c.cheque_status === "received" && c.receipt_status === "issued";
            return (
              <Tr key={c.receipt_id}>
                <Td>
                  {canDeposit ? (
                    <input
                      type="checkbox"
                      className="size-5"
                      checked={selected.has(c.receipt_id)}
                      onChange={() => onToggle(c.receipt_id)}
                      aria-label={t("chequeLabel", { number: c.cheque_number })}
                    />
                  ) : null}
                </Td>
                <Td className="num">{c.cheque_number}</Td>
                <Td>{c.bank_name}</Td>
                <Td>
                  <Link href={`/fees/receipts/${c.receipt_id}`}>{c.receipt_number}</Link>
                  <span className="block text-sm text-ink-muted">{formatDate(c.receipt_date)}</span>
                </Td>
                <Td>
                  {c.student_name}
                  <span className="block text-sm text-ink-muted">{c.class_section}</span>
                </Td>
                <Td numeric>{formatInr(c.amount, locale)}</Td>
                <Td>
                  <Pill variant={STATUS_PILL[c.cheque_status]}>
                    {t(`statuses.${c.cheque_status}`)}
                  </Pill>
                  <span className="block text-sm text-ink-muted">
                    {c.cheque_status === "deposited" && c.deposited_on
                      ? t("depositedOnText", { date: formatDate(c.deposited_on) ?? "" })
                      : c.cheque_status === "cleared" && c.cleared_on
                        ? t("clearedOnText", { date: formatDate(c.cleared_on) ?? "" })
                        : c.cheque_status === "returned" && c.returned_on
                          ? t("returnedOnText", { date: formatDate(c.returned_on) ?? "" })
                          : c.deposit_overdue
                            ? t("overdue")
                            : null}
                  </span>
                  {c.receipt_status === "cancelled" ? (
                    <span className="block text-sm text-ink-muted">{t("receiptCancelled")}</span>
                  ) : null}
                </Td>
                <Td>
                  {canReturn && returnable(c) && c.cheque_status !== "returned" ? (
                    <ReturnDialog cheque={c} today={today} />
                  ) : null}
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </Table>
    </TableScroll>
  );
}

/**
 * `/fees/cheques` (FM-08; FR-FEE-131..141): the cheques received at the counter and where each
 * one is. The counter ticks cheques and marks them deposited, prints the day's deposit list for
 * the bank's pay-in slip; a reconciler marks a returned cheque, which raises a
 * `cheque_dishonoured` cancellation request for a checker. Cleared comes only from a bank
 * statement a person confirmed (Reconcile UPI and bank).
 */
export function ChequesScreen() {
  const t = useTranslations("fees.cheques");
  const tn = useTranslations("school.nav");
  const locale = useLocale() as Locale;
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = CHEQUE_READERS.some((p) => can(p));
  const [status, setStatus] = useState<ChequeStatus | "">("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const cheques = useCheques(status, me !== undefined && allowed);
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  const today = cheques.status === "ready" ? cheques.data.as_of : "";
  const chosen = useMemo(
    () =>
      cheques.status === "ready"
        ? depositable(cheques.data.items).filter((c) => selected.has(c.receipt_id))
        : [],
    [cheques, selected],
  );
  if (me === undefined) return header;
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <NoAccess />
      </div>
    );
  }
  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  return (
    <div className="space-y-5">
      {header}
      <FeesGate data={cheques}>
        {(list) => (
          <Card
            title={t("listTitle", {
              from: formatDate(list.date_from) ?? "",
              to: formatDate(list.date_to) ?? "",
            })}
            description={t("listBody", {
              received: formatCount(list.counts.received ?? 0, locale) ?? "0",
              deposited: formatCount(list.counts.deposited ?? 0, locale) ?? "0",
              cleared: formatCount(list.counts.cleared ?? 0, locale) ?? "0",
              returned: formatCount(list.counts.returned ?? 0, locale) ?? "0",
            })}
            actions={
              <div className="flex flex-wrap gap-2">
                {can("fee.collect") ? (
                  <DepositDialog
                    chosen={chosen}
                    today={list.as_of}
                    onDone={(text) => {
                      setNotice(text);
                      setSelected(new Set());
                    }}
                  />
                ) : null}
                <PrintLink path={`${DEPOSIT_SLIP_PATH}?day=${today}`} label={t("printSlip")} />
              </div>
            }
          >
            <div className="space-y-4">
              {notice ? (
                <Alert tone="success" live>
                  {notice}
                </Alert>
              ) : null}
              <Alert tone="info">{t("clearedNote")}</Alert>
              <SelectField
                name="status"
                label={t("filter")}
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value as ChequeStatus | "");
                  setSelected(new Set());
                }}
                options={[
                  { value: "", label: t("allStatuses") },
                  ...CHEQUE_STATUSES.map((s) => ({ value: s, label: t(`statuses.${s}`) })),
                ]}
              />
              {list.truncated ? <Alert tone="warning">{t("truncated")}</Alert> : null}
              {list.items.length === 0 ? (
                <p>{t("empty")}</p>
              ) : (
                <ChequesTable
                  items={list.items}
                  selected={selected}
                  onToggle={toggle}
                  canReturn={can("fee.reconcile")}
                  today={list.as_of}
                />
              )}
            </div>
          </Card>
        )}
      </FeesGate>
    </div>
  );
}
