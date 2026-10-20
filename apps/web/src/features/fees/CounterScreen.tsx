"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button, buttonClasses } from "@/components/ui/Button";
import { HeroFigure } from "@/components/ui/HeroFigure";
import { Card } from "@/components/ui/Card";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { Icon } from "@/components/ui/Icon";
import { Input, TextField } from "@/components/ui/Input";
import { Kbd } from "@/components/ui/Kbd";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import type { Locale } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { ApiError, newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate, formatInr } from "@/lib/format";
import {
  BANK_REFERENCE,
  CHEQUE_NUMBER,
  COLLECT,
  defaultAllocation,
  fromPaise,
  KEYS,
  MODE_KEYS,
  printHref,
  toPaise,
  useAccount,
  useFeeStatus,
  UTR,
  type Account,
  type AccountSummary,
  type CounterMode,
  type DueLine,
  type Receipt,
} from "./data";
import { ReceiptWhatsApp } from "@/features/messaging/ReceiptWhatsApp";
import { Amount, FeesGate, NoAccess } from "./parts";
import { UpiCounterHelp } from "./UpiCounter";

const MODES: readonly CounterMode[] = ["cash", "cheque", "bank_transfer", "upi_qr"];
const MNEMONIC: Record<CounterMode, string> = {
  cash: "C",
  cheque: "Q",
  bank_transfer: "B",
  upi_qr: "U",
};

/** Fixed ids of the payment fields, so the error summary can link to each one. */
const FIELD_ID = {
  amount: "fee-amount",
  chequeNumber: "fee-cheque-number",
  bankName: "fee-bank-name",
  chequeDate: "fee-cheque-date",
  reference: "fee-reference",
  utr: "fee-utr",
  allocation: "fee-allocation",
} as const;

function lineKey(line: Pick<DueLine, "instalment_no" | "head_id">): string {
  return `${line.instalment_no}:${line.head_id ?? "late"}`;
}

/** The counter's keys, always on screen (docs/17 §5.8; report §2 "shown on screen"). */
function ShortcutHints() {
  const t = useTranslations("fees.counter");
  const item = "inline-flex items-center gap-1";
  return (
    <section aria-label={t("shortcutsLabel")} data-print="hide">
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
        <li className={item}>
          <Kbd>/</Kbd>
          <Kbd>F2</Kbd>
          {t("findTitle")}
        </li>
        <li className={item}>
          {MODES.map((mode) => (
            <Kbd key={mode}>{MNEMONIC[mode]}</Kbd>
          ))}
          {t("mode")}
        </li>
        <li className={item}>
          <Kbd>Ctrl</Kbd>+<Kbd>Enter</Kbd>
          {t("save")}
        </li>
        <li className={item}>
          <Kbd>P</Kbd>
          {t("print")}
        </li>
        <li className={item}>
          <Kbd>N</Kbd>
          {t("next")}
        </li>
      </ul>
    </section>
  );
}

/** Student search as a combobox with a listbox of up to 8 matches (WAI-ARIA combobox). */
function StudentSearch({
  onPick,
  inputRef,
}: {
  onPick: (account: AccountSummary) => void;
  inputRef: RefObject<HTMLInputElement | null>;
}) {
  const t = useTranslations("fees.counter");
  const api = useBffClient("staff");
  const listId = useId();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ query: string; rows: AccountSummary[] } | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<unknown>(undefined);
  const text = query.trim();

  useEffect(() => {
    if (text.length < 2) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      unwrap(api.POST("/api/v1/fees/accounts/search", { body: { query: text } }))
        .then((rows) => {
          if (cancelled) return;
          setFound({ query: text, rows: rows.slice(0, 8) });
          setActive(0);
          setError(undefined);
        })
        .catch((failure: unknown) => {
          if (!cancelled) setError(failure);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, text]);

  const current = text.length >= 2 && found?.query === text ? found.rows : [];
  const searching = text.length >= 2 && found?.query !== text && !error;

  function choose(account: AccountSummary | undefined) {
    if (!account) return;
    onPick(account);
    setQuery("");
    setFound(null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && current.length) {
      event.preventDefault();
      setActive((i) => (i + 1) % current.length);
    } else if (event.key === "ArrowUp" && current.length) {
      event.preventDefault();
      setActive((i) => (i - 1 + current.length) % current.length);
    } else if (event.key === "Enter" && current.length) {
      event.preventDefault();
      choose(current[active]);
    } else if (event.key === "Escape") {
      setQuery("");
    }
  }

  const open = current.length > 0;
  return (
    <div className="relative space-y-1">
      <label htmlFor={`${listId}-input`} className="text-sm font-semibold text-ink">
        {t("searchLabel")}
      </label>
      <p id={`${listId}-hint`} className="text-sm text-ink-muted">
        {t("searchHint")}
      </p>
      <div className="relative">
        <Icon
          name="search"
          className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-ink-muted"
        />
        <Input
          ref={inputRef}
          id={`${listId}-input`}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${listId}-list`}
          aria-autocomplete="list"
          aria-describedby={`${listId}-hint`}
          aria-activedescendant={open ? `${listId}-opt-${active}` : undefined}
          autoComplete="off"
          className="ps-9"
          // A name typed at the counter never leaves this field except in a POST body.
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      {searching ? <p className="text-sm text-ink-muted">{t("searching")}</p> : null}
      {text.length >= 2 && !searching && !open && !error ? (
        <p className="text-sm text-ink-muted" role="status">
          {t("noMatches")}
        </p>
      ) : null}
      <div
        id={`${listId}-list`}
        role="listbox"
        aria-label={t("matchesLabel")}
        hidden={!open}
        className="overlay-panel absolute z-10 mt-1 w-full p-1"
      >
        {current.map((m, i) => (
          <div
            key={m.account_id}
            id={`${listId}-opt-${i}`}
            role="option"
            tabIndex={-1}
            aria-selected={i === active}
            className={cn(
              "flex cursor-pointer flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 rounded-md px-3 py-2",
              i === active ? "bg-accent-soft" : "hover:bg-surface-muted",
            )}
            onMouseDown={(event) => {
              event.preventDefault();
              choose(m);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") choose(m);
            }}
          >
            <span className="min-w-0">
              <span className="font-semibold">{m.display_name ?? t("noName")}</span>{" "}
              <span className="text-sm text-ink-muted">
                {[m.admission_no, m.class_section].filter(Boolean).join(" · ")}
              </span>
            </span>{" "}
            <span className="text-sm whitespace-nowrap">
              {t("balance")} <Amount value={m.balance} />
            </span>
          </div>
        ))}
      </div>
      <ApiErrorAlert error={error} namespace="fees" />
    </div>
  );
}

function DuesTable({ account }: { account: Account }) {
  const t = useTranslations("fees.counter");
  const locale = useLocale() as Locale;
  const rows = account.lines.filter((line) => (toPaise(line.due) ?? 0) !== 0);
  if (rows.length === 0) return <p>{t("nothingDue")}</p>;
  return (
    <TableScroll label={t("duesTitle")}>
      <Table density="compact">
        <THead>
          <Tr>
            <Th>{t("columns.instalment")}</Th>
            <Th>{t("columns.head")}</Th>
            <Th>{t("columns.dueOn")}</Th>
            <Th numeric>{t("columns.charged")}</Th>
            <Th numeric>{t("columns.lateFee")}</Th>
            <Th numeric>{t("columns.paid")}</Th>
            <Th numeric>{t("columns.due")}</Th>
          </Tr>
        </THead>
        <TBody>
          {rows.map((line) => (
            <Tr key={lineKey(line)}>
              <Td>{line.label}</Td>
              <Td>
                {line.head_name ?? t("lateFee")}
                {!line.collectable ? (
                  <span className="block text-xs text-ink-muted">{t("taxable")}</span>
                ) : null}
              </Td>
              <Td className="whitespace-nowrap">
                <span className="num">{line.due_on ? formatDate(line.due_on) : "—"}</span>
                {line.overdue ? (
                  <span className="block text-xs font-semibold text-danger">{t("overdue")}</span>
                ) : null}
              </Td>
              <Td numeric>{formatInr(line.charged, locale)}</Td>
              <Td numeric>{formatInr(line.late_fee, locale)}</Td>
              <Td numeric>{formatInr(line.paid, locale)}</Td>
              <Td numeric className="font-semibold">
                {formatInr(line.due, locale)}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableScroll>
  );
}

/** The chosen student, what is due and the total, left of (or above) the payment. */
function DuesPanel({ account }: { account: Account }) {
  const t = useTranslations("fees.counter");
  return (
    <Card
      padding="sm"
      title={
        <>
          {account.display_name ?? t("noName")}{" "}
          <span className="text-base font-normal text-ink-muted">
            {[account.admission_no, account.class_section].filter(Boolean).join(" · ")}
          </span>
        </>
      }
    >
      <div className="mt-3 space-y-3">
        <DuesTable account={account} />
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <p className="flex flex-col">
            <span className="text-sm text-ink-muted">{t("totalDueNow")}</span>
            <Amount
              value={account.totals.collectable}
              className="num text-2xl font-semibold whitespace-nowrap text-ink"
            />
          </p>
          <Link
            href={`/fees/accounts/${account.id}`}
            className="text-sm font-semibold text-primary underline underline-offset-4"
          >
            {t("openAccount")}
          </Link>
        </div>
      </div>
    </Card>
  );
}

interface Form {
  amount: string;
  mode: CounterMode;
  chequeNumber: string;
  bankName: string;
  chequeDate: string;
  reference: string;
  utr: string;
}

type Errors = Partial<Record<keyof Form | "allocation", string>>;

function check(
  form: Form,
  collectable: number,
  custom: Map<string, string> | null,
  t: (key: string) => string,
): Errors {
  const errors: Errors = {};
  const paise = toPaise(form.amount);
  if (paise === null || paise <= 0) errors.amount = t("errors.amount");
  else if (paise > collectable) errors.amount = t("errors.amountAboveDue");
  if (form.mode === "cheque") {
    if (!CHEQUE_NUMBER.test(form.chequeNumber.trim()))
      errors.chequeNumber = t("errors.chequeNumber");
    if (!form.bankName.trim()) errors.bankName = t("errors.required");
    if (!form.chequeDate) errors.chequeDate = t("errors.required");
  }
  if (form.mode === "bank_transfer" && !BANK_REFERENCE.test(form.reference.trim())) {
    errors.reference = t("errors.reference");
  }
  if (form.mode === "upi_qr" && !UTR.test(form.utr.trim())) errors.utr = t("errors.utr");
  if (custom && paise !== null) {
    let sum = 0;
    for (const value of custom.values()) {
      const p = toPaise(value || "0");
      if (p === null || p < 0) {
        errors.allocation = t("errors.allocation");
        return errors;
      }
      sum += p;
    }
    if (sum !== paise) errors.allocation = t("errors.allocationSum");
  }
  return errors;
}

/**
 * Taking one payment for one account. Keyed by the account, so a newly chosen student starts
 * from a fresh form (the amount pre-filled with what is due now and selected for typing).
 */
function PaymentForm({
  account,
  today,
  onSaved,
}: {
  account: Account;
  today: string;
  onSaved: (receipt: Receipt) => void;
}) {
  const t = useTranslations("fees.counter");
  const tf = useTranslations("fees");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const amountRef = useRef<HTMLInputElement | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const collectable = toPaise(account.totals.collectable) ?? 0;
  const [form, setForm] = useState<Form>({
    amount: account.totals.collectable,
    mode: "cash",
    chequeNumber: "",
    bankName: "",
    chequeDate: today,
    reference: "",
    utr: "",
  });
  const [custom, setCustom] = useState<Map<string, string> | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<unknown>(undefined);
  const [intentKey] = useState(newIdempotencyKey);

  useEffect(() => {
    amountRef.current?.focus();
    amountRef.current?.select();
  }, []);

  const allocation = useMemo(() => {
    const paise = toPaise(form.amount) ?? 0;
    return defaultAllocation(account.lines, Math.min(Math.max(paise, 0), collectable));
  }, [account.lines, form.amount, collectable]);

  function update<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function onModeKey(event: KeyboardEvent<HTMLInputElement>) {
    // Enter on the mode records the payment, like Enter in any field of the form.
    if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      formRef.current?.requestSubmit();
      return;
    }
    const mode = MODE_KEYS[event.key.toLowerCase()];
    if (mode && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      update("mode", mode);
      document.getElementById(`mode-${mode}`)?.focus();
    }
  }

  const save = useCallback(
    async (event?: FormEvent) => {
      event?.preventDefault();
      if (saving) return;
      const found = check(form, collectable, custom, (k) => t(k as "errors.amount"));
      setErrors(found);
      if (Object.keys(found).length > 0) {
        setAttempt((n) => n + 1);
        return;
      }
      setSaving(true);
      setFailure(undefined);
      const mode_details =
        form.mode === "cheque"
          ? {
              cheque_number: form.chequeNumber.trim(),
              bank_name: form.bankName.trim(),
              cheque_date: form.chequeDate,
            }
          : form.mode === "bank_transfer"
            ? { reference: form.reference.trim() }
            : {};
      const allocations = custom
        ? account.lines
            .filter((line) => (toPaise(custom.get(lineKey(line)) || "0") ?? 0) > 0)
            .map((line) => ({
              instalment_no: line.instalment_no,
              head_id: line.head_id,
              amount: fromPaise(toPaise(custom.get(lineKey(line)) || "0") ?? 0),
            }))
        : null;
      try {
        const receipt = await unwrap(
          api.POST("/api/v1/fees/receipts", {
            headers: { "Idempotency-Key": intentKey },
            body: {
              account_id: account.id,
              amount: fromPaise(toPaise(form.amount) ?? 0),
              mode: form.mode,
              mode_details,
              utr: form.mode === "upi_qr" ? form.utr.trim() : null,
              allocations,
            },
          }),
        );
        await queryClient.invalidateQueries({ queryKey: KEYS.all });
        onSaved(receipt);
      } catch (error) {
        setFailure(error);
        if (error instanceof ApiError && error.code === "utr_in_use") {
          const detail = error.problem.detail;
          setErrors({ utr: typeof detail === "string" ? detail : t("errors.utrInUse") });
        }
      } finally {
        setSaving(false);
      }
    },
    [account, api, collectable, custom, form, intentKey, onSaved, queryClient, saving, t],
  );

  // Ctrl+Enter saves from anywhere on the form (docs/17 §5.8).
  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        void save();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  if (collectable <= 0) {
    return (
      <Alert tone="info" title={t("nothingDueTitle")}>
        {t("nothingDue")}
      </Alert>
    );
  }

  const summary: SummaryError[] = (Object.keys(FIELD_ID) as (keyof typeof FIELD_ID)[]).flatMap(
    (key) => {
      const message = errors[key];
      return message ? [{ fieldId: FIELD_ID[key], message }] : [];
    },
  );

  return (
    <form
      ref={formRef}
      aria-label={t("paymentTitle")}
      onSubmit={(event) => void save(event)}
      noValidate
      className="space-y-4 rounded-xl border border-border bg-surface p-3 sm:p-4"
    >
      <ErrorSummary errors={summary} attempt={attempt} />
      <TextField
        ref={amountRef}
        id={FIELD_ID.amount}
        name="amount"
        label={t("amount")}
        hint={t("amountHint")}
        error={errors.amount}
        inputMode="decimal"
        className="[&_input]:num [&_input]:text-lg [&_input]:font-semibold"
        value={form.amount}
        onChange={(event) => update("amount", event.target.value)}
        onFocus={(event) => event.currentTarget.select()}
      />
      <fieldset className="min-w-0 space-y-1" aria-describedby="fee-mode-hint">
        <legend className="text-sm font-semibold text-ink">{t("mode")}</legend>
        <p id="fee-mode-hint" className="text-sm text-ink-muted">
          {t("modeHint")}
        </p>
        <div className="flex w-full flex-wrap gap-1 rounded-lg bg-surface-sunken p-1">
          {MODES.map((mode) => (
            <label
              key={mode}
              className={cn(
                "relative inline-flex min-h-9 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-transparent px-3 text-sm font-semibold whitespace-nowrap text-ink-muted transition-colors",
                "hover:text-ink",
                "has-checked:border-border has-checked:bg-surface has-checked:text-ink has-checked:shadow-raised",
                "has-focus-visible:outline-3 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus",
                "pointer-coarse:min-h-11",
              )}
            >
              <input
                id={`mode-${mode}`}
                type="radio"
                name="mode"
                value={mode}
                checked={form.mode === mode}
                onChange={() => update("mode", mode)}
                onKeyDown={onModeKey}
                aria-keyshortcuts={MNEMONIC[mode]}
                className="sr-only"
              />
              {tf(`modes.${mode}`)} <Kbd>{MNEMONIC[mode]}</Kbd>
            </label>
          ))}
        </div>
      </fieldset>
      {form.mode === "cheque" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            id={FIELD_ID.chequeNumber}
            name="cheque_number"
            label={t("chequeNumber")}
            error={errors.chequeNumber}
            inputMode="numeric"
            maxLength={6}
            className="min-w-0 [&_input]:num"
            value={form.chequeNumber}
            onChange={(event) => update("chequeNumber", event.target.value)}
          />
          <TextField
            id={FIELD_ID.chequeDate}
            name="cheque_date"
            type="date"
            label={t("chequeDate")}
            error={errors.chequeDate}
            className="min-w-0 [&_input]:num"
            value={form.chequeDate}
            onChange={(event) => update("chequeDate", event.target.value)}
          />
          <TextField
            id={FIELD_ID.bankName}
            name="bank_name"
            label={t("bankName")}
            error={errors.bankName}
            maxLength={80}
            className="min-w-0 sm:col-span-2"
            value={form.bankName}
            onChange={(event) => update("bankName", event.target.value)}
          />
        </div>
      ) : null}
      {form.mode === "bank_transfer" ? (
        <TextField
          id={FIELD_ID.reference}
          name="reference"
          label={t("reference")}
          hint={t("referenceHint")}
          error={errors.reference}
          maxLength={22}
          className="[&_input]:num"
          value={form.reference}
          onChange={(event) => update("reference", event.target.value)}
        />
      ) : null}
      {form.mode === "upi_qr" ? (
        <div className="space-y-2">
          <TextField
            id={FIELD_ID.utr}
            name="utr"
            label={t("utr")}
            hint={t("utrHint")}
            error={errors.utr}
            inputMode="numeric"
            maxLength={12}
            autoComplete="off"
            className="[&_input]:num"
            value={form.utr}
            onChange={(event) => update("utr", event.target.value)}
          />
          <UpiCounterHelp utr={form.utr} />
        </div>
      ) : null}
      <section aria-labelledby="fee-allocation-title" className="space-y-2">
        <h2 id="fee-allocation-title" className="text-sm font-semibold text-ink">
          {t("allocationTitle")}
        </h2>
        {custom ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {account.lines
              .filter((line) => line.collectable && (toPaise(line.due) ?? 0) > 0)
              .map((line) => (
                <TextField
                  key={lineKey(line)}
                  name={`alloc-${lineKey(line)}`}
                  label={`${line.label} · ${line.head_name ?? t("lateFee")}`}
                  hint={t("upTo", { amount: line.due })}
                  inputMode="decimal"
                  className="min-w-0 [&_input]:num"
                  value={custom.get(lineKey(line)) ?? ""}
                  onChange={(event) =>
                    setCustom((current) => {
                      const next = new Map(current ?? []);
                      next.set(lineKey(line), event.target.value);
                      return next;
                    })
                  }
                />
              ))}
          </div>
        ) : (
          <ul className="space-y-0.5 text-sm">
            {allocation.map(({ line, paise }) => (
              <li key={lineKey(line)} className="flex justify-between gap-4">
                <span className="min-w-0">
                  {line.label} · {line.head_name ?? t("lateFee")}
                </span>
                <Amount value={fromPaise(paise)} />
              </li>
            ))}
          </ul>
        )}
        {errors.allocation ? (
          <p id={FIELD_ID.allocation} tabIndex={-1} className="text-sm font-semibold text-danger">
            {errors.allocation}
          </p>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            setCustom((current) =>
              current
                ? null
                : new Map(allocation.map(({ line, paise }) => [lineKey(line), fromPaise(paise)])),
            )
          }
        >
          {custom ? t("useOldestFirst") : t("changeAllocation")}
        </Button>
      </section>
      <ApiErrorAlert error={failure} namespace="fees" />
      <div className="space-y-2 border-t border-border pt-3">
        <Button
          type="submit"
          size="lg"
          className="w-full"
          loading={saving}
          aria-keyshortcuts="Control+Enter"
        >
          {t("save")} <Kbd>Ctrl+Enter</Kbd>
        </Button>
        <p className="text-sm text-ink-muted">
          {t("todayNote", { date: formatDate(today) ?? "" })}
        </p>
      </div>
    </form>
  );
}

/** After saving: the receipt number large, Print focused (P), Next student (N), and "Send on
 * WhatsApp" as a secondary button without a shortcut (FR-MSG-026). */
function SavedPanel({
  saved,
  printRef,
  onNext,
}: {
  saved: Receipt;
  printRef: RefObject<HTMLAnchorElement | null>;
  onNext: () => void;
}) {
  const t = useTranslations("fees.counter");
  const tf = useTranslations("fees");
  return (
    <Card title={t("savedTitle")} className="max-w-2xl">
      <div role="status" className="mt-2 space-y-2">
        <HeroFigure size="md" label={t("receiptNumber")} value={saved.receipt_number} />
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span>{saved.student_name}</span>
          <span aria-hidden="true">·</span>
          <Amount value={saved.amount} className="num text-xl font-semibold whitespace-nowrap" />
          <span aria-hidden="true">·</span>
          <span className="num">{formatDate(saved.receipt_date)}</span>
        </p>
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <a
          ref={printRef}
          href={printHref(`/api/v1/fees/receipts/${saved.id}/print`)}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonClasses("primary", "lg")}
          aria-keyshortcuts="P"
        >
          <Icon name="file" className="size-4" />
          {t("print")} <Kbd>P</Kbd>
          <span className="sr-only"> ({tf("newTab")})</span>
        </a>
        <Button variant="secondary" size="lg" onClick={onNext} aria-keyshortcuts="N">
          {t("next")} <Kbd>N</Kbd>
        </Button>
        <Link
          href={`/fees/receipts/${saved.id}`}
          className="inline-flex min-h-10 items-center px-2 font-semibold text-primary underline underline-offset-4"
        >
          {t("openReceipt")}
        </Link>
      </div>
      {/* FR-MSG-026: a secondary action after the receipt is saved; Print keeps the focus. */}
      <div className="mt-4">
        <ReceiptWhatsApp receiptId={saved.id} />
      </div>
    </Card>
  );
}

/**
 * The fee counter (US-2102; FR-FEE-010, FR-FEE-022; docs/17 §5.8): keyboard-only at 1366×768.
 * Focus starts in the search box (`/` or `F2` brings it back); Enter picks a student; the
 * amount is pre-filled with what is due and selected; C/Q/B/U on the mode choose it; Enter or
 * Ctrl+Enter saves; after saving P prints and N starts the next student. Receipts are dated
 * today (IST) by the server: there is no date field (no backdating). From `lg` the dues sit
 * left of the payment so a typical student fits without scrolling; the tab order stays
 * search → dues → amount → mode → mode fields → save.
 */
export function CounterScreen({ initialAccountId = null }: { initialAccountId?: string | null }) {
  const t = useTranslations("fees.counter");
  const tf = useTranslations("fees");
  const tn = useTranslations("school.nav");
  const tc = useTranslations("common");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can(COLLECT);
  const status = useFeeStatus(me !== undefined && allowed);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const printRef = useRef<HTMLAnchorElement | null>(null);
  const [accountId, setAccountId] = useState<string | null>(initialAccountId);
  const account = useAccount(accountId);
  const [saved, setSaved] = useState<Receipt | null>(null);
  const loaded = account.status === "ready" ? account.data : null;

  useEffect(() => {
    if (saved) printRef.current?.focus();
    else if (accountId === null) searchRef.current?.focus();
  }, [saved, accountId, status.status]);

  const nextStudent = useCallback(() => {
    setSaved(null);
    setAccountId(null);
  }, []);

  // `/` or F2 focuses the search from anywhere on the counter; P and N after saving.
  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement;
      if (event.key === "F2" || (event.key === "/" && !typing)) {
        event.preventDefault();
        if (saved) nextStudent();
        else searchRef.current?.focus();
        return;
      }
      if (saved && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
        if (event.key.toLowerCase() === "n") {
          event.preventDefault();
          nextStudent();
        } else if (event.key.toLowerCase() === "p") {
          event.preventDefault();
          printRef.current?.click();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saved, nextStudent]);

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tf("nav.receipts"), href: "/fees/receipts" },
        { label: t("title") },
      ]}
      className="mb-2 md:mb-3"
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

  return (
    <div className="space-y-4">
      {header}
      <FeesGate data={status}>
        {(info) =>
          info.day_closed ? (
            <Alert tone="warning" title={t("dayClosedTitle")}>
              {t("dayClosedBody")}
            </Alert>
          ) : saved ? (
            <SavedPanel saved={saved} printRef={printRef} onNext={nextStudent} />
          ) : (
            <>
              <ShortcutHints />
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:items-start">
                <div className="min-w-0 space-y-4">
                  <Card padding="sm">
                    <StudentSearch
                      inputRef={searchRef}
                      onPick={(m) => setAccountId(m.account_id)}
                    />
                  </Card>
                  {accountId === null ? null : account.status === "loading" ? (
                    <LoadingState label={tc("loading")} />
                  ) : loaded ? (
                    <DuesPanel account={loaded} />
                  ) : (
                    <FeesGate data={account}>{() => null}</FeesGate>
                  )}
                </div>
                {loaded ? (
                  <div className="min-w-0">
                    <PaymentForm
                      key={`${loaded.id}:${loaded.version}`}
                      account={loaded}
                      today={info.today}
                      onSaved={setSaved}
                    />
                  </div>
                ) : null}
              </div>
            </>
          )
        }
      </FeesGate>
    </div>
  );
}
