"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { Locale } from "@/i18n/routing";
import { ApiError, newIdempotencyKey } from "@/lib/bff/query";
import { formatInr } from "@/lib/format";
import {
  BANK_REFERENCE,
  CHEQUE_NUMBER,
  fromPaise,
  isFeesOff,
  KEYS,
  printHref,
  toPaise,
} from "./data";
import {
  bffJson,
  familyPaymentSchema,
  slipHref,
  useFamily,
  type FamilyCounter,
  type FamilyPayment,
} from "./family";
import { Amount, FeesGate } from "./parts";

type FamilyMode = "cash" | "cheque" | "bank_transfer";

/** The 422 codes of a family payment that the panel explains in its own words. */
const FAMILY_CODES = new Set([
  "upi_one_receipt_per_utr",
  "not_in_family",
  "duplicate",
  "too_many",
  "above_due",
  "invalid_cheque_number",
  "invalid_reference",
  "missing",
]);

function familyErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const found = (error.problem.errors ?? []).find((e) => FAMILY_CODES.has(e.code));
  return found ? found.code : null;
}

interface Row {
  accountId: string;
  studentName: string;
  classSection: string | null;
  yearLabel: string | null;
  collectable: string;
}

function rowsOf(data: FamilyCounter): Row[] {
  return data.members.flatMap((m) =>
    m.accounts
      .filter((a) => (toPaise(a.collectable) ?? 0) > 0)
      .map((a) => ({
        accountId: a.account_id,
        studentName: m.display_name ?? "—",
        classSection: m.class_section,
        yearLabel: a.academic_year_label,
        collectable: a.collectable,
      })),
  );
}

/**
 * The counter's "Family" panel (FM-07; FR-FEE-101..107, FR-FEE-124; D86): the student's
 * siblings (children sharing a guardian on their records, only those the clerk may see) with
 * what each owes now, and one payment form with an amount per account. Saving issues one
 * receipt per account in one go (all or none, consecutive numbers); UPI is not offered because
 * a UTR belongs to one receipt. Nothing is saved until the clerk saves. Shown only when the
 * student has at least one sibling.
 */
export function FamilyPanel({ studentId }: { studentId: string }) {
  const family = useFamily(studentId);
  if (family.status === "loading" || isFeesOff(family)) return null;
  return (
    <FeesGate data={family}>
      {(data) => (data.members.length > 1 ? <FamilyCard data={data} /> : null)}
    </FeesGate>
  );
}

function FamilyCard({ data }: { data: FamilyCounter }) {
  const t = useTranslations("fees.counter.family");
  const tm = useTranslations("fees.modes");
  const td = useTranslations("fees.modeDetails");
  const locale = useLocale() as Locale;
  const queryClient = useQueryClient();
  const formId = useId();
  const rows = useMemo(() => rowsOf(data), [data]);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<FamilyMode>("cash");
  const [chequeNumber, setChequeNumber] = useState("");
  const [bankName, setBankName] = useState("");
  const [chequeDate, setChequeDate] = useState(data.as_of);
  const [reference, setReference] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<unknown>(undefined);
  const [codeMessage, setCodeMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState<FamilyPayment | null>(null);
  const [key, setKey] = useState(() => newIdempotencyKey());

  const modes = data.modes.filter((m): m is FamilyMode =>
    ["cash", "cheque", "bank_transfer"].includes(m),
  );
  const chosen = rows
    .map((r) => ({ row: r, paise: toPaise(amounts[r.accountId] ?? "") }))
    .filter((x) => x.paise !== null && x.paise > 0);
  const totalPaise = chosen.reduce((sum, x) => sum + (x.paise ?? 0), 0);

  function check(): Record<string, string> {
    const errors: Record<string, string> = {};
    for (const r of rows) {
      const text = (amounts[r.accountId] ?? "").trim();
      if (!text) continue;
      const paise = toPaise(text);
      if (paise === null || paise < 0) errors[r.accountId] = t("errors.amount");
      else if (paise > (toPaise(r.collectable) ?? 0)) errors[r.accountId] = t("errors.above_due");
    }
    if (chosen.length < 2) errors.parts = t("errors.two_or_more");
    if (chosen.length > data.max_accounts)
      errors.parts = t("errors.too_many", { count: data.max_accounts });
    if (mode === "cheque") {
      if (!CHEQUE_NUMBER.test(chequeNumber.trim()))
        errors.cheque = t("errors.invalid_cheque_number");
      if (!bankName.trim()) errors.bank = t("errors.missing");
      if (!chequeDate) errors.date = t("errors.missing");
    }
    if (mode === "bank_transfer" && !BANK_REFERENCE.test(reference.trim()))
      errors.reference = t("errors.invalid_reference");
    return errors;
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setFailure(undefined);
    setCodeMessage(null);
    const errors = check();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    const details =
      mode === "cheque"
        ? {
            cheque_number: chequeNumber.trim(),
            bank_name: bankName.trim(),
            cheque_date: chequeDate,
          }
        : mode === "bank_transfer"
          ? { reference: reference.trim() }
          : {};
    setPending(true);
    try {
      const out = await bffJson(locale, "/api/v1/fees/family-payments", familyPaymentSchema, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: {
          mode,
          mode_details: details,
          parts: chosen.map((x) => ({
            account_id: x.row.accountId,
            amount: fromPaise(x.paise ?? 0),
          })),
        },
      });
      setDone(out);
      setKey(newIdempotencyKey());
      await queryClient.invalidateQueries({ queryKey: KEYS.all });
    } catch (error) {
      const code = familyErrorCode(error);
      if (code) setCodeMessage(t(`errors.${code}` as "errors.missing"));
      else setFailure(error);
      // A refused payment can be corrected and sent again as a new intent.
      if (error instanceof ApiError && error.status < 500) setKey(newIdempotencyKey());
    } finally {
      setPending(false);
    }
  }

  if (done) {
    return (
      <Card padding="sm" title={t("savedTitle")}>
        <div className="mt-2 space-y-3">
          <Alert tone="success" title={t("savedLead", { count: done.receipts.length })}>
            {t("savedBody", { amount: formatInr(done.amount, locale) ?? done.amount })}
          </Alert>
          <ul className="divide-y divide-border rounded-md border border-border">
            {done.receipts.map((r) => (
              <li
                key={r.id}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
              >
                <span>
                  <span className="font-semibold">{r.receipt_number}</span> · {r.student_name}
                  {r.class_section ? ` (${r.class_section})` : ""} · <Amount value={r.amount} />
                </span>
                <a
                  className={buttonClasses("ghost", "sm")}
                  href={printHref(`/api/v1/fees/receipts/${r.id}/print`)}
                  target="_blank"
                  rel="noopener"
                >
                  {t("printReceipt", { number: r.receipt_number })}
                </a>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <a
              className={buttonClasses("primary")}
              href={slipHref(done.receipts.map((r) => r.id))}
              target="_blank"
              rel="noopener"
            >
              {t("printSlip")}
            </a>
            <Button variant="secondary" onClick={() => setDone(null)}>
              {t("another")}
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card padding="sm" title={t("title")}>
      <form
        id={formId}
        aria-label={t("formLabel")}
        className="mt-2 space-y-4"
        onSubmit={(event) => void save(event)}
        noValidate
      >
        <p className="text-sm text-ink-muted">{t("description")}</p>
        <TableScroll label={t("title")}>
          <Table density="compact">
            <THead>
              <Tr>
                <Th>{t("columns.student")}</Th>
                <Th>{t("columns.year")}</Th>
                <Th numeric>{t("columns.dueNow")}</Th>
                <Th numeric>{t("columns.amount")}</Th>
              </Tr>
            </THead>
            <TBody>
              {data.members.map((m) =>
                m.accounts.length === 0 || (toPaise(m.collectable) ?? 0) === 0 ? (
                  <Tr key={m.student_id}>
                    <Td>
                      {m.display_name ?? "—"}
                      <span className="block text-xs text-ink-muted">
                        {m.class_section ?? ""}
                        {m.selected ? ` · ${t("thisStudent")}` : ""}
                      </span>
                    </Td>
                    <Td colSpan={3} className="text-sm text-ink-muted">
                      {t("nothingDue")}
                    </Td>
                  </Tr>
                ) : (
                  m.accounts
                    .filter((a) => (toPaise(a.collectable) ?? 0) > 0)
                    .map((a, index) => (
                      <Tr key={a.account_id}>
                        <Td>
                          {index === 0 ? (
                            <>
                              {m.display_name ?? "—"}
                              <span className="block text-xs text-ink-muted">
                                {m.class_section ?? ""}
                                {m.selected ? ` · ${t("thisStudent")}` : ""}
                              </span>
                            </>
                          ) : (
                            <span className="sr-only">{m.display_name ?? "—"}</span>
                          )}
                        </Td>
                        <Td className="whitespace-nowrap">
                          {a.academic_year_label ?? t("noYear")}
                        </Td>
                        <Td numeric>{formatInr(a.collectable, locale)}</Td>
                        <Td numeric>
                          <TextField
                            label={
                              <span className="sr-only">
                                {t("amountFor", {
                                  name: m.display_name ?? "—",
                                  year: a.academic_year_label ?? t("noYear"),
                                })}
                              </span>
                            }
                            inputMode="decimal"
                            className="ms-auto w-32 [&_input]:num"
                            value={amounts[a.account_id] ?? ""}
                            error={fieldErrors[a.account_id]}
                            onChange={(event) =>
                              setAmounts((current) => ({
                                ...current,
                                [a.account_id]: event.target.value,
                              }))
                            }
                          />
                        </Td>
                      </Tr>
                    ))
                ),
              )}
            </TBody>
          </Table>
        </TableScroll>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              setAmounts(Object.fromEntries(rows.map((r) => [r.accountId, r.collectable])))
            }
          >
            {t("fillAll")}
          </Button>
          <p className="text-sm">
            {t("total")}{" "}
            <span className="font-semibold">{formatInr(fromPaise(totalPaise), locale)}</span>
          </p>
        </div>
        {fieldErrors.parts ? (
          <p className="text-sm text-danger" role="alert">
            {fieldErrors.parts}
          </p>
        ) : null}
        <SegmentedControl
          legend={t("mode")}
          legendVisible
          options={modes.map((m) => ({ value: m, label: tm(m) }))}
          value={mode}
          onValueChange={(value) => setMode(value as FamilyMode)}
        />
        <p className="text-xs text-ink-muted">{t("noUpi")}</p>
        {mode === "cheque" ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <TextField
              label={td("cheque_number")}
              inputMode="numeric"
              value={chequeNumber}
              error={fieldErrors.cheque}
              onChange={(event) => setChequeNumber(event.target.value)}
            />
            <TextField
              label={td("bank_name")}
              value={bankName}
              error={fieldErrors.bank}
              onChange={(event) => setBankName(event.target.value)}
            />
            <TextField
              label={td("cheque_date")}
              type="date"
              value={chequeDate}
              error={fieldErrors.date}
              onChange={(event) => setChequeDate(event.target.value)}
            />
          </div>
        ) : null}
        {mode === "bank_transfer" ? (
          <TextField
            label={td("reference")}
            value={reference}
            error={fieldErrors.reference}
            onChange={(event) => setReference(event.target.value)}
          />
        ) : null}
        {codeMessage ? (
          <Alert tone="danger" title={t("refusedTitle")}>
            {codeMessage}
          </Alert>
        ) : null}
        <ApiErrorAlert error={failure} namespace="fees" />
        <Button type="submit" loading={pending}>
          {t("save", { count: chosen.length })}
        </Button>
      </form>
    </Card>
  );
}
