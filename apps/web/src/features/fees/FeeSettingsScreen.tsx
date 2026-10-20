"use client";

import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { SelectField } from "@/components/ui/Select";
import { useMessagingSettings } from "@/features/messaging/data";
import { unwrap, useApiMutation, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { FINANCE_READ, KEYS, MANAGE, ifMatch } from "./data";
import { FeesGate, NoAccess } from "./parts";
import type { components } from "@schoolos/api-client";

type FeeSettings = components["schemas"]["FeeSettingsOut"];
type When = "before" | "on" | "after";
interface DayRow {
  key: number;
  when: When;
  days: string;
}

const MAX_DAYS = 5;
const MAX_OFFSET = 60;
const MONEY = /^\d{1,12}(\.\d{1,2})?$/;

function toRows(offsets: readonly number[]): DayRow[] {
  return [...offsets]
    .sort((a, b) => a - b)
    .map((offset, index) => ({
      key: index,
      when: offset < 0 ? "before" : offset === 0 ? "on" : "after",
      days: offset === 0 ? "" : String(Math.abs(offset)),
    }));
}

/** The offset of a row (negative before the due date), or null when its days are not valid. */
function offsetOf(row: DayRow): number | null {
  if (row.when === "on") return 0;
  if (!/^\d{1,2}$/.test(row.days.trim())) return null;
  const days = Number(row.days);
  if (days < 1 || days > MAX_OFFSET) return null;
  return row.when === "before" ? -days : days;
}

/** "3 days before" / "On the due date" / "7 days after". */
export function OffsetText({ offset }: { offset: number }) {
  const t = useTranslations("fees.settings");
  if (offset === 0) return <>{t("valueOn")}</>;
  return (
    <>{offset < 0 ? t("valueBefore", { days: -offset }) : t("valueAfter", { days: offset })}</>
  );
}

function SettingsForm({ settings }: { settings: FeeSettings }) {
  const t = useTranslations("fees.settings");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const formId = useId();
  const [paper, setPaper] = useState(settings.paper_size);
  const [gstNote, setGstNote] = useState(settings.gst_note);
  const [rows, setRows] = useState<DayRow[]>(() => toRows(settings.reminder_offsets));
  const [nextKey, setNextKey] = useState(settings.reminder_offsets.length);
  const [minimum, setMinimum] = useState(settings.min_reminder_amount);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [saved, setSaved] = useState(false);
  const id = (name: string) => `${formId}-${name}`;

  const original = toRows(settings.reminder_offsets);
  const dirty =
    paper !== settings.paper_size ||
    gstNote !== settings.gst_note ||
    minimum !== settings.min_reminder_amount ||
    JSON.stringify(rows.map(offsetOf)) !== JSON.stringify(original.map(offsetOf)) ||
    rows.length !== original.length;

  const save = useApiMutation(
    (body: {
      paper_size: FeeSettings["paper_size"];
      gst_note: string;
      reminder_offsets: number[];
      min_reminder_amount: string;
    }) =>
      unwrap(
        api.PUT("/api/v1/fees/settings", {
          body,
          headers: { "If-Match": ifMatch(settings.version) },
        }),
      ),
    { invalidate: [KEYS.settings], onSuccess: () => setSaved(true) },
  );

  function discard() {
    setPaper(settings.paper_size);
    setGstNote(settings.gst_note);
    setRows(toRows(settings.reminder_offsets));
    setMinimum(settings.min_reminder_amount);
    setErrors({});
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved(false);
    const next: Record<string, string> = {};
    const note = gstNote.trim();
    if (note.length < 1 || note.length > 200) next.gst = t("errors.gstNote");
    const seen = new Set<number>();
    const offsets: number[] = [];
    rows.forEach((row, index) => {
      const offset = offsetOf(row);
      if (offset === null) {
        next[`day-${row.key}`] = t("errors.days");
        return;
      }
      if (seen.has(offset)) {
        next[`day-${row.key}`] = t("errors.repeated");
        return;
      }
      seen.add(offset);
      offsets[index] = offset;
    });
    if (!MONEY.test(minimum.trim())) next.minimum = t("errors.minimum");
    setErrors(next);
    if (Object.keys(next).length > 0) {
      setAttempt((n) => n + 1);
      return;
    }
    save.mutate({
      paper_size: paper,
      gst_note: note,
      reminder_offsets: offsets.filter((o) => o !== undefined),
      min_reminder_amount: minimum.trim(),
    });
  }

  const summary: SummaryError[] = [
    ...(errors.gst ? [{ fieldId: id("gst"), message: errors.gst }] : []),
    ...rows.flatMap((row) => {
      const message = errors[`day-${row.key}`];
      return message
        ? [{ fieldId: id(row.when === "on" ? `when-${row.key}` : `days-${row.key}`), message }]
        : [];
    }),
    ...(errors.minimum ? [{ fieldId: id("minimum"), message: errors.minimum }] : []),
  ];

  return (
    <form onSubmit={onSubmit} noValidate aria-label={t("title")} className="space-y-5">
      {summary.length > 0 ? <ErrorSummary errors={summary} attempt={attempt} /> : null}
      <Card title={t("receiptsTitle")}>
        <div className="mt-2 space-y-4">
          <SelectField
            id={id("paper")}
            name="paper_size"
            className="max-w-60"
            label={t("paperSize")}
            value={paper}
            onChange={(event) => setPaper(event.target.value as FeeSettings["paper_size"])}
            options={[
              { value: "A5", label: t("paperA5") },
              { value: "A4", label: t("paperA4") },
            ]}
          />
          <TextAreaField
            id={id("gst")}
            name="gst_note"
            label={t("gstNote")}
            hint={t("gstNoteHint")}
            error={errors.gst}
            maxLength={200}
            rows={2}
            value={gstNote}
            onChange={(event) => setGstNote(event.target.value)}
          />
        </div>
      </Card>
      <Card title={t("remindersTitle")} description={t("remindersDescription")}>
        <div className="mt-2 space-y-4">
          {rows.length === 0 ? <p className="text-sm text-ink-muted">{t("noDays")}</p> : null}
          <ol className="space-y-3">
            {rows.map((row, index) => (
              <li key={row.key}>
                <fieldset className="flex flex-wrap items-end gap-3">
                  <legend className="sr-only">{t("reminderLegend", { n: index + 1 })}</legend>
                  <SelectField
                    id={id(`when-${row.key}`)}
                    name={`when-${row.key}`}
                    className="w-56"
                    label={t("when")}
                    value={row.when}
                    error={row.when === "on" ? errors[`day-${row.key}`] : undefined}
                    onChange={(event) =>
                      setRows((current) =>
                        current.map((r) =>
                          r.key === row.key ? { ...r, when: event.target.value as When } : r,
                        ),
                      )
                    }
                    options={[
                      { value: "before", label: t("before") },
                      { value: "on", label: t("on") },
                      { value: "after", label: t("after") },
                    ]}
                  />
                  {row.when !== "on" ? (
                    <TextField
                      id={id(`days-${row.key}`)}
                      name={`days-${row.key}`}
                      className="w-28"
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={MAX_OFFSET}
                      label={t("days")}
                      error={errors[`day-${row.key}`]}
                      value={row.days}
                      onChange={(event) =>
                        setRows((current) =>
                          current.map((r) =>
                            r.key === row.key ? { ...r, days: event.target.value } : r,
                          ),
                        )
                      }
                    />
                  ) : null}
                  <Button
                    variant="ghost"
                    onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
                  >
                    {t("removeDay", { n: index + 1 })}
                  </Button>
                </fieldset>
              </li>
            ))}
          </ol>
          {rows.length < MAX_DAYS ? (
            <Button
              variant="secondary"
              onClick={() => {
                setRows((current) => [...current, { key: nextKey, when: "after", days: "" }]);
                setNextKey((n) => n + 1);
              }}
            >
              {t("addDay")}
            </Button>
          ) : null}
          <TextField
            id={id("minimum")}
            name="min_reminder_amount"
            className="max-w-48"
            label={t("minimum")}
            hint={t("minimumHint")}
            error={errors.minimum}
            inputMode="decimal"
            autoComplete="off"
            value={minimum}
            onChange={(event) => setMinimum(event.target.value)}
          />
        </div>
      </Card>
      {saved ? (
        <Alert tone="success" live>
          {t("saved")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={save.error ?? undefined} namespace="fees" />
      <SaveBar dirty={dirty}>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? tc("working") : t("save")}
        </Button>
        <Button variant="secondary" disabled={!dirty || save.isPending} onClick={discard}>
          {t("discard")}
        </Button>
      </SaveBar>
    </form>
  );
}

function ReadOnly({ settings }: { settings: FeeSettings }) {
  const t = useTranslations("fees.settings");
  return (
    <Card title={t("title")} description={t("readOnly")}>
      <dl className="mt-2 grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-sm text-ink-muted">{t("paperSize")}</dt>
          <dd>{settings.paper_size === "A4" ? t("paperA4") : t("paperA5")}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("gstNote")}</dt>
          <dd>{settings.gst_note}</dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("remindersTitle")}</dt>
          <dd>
            {settings.reminder_offsets.length === 0 ? (
              t("noDays")
            ) : (
              <ul>
                {[...settings.reminder_offsets]
                  .sort((a, b) => a - b)
                  .map((offset) => (
                    <li key={offset} className="num">
                      <OffsetText offset={offset} />
                    </li>
                  ))}
              </ul>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-sm text-ink-muted">{t("minimum")}</dt>
          <dd className="num">{settings.min_reminder_amount}</dd>
        </div>
      </dl>
    </Card>
  );
}

/**
 * `/settings/fees` (R2-3J; FR-MSG-014, US-2205; FR-FEE-011; docs/17 §5.8): the receipt paper
 * size and GST line, and the days around each due date on which the automatic WhatsApp fee
 * reminder goes, with the smallest amount worth a reminder. Explicit save (SaveBar, If-Match);
 * changing needs `fee.manage` (audited `fee.settings.updated` with the days); finance readers
 * see the values.
 */
export function FeeSettingsScreen() {
  const t = useTranslations("fees.settings");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const me = useStaffMe();
  const allowed = can([FINANCE_READ, MANAGE]);
  const api = useBffClient("staff");
  const settings = useApiQuery(KEYS.settings, () => unwrap(api.GET("/api/v1/fees/settings")), {
    enabled: me !== undefined && allowed,
  });
  const messaging = useMessagingSettings(me !== undefined && can("message.read"));
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
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
    <div className="space-y-5">
      {header}
      {messaging.status === "ready" && !messaging.data.fee_reminders_enabled ? (
        <Alert tone="info">{t("remindersOff")}</Alert>
      ) : null}
      <FeesGate data={settings}>
        {(s) =>
          can(MANAGE) ? <SettingsForm key={s.version} settings={s} /> : <ReadOnly settings={s} />
        }
      </FeesGate>
    </div>
  );
}
