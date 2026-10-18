"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate, formatDateTime } from "@/lib/format";
import { formList } from "@/lib/forms";
import * as z from "@/lib/zod";
import { fromPaise, toPaise, type Account } from "@/features/fees/data";
import { Amount, FeesGate } from "@/features/fees/parts";
import { COLLECT, KEYS, isOpen, useConnection, useIntents, type Intent } from "./data";

const STATUS_PILL: Record<string, PillVariant> = {
  created: "progress",
  paid: "done",
  expired: "tag",
  cancelled: "sample",
  failed: "negative",
};

const linkSchema = z.object({
  instalments: z.array(z.coerce.number().int().min(1).max(24)).min(1, { error: "chooseOption" }),
});

interface InstalmentChoice {
  no: number;
  label: string;
  duePaise: number;
  dueOn: string | null;
}

/** Instalments with something collectable still due (taxable heads are never collected). */
function choices(account: Account): InstalmentChoice[] {
  const per = new Map<number, InstalmentChoice>();
  for (const line of account.lines) {
    const due = toPaise(line.due) ?? 0;
    if (!line.collectable || due <= 0) continue;
    const found = per.get(line.instalment_no);
    if (found) found.duePaise += due;
    else
      per.set(line.instalment_no, {
        no: line.instalment_no,
        label: line.label,
        duePaise: due,
        dueOn: line.due_on ?? null,
      });
  }
  return [...per.values()].sort((a, b) => a.no - b.no);
}

/** The link to share, with a copy button (it carries no personal data, ADR-0042 decision 8). */
function ShareLink({ intent, onDone }: { intent: Intent; onDone: () => void }) {
  const t = useTranslations("payments.links");
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(intent.short_url);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  }
  return (
    <div className="space-y-4">
      <Alert tone="success" title={intent.reused ? t("reusedTitle") : t("createdTitle")}>
        {t("createdBody", { date: formatDate(intent.expires_at) ?? "" })}
      </Alert>
      <div className="flex gap-2">
        <Input
          readOnly
          aria-label={t("url")}
          value={intent.short_url}
          className="font-mono text-sm"
          onFocus={(event) => event.currentTarget.select()}
        />
        <Button variant="secondary" onClick={() => void copy()}>
          {t("copy")}
        </Button>
      </div>
      <p role="status" className="text-sm text-ink-muted">
        {copied === "copied" ? t("copied") : copied === "failed" ? t("copyFailed") : ""}
      </p>
      <div className="flex justify-end">
        <Button onClick={onDone}>{t("done")}</Button>
      </div>
    </div>
  );
}

function CreateLink({ account }: { account: Account }) {
  const t = useTranslations("payments.links");
  const api = useBffClient("staff");
  const options = choices(account);
  if (options.length === 0) return <p className="text-sm text-ink-muted">{t("nothingDue")}</p>;
  return (
    <ActionDialog
      triggerLabel={t("create")}
      triggerVariant="primary"
      title={t("createTitle")}
      description={t("createBody")}
      confirmLabel={t("create")}
      consequence={t("createConsequence")}
      schema={linkSchema}
      invalidate={[KEYS.intents(account.id)]}
      errorNamespace="payments"
      extra={(form) => ({ instalments: formList(form, "instalments") })}
      submit={(input, key) =>
        unwrap(
          api.POST("/api/v1/fees/accounts/{account_id}/payment-intents", {
            params: { path: { account_id: account.id } },
            headers: { "Idempotency-Key": key },
            body: { instalments: input.instalments },
          }),
        )
      }
      renderResult={(result: Intent, close) => <ShareLink intent={result} onDone={close} />}
    >
      {(errors) => (
        <fieldset
          className="space-y-2"
          aria-describedby={errors.instalments ? "inst-err" : undefined}
        >
          <legend className="font-semibold text-ink">{t("instalments")}</legend>
          {options.map((o, i) => (
            <label key={o.no} className="flex items-center gap-2">
              <input
                type="checkbox"
                name="instalments"
                value={String(o.no)}
                defaultChecked={i === 0}
                className="size-4 accent-primary"
              />
              <span>
                {o.label}
                {o.dueOn ? ` · ${formatDate(o.dueOn)}` : ""} ·{" "}
                <Amount value={fromPaise(o.duePaise)} />
              </span>
            </label>
          ))}
          {errors.instalments ? (
            <p id="inst-err" className="text-sm text-danger">
              {errors.instalments}
            </p>
          ) : null}
        </fieldset>
      )}
    </ActionDialog>
  );
}

function CancelLink({ intent }: { intent: Intent }) {
  const t = useTranslations("payments.links");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("cancel")}
      triggerSize="sm"
      triggerVariant="danger"
      triggerDescription={t("cancelFor", { code: intent.intent_code })}
      title={t("cancelTitle")}
      description={t("cancelBody")}
      confirmLabel={t("cancel")}
      confirmVariant="danger"
      consequence={t("cancelConsequence")}
      schema={z.object({})}
      invalidate={[KEYS.intents(intent.account_id)]}
      errorNamespace="payments"
      submit={() =>
        unwrap(
          api.POST("/api/v1/fees/payment-intents/{intent_id}/cancel", {
            params: { path: { intent_id: intent.id } },
          }),
        )
      }
    />
  );
}

/**
 * Payment links of one fee account (US-2108; FR-PAY-005, FR-PAY-006, FR-PAY-015): create a link
 * for chosen instalments (the amount is what is due on them now), copy it to share, see each
 * link's status and receipt, cancel an unpaid one. Razorpay never receives a name, phone or
 * email; the money goes to the school's own account.
 */
export function PaymentLinks({ account }: { account: Account }) {
  const t = useTranslations("payments.links");
  const can = useStaffCan();
  const canCollect = can(COLLECT);
  const connection = useConnection();
  const intents = useIntents(account.id);
  const connected = connection.status === "ready" && connection.data.connected;
  return (
    <div className="space-y-4">
      {connection.status === "ready" && !connected ? (
        <Alert tone="info" title={t("notConnectedTitle")}>
          {t("notConnectedBody")}
        </Alert>
      ) : null}
      {connected && canCollect && account.status === "open" ? (
        <CreateLink account={account} />
      ) : null}
      <FeesGate data={intents}>
        {(rows) =>
          rows.length === 0 ? (
            <p className="text-ink-muted">{t("none")}</p>
          ) : (
            <TableScroll label={t("title")}>
              <Table density="compact">
                <THead>
                  <Tr>
                    <Th>{t("columns.code")}</Th>
                    <Th>{t("columns.instalments")}</Th>
                    <Th numeric>{t("columns.amount")}</Th>
                    <Th>{t("columns.status")}</Th>
                    <Th>{t("columns.expires")}</Th>
                    <Th>{t("columns.receipt")}</Th>
                    <Th>{t("columns.action")}</Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.map((intent) => (
                    <Tr key={intent.id}>
                      <Td className="whitespace-nowrap">
                        <code className="font-mono">{intent.intent_code}</code>
                        <span className="num block text-xs text-ink-muted">
                          {formatDateTime(intent.created_at)}
                          {intent.created_by_name ? (
                            <span className="font-sans">{` · ${intent.created_by_name}`}</span>
                          ) : null}
                        </span>
                      </Td>
                      <Td className="num">{intent.instalments.join(", ")}</Td>
                      <Td numeric className="font-semibold">
                        <Amount value={intent.amount} />
                      </Td>
                      <Td>
                        <Pill variant={STATUS_PILL[intent.status] ?? "tag"}>
                          {t(`statuses.${intent.status}`)}
                        </Pill>
                      </Td>
                      <Td className="num whitespace-nowrap">{formatDate(intent.expires_at)}</Td>
                      <Td className="whitespace-nowrap">
                        {intent.receipt_id ? (
                          <Link
                            href={`/fees/receipts/${intent.receipt_id}`}
                            className="num text-primary underline underline-offset-4"
                          >
                            {intent.receipt_number}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </Td>
                      <Td>
                        {canCollect && isOpen(intent) ? <CancelLink intent={intent} /> : null}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableScroll>
          )
        }
      </FeesGate>
    </div>
  );
}
