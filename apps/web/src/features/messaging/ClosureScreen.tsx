"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console/DataTableV2";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import type { Locale } from "@/i18n/routing";
import { newIdempotencyKey, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate, formatDateTime, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { READ, hhmm, isUnavailable } from "./data";
import {
  CLOSURE_REASONS,
  CLOSURE_SEND,
  REACH_KEYS,
  previewClosure,
  sendClosure,
  useClosures,
  type Closure,
  type ClosureInput,
  type ClosurePreview,
  type ClosureReason,
} from "./reach-data";

/**
 * FP-02: the urgent closure message (FR-MSG-047..055; ADR-0043 amendment, lead decision D85).
 * The principal (``message.closure.send``, step-up MFA: the BFF asks for it) chooses the whole
 * school or sections, the dates and a reason from the list; the preview shows the exact English
 * text, who gets it and when it goes out (06:00-21:30 IST, also during quiet hours, never
 * outside that window). Recent closure messages and how many were sent are listed for everyone
 * with school-wide ``message.read``.
 */

const MAX_DAYS_AHEAD = 7;

function istToday(): string {
  // en-CA formats as YYYY-MM-DD; the school's calendar day is IST.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

function addDays(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function ClosureForm() {
  const t = useTranslations("messaging.closures");
  const tr = useTranslations("messaging.log.reasons");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const locale = useLocale() as Locale;
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const [today] = useState(istToday);
  const [wholeSchool, setWholeSchool] = useState(true);
  const [chosen, setChosen] = useState<Set<string>>(() => new Set());
  const [from, setFrom] = useState(() => addDays(today, 1));
  const [to, setTo] = useState("");
  const [reason, setReason] = useState<ClosureReason>("weather");
  const [preview, setPreview] = useState<{ input: ClosureInput; out: ClosurePreview } | null>(null);
  const [sent, setSent] = useState<Closure | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const confirmKey = useMemo(() => (preview ? newIdempotencyKey() : ""), [preview]);
  const legendId = useId();
  const count = (n: number | undefined) => formatCount(n ?? 0, locale) ?? "0";

  function changed() {
    setPreview(null);
    setSent(null);
  }

  const input: ClosureInput = {
    whole_school: wholeSchool,
    ...(wholeSchool ? {} : { section_ids: [...chosen] }),
    closed_from: from,
    ...(to ? { closed_to: to } : {}),
    reason,
  };
  const ready = from !== "" && (wholeSchool || chosen.size > 0);

  async function check() {
    setPending(true);
    setError(undefined);
    try {
      setPreview({ input, out: await previewClosure(api, input) });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  async function confirm() {
    if (!preview) return;
    setPending(true);
    setError(undefined);
    try {
      const out = await sendClosure(
        api,
        { ...preview.input, preview_token: preview.out.preview_token },
        confirmKey,
      );
      setSent(out);
      setPreview(null);
      await queryClient.invalidateQueries({ queryKey: REACH_KEYS.closures });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const p = preview?.out;
  return (
    <Card title={t("formTitle")} description={t("formDescription")}>
      <div className="space-y-4">
        <fieldset aria-labelledby={legendId} className="space-y-2">
          <legend id={legendId} className="font-semibold text-ink">
            {t("audience")}
          </legend>
          {(["school", "sections"] as const).map((value) => (
            <div key={value} className="flex min-h-8 items-center gap-2">
              <input
                id={`closure-audience-${value}`}
                type="radio"
                name="closure-audience"
                className="size-5"
                checked={wholeSchool === (value === "school")}
                onChange={() => {
                  setWholeSchool(value === "school");
                  changed();
                }}
              />
              <label htmlFor={`closure-audience-${value}`}>{t(`audiences.${value}`)}</label>
            </div>
          ))}
          {!wholeSchool ? (
            <div className="grid gap-1 ps-7 sm:grid-cols-3">
              {sections.map((section) => (
                <div key={section.value} className="flex min-h-8 items-center gap-2">
                  <input
                    id={`closure-section-${section.value}`}
                    type="checkbox"
                    className="size-5"
                    checked={chosen.has(section.value)}
                    onChange={(event) => {
                      const next = new Set(chosen);
                      if (event.target.checked) next.add(section.value);
                      else next.delete(section.value);
                      setChosen(next);
                      changed();
                    }}
                  />
                  <label htmlFor={`closure-section-${section.value}`}>{section.label}</label>
                </div>
              ))}
            </div>
          ) : null}
        </fieldset>
        <div className="grid gap-4 sm:grid-cols-3">
          <TextField
            label={t("from")}
            type="date"
            value={from}
            min={today}
            max={addDays(today, MAX_DAYS_AHEAD)}
            required
            onChange={(event) => {
              setFrom(event.target.value);
              changed();
            }}
          />
          <TextField
            label={t("to")}
            hint={t("toHint")}
            type="date"
            value={to}
            min={from || today}
            onChange={(event) => {
              setTo(event.target.value);
              changed();
            }}
          />
          <SelectField
            label={t("reason")}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value as ClosureReason);
              changed();
            }}
            options={CLOSURE_REASONS.map((r) => ({ value: r, label: t(`reasons.${r}`) }))}
          />
        </div>
        <Button
          variant="secondary"
          disabled={!ready || pending}
          aria-disabled={!ready || pending || undefined}
          onClick={() => void check()}
        >
          {t("check")}
        </Button>
        <ApiErrorAlert error={error} namespace="messaging" />
        {p ? (
          <div className="space-y-3 rounded-md border border-border p-4">
            <p className="font-semibold text-ink">{t("messageLabel")}</p>
            <blockquote className="border-s-4 border-border ps-3 text-ink">
              {p.message_en}
            </blockquote>
            <p className="text-ink">
              {t("recipients", {
                recipients: count(p.recipients),
                guardians: count(p.guardians),
              })}
            </p>
            {Object.keys(p.skipped).length > 0 ? (
              <ul className="space-y-1 text-sm text-ink-muted">
                {Object.entries(p.skipped).map(([code, n]) => (
                  <li key={code}>
                    {tr.has(code as "no_optin") ? tr(code as "no_optin") : tr("other")}:{" "}
                    <span className="num">{count(n)}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="text-sm text-ink">
              {p.sends_at
                ? t("sendsAt", { when: formatDateTime(p.sends_at) ?? "" })
                : t("sendsNow")}{" "}
              {t("window", { start: hhmm(p.window_start), end: hhmm(p.window_end) })}
            </p>
            <p className="text-sm text-ink-muted">
              {t("cost", { cost: formatInr(p.estimated_cost_inr, locale) ?? "" })}
            </p>
            {p.recent_closures > 0 ? (
              <p className="text-sm text-ink-muted">{t("recent", { count: p.recent_closures })}</p>
            ) : null}
            {p.blocked_reason ? (
              <Alert tone="warning">
                {tr.has(p.blocked_reason as "no_optin")
                  ? tr(p.blocked_reason as "no_optin")
                  : t("blocked")}
              </Alert>
            ) : (
              <Button
                disabled={pending || p.recipients === 0}
                aria-disabled={pending || p.recipients === 0 || undefined}
                onClick={() => void confirm()}
              >
                {t("send", { count: p.recipients })}
              </Button>
            )}
          </div>
        ) : null}
        {sent ? (
          <Alert tone="success" live>
            {t("queued", { count: sent.queued })}
          </Alert>
        ) : null}
      </div>
    </Card>
  );
}

function RecentClosures() {
  const t = useTranslations("messaging.closures");
  const tm = useTranslations("messaging");
  const locale = useLocale() as Locale;
  const page = useClosures();
  if (page.status === "error" && (page.reason === "forbidden" || page.reason === "not_found")) {
    return null;
  }
  const count = (n: number | undefined) => formatCount(n ?? 0, locale) ?? "0";
  const rows: Loadable<readonly Closure[]> =
    page.status === "ready" ? { status: "ready", data: page.data.items } : page;
  const columns: ColumnV2<Closure>[] = [
    {
      key: "dates",
      header: t("columns.dates"),
      cell: (row) =>
        row.closed_from === row.closed_to
          ? formatDate(row.closed_from)
          : t("range", {
              from: formatDate(row.closed_from) ?? "",
              to: formatDate(row.closed_to) ?? "",
            }),
    },
    {
      key: "reason",
      header: t("columns.reason"),
      cell: (row) =>
        (CLOSURE_REASONS as readonly string[]).includes(row.reason)
          ? t(`reasons.${row.reason as ClosureReason}`)
          : row.reason,
    },
    {
      key: "audience",
      header: t("columns.audience"),
      cell: (row) =>
        row.whole_school
          ? t("audiences.school")
          : t("sectionCount", { count: row.section_ids.length }),
    },
    ...(["queued", "sent", "delivered", "read", "failed"] as const).map(
      (status): ColumnV2<Closure> => ({
        key: status,
        header: tm(`log.statuses.${status}`),
        numeric: true,
        cell: (row) => <Value>{count(row.counts[status])}</Value>,
      }),
    ),
    {
      key: "sentAt",
      header: t("columns.sentAt"),
      cell: (row) => formatDateTime(row.sent_at),
    },
  ];
  return (
    <Card
      title={t("recentTitle")}
      description={
        page.status === "ready"
          ? t("uses", { count: page.data.uses, days: page.data.uses_window_days })
          : undefined
      }
    >
      <DataTableV2
        id="closures"
        caption={t("recentTitle")}
        columns={columns}
        state={rows}
        rowKey={(row) => row.id}
        stacked
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </Card>
  );
}

/** /messages/closures: send an urgent closure message and see the recent ones. */
export function ClosureScreen() {
  const t = useTranslations("messaging.closures");
  const tm = useTranslations("messaging");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const closures = useClosures(me !== undefined && can(READ));
  const sender = can(CLOSURE_SEND);
  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tm("logTitle"), href: "/messages" },
          { label: t("title") },
        ]}
      />
      {me !== undefined && !sender && !can(READ) ? (
        <EmptyState icon="lock" title={tm("noAccessTitle")} body={tm("noAccessBody")} />
      ) : isUnavailable(closures) ? (
        <EmptyState icon="search" title={t("unavailableTitle")} body={t("unavailableBody")} />
      ) : (
        <>
          {sender ? <ClosureForm /> : <Alert tone="info">{t("principalOnly")}</Alert>}
          {can(READ) ? <RecentClosures /> : null}
        </>
      )}
    </div>
  );
}
