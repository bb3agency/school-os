"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import type { Locale } from "@/i18n/routing";
import { ApiError, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { formatInr } from "@/lib/format";
import { fromPaise, isFeesOff, KEYS, toPaise, type StudentYears } from "./data";
import { Amount, FeesGate } from "./parts";

/** One receipt to issue from an "oldest first" split: the account, its year and the amount. */
export interface PlannedPart {
  account_id: string;
  label: string;
  amount: string;
}

/** The clerk's split for one student; `done` holds the accounts already given a receipt. */
export interface SplitPlan {
  studentId: string;
  parts: PlannedPart[];
  done: string[];
}

/** The split codes the API can answer with (422 `split_amount`, FR-FEE-037). */
const SPLIT_CODES = new Set(["not_positive", "above_outstanding"]);

function splitErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const found = (error.problem.errors ?? []).find(
    (e) => e.field.endsWith("split_amount") && SPLIT_CODES.has(e.code),
  );
  return found ? found.code : null;
}

/**
 * The counter's "Earlier years" panel (FM-01; FR-FEE-036, FR-FEE-037; D84): the student's open
 * accounts, earlier years first, each collected with its own receipt. "Split oldest first" asks
 * the API how much of one payment goes to each year (nothing is written); the clerk then issues
 * each part through the normal receipt form, one at a time. Nothing crosses accounts unless the
 * clerk asks, and nothing is saved without the clerk saving each receipt.
 * Shown only when the student has more than one open account (otherwise there is no choice).
 */
export function EarlierYearsPanel({
  studentId,
  currentAccountId,
  plan,
  onCollect,
  onSplit,
  onTakePart,
}: {
  studentId: string;
  currentAccountId: string;
  plan: SplitPlan | null;
  onCollect: (accountId: string) => void;
  onSplit: (plan: SplitPlan) => void;
  onTakePart: (part: PlannedPart) => void;
}) {
  const api = useBffClient("staff");
  const years = useApiQuery(KEYS.studentYears(studentId), () =>
    unwrap(
      api.GET("/api/v1/fees/students/{student_id}/years", {
        params: { path: { student_id: studentId } },
      }),
    ),
  );
  // While it loads, and when the student is not found (404), the counter goes on with the
  // chosen account alone; other failures say the years could not be loaded.
  if (years.status === "loading" || isFeesOff(years)) return null;
  return (
    <FeesGate data={years}>
      {(data) =>
        data.years.length > 1 ? (
          <YearsCard
            data={data}
            currentAccountId={currentAccountId}
            plan={plan !== null && plan.studentId === studentId ? plan : null}
            onCollect={onCollect}
            onSplit={onSplit}
            onTakePart={onTakePart}
          />
        ) : null
      }
    </FeesGate>
  );
}

function YearsCard({
  data,
  currentAccountId,
  plan,
  onCollect,
  onSplit,
  onTakePart,
}: {
  data: StudentYears;
  currentAccountId: string;
  plan: SplitPlan | null;
  onCollect: (accountId: string) => void;
  onSplit: (plan: SplitPlan) => void;
  onTakePart: (part: PlannedPart) => void;
}) {
  const t = useTranslations("fees.counter.years");
  const locale = useLocale() as Locale;
  const api = useBffClient("staff");

  const fieldId = useId();
  const [amount, setAmount] = useState(data.total_collectable);
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);
  const [failure, setFailure] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const label = (accountId: string) =>
    data.years.find((y) => y.account_id === accountId)?.academic_year_label ?? t("noYear");

  async function split(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    const paise = toPaise(amount);
    setFailure(undefined);
    if (paise === null || paise <= 0) {
      setFieldError(t("errors.not_positive"));
      return;
    }
    if (paise > (toPaise(data.total_collectable) ?? 0)) {
      setFieldError(t("errors.above_outstanding"));
      return;
    }
    setFieldError(undefined);
    setPending(true);
    try {
      const out = await unwrap(
        api.GET("/api/v1/fees/students/{student_id}/years", {
          params: {
            path: { student_id: data.student_id },
            query: { split_amount: fromPaise(paise) },
          },
        }),
      );
      onSplit({
        studentId: data.student_id,
        parts: (out.split ?? []).map((part) => ({
          account_id: part.account_id,
          amount: part.amount,
          label: label(part.account_id),
        })),
        done: [],
      });
    } catch (error) {
      const code = splitErrorCode(error);
      if (code) setFieldError(t(`errors.${code}` as "errors.not_positive"));
      else setFailure(error);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card padding="sm" title={t("title")}>
      <div className="mt-2 space-y-4">
        <p className="text-sm text-ink-muted">{t("description")}</p>
        <TableScroll label={t("title")}>
          <Table density="compact">
            <THead>
              <Tr>
                <Th>{t("columns.year")}</Th>
                <Th numeric>{t("columns.overdue")}</Th>
                <Th numeric>{t("columns.dueNow")}</Th>
                <Th>
                  <span className="sr-only">{t("columns.actions")}</span>
                </Th>
              </Tr>
            </THead>
            <TBody>
              {data.years.map((year) => {
                const name = year.academic_year_label ?? t("noYear");
                return (
                  <Tr key={year.account_id}>
                    <Td className="whitespace-nowrap">
                      {name}
                      {year.earlier_year ? (
                        <span className="block text-xs text-ink-muted">{t("earlierYear")}</span>
                      ) : null}
                    </Td>
                    <Td numeric>{formatInr(year.overdue, locale)}</Td>
                    <Td numeric className="font-semibold">
                      {formatInr(year.collectable, locale)}
                    </Td>
                    <Td className="text-end">
                      {year.account_id === currentAccountId ? (
                        <span className="text-sm text-ink-muted">{t("onForm")}</span>
                      ) : (toPaise(year.collectable) ?? 0) > 0 ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onCollect(year.account_id)}
                        >
                          {t("collect", { year: name })}
                        </Button>
                      ) : null}
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableScroll>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-ink-muted">{t("earlierTotal")}</dt>
          <dd className="text-end">
            <Amount value={data.earlier_years_due} />
          </dd>
          <dt className="text-ink-muted">{t("allTotal")}</dt>
          <dd className="text-end font-semibold">
            <Amount value={data.total_collectable} />
          </dd>
        </dl>
        <form
          aria-label={t("splitTitle")}
          className="space-y-2 border-t border-border pt-3"
          onSubmit={(event) => void split(event)}
          noValidate
        >
          <h3 className="text-sm font-semibold text-ink">{t("splitTitle")}</h3>
          <div className="flex flex-wrap items-end gap-3">
            <TextField
              id={fieldId}
              name="split_amount"
              label={t("splitAmount")}
              hint={t("splitHint")}
              error={fieldError}
              inputMode="decimal"
              className="min-w-0 flex-1 [&_input]:num"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
            <Button type="submit" variant="secondary" loading={pending}>
              {t("split")}
            </Button>
          </div>
          <ApiErrorAlert error={failure} namespace="fees" />
        </form>
        {plan ? (
          <section aria-label={t("partsTitle")} className="space-y-2">
            <h3 className="text-sm font-semibold text-ink">{t("partsTitle")}</h3>
            <p className="text-sm text-ink-muted">{t("partsNote")}</p>
            <ol className="divide-y divide-border rounded-md border border-border">
              {plan.parts.map((part) => {
                const done = plan.done.includes(part.account_id);
                return (
                  <li
                    key={part.account_id}
                    className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                  >
                    <span>
                      {part.label} · <Amount value={part.amount} />
                    </span>
                    {done ? (
                      <span className="text-sm font-semibold text-ink-muted">{t("partDone")}</span>
                    ) : (
                      <Button variant="secondary" size="sm" onClick={() => onTakePart(part)}>
                        {t("takePart", { year: part.label })}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        ) : null}
      </div>
    </Card>
  );
}
