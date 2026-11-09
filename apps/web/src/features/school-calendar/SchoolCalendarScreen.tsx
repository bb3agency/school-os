"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { todayInIndia } from "@/features/change-requests/dates";
import { useStructureLists } from "@/features/academic-structure/data";
import { ApiError, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { typedDateToIso, useDateInput } from "@/lib/date-format";
import { formatDate } from "@/lib/format";
import { formList } from "@/lib/forms";
import type { Loadable } from "@/lib/loadable";
import * as z from "@/lib/zod";
import {
  CALENDAR_MANAGE,
  ENTRY_KINDS,
  SATURDAY_NUMBERS,
  SCHOOL_CALENDAR_KEYS,
  WEEKDAYS,
  addDays,
  addEntry,
  monthOf,
  removeEntry,
  savePattern,
  shiftMonth,
  useCalendarMonth,
  useCalendarSuggestions,
  useSchoolCalendar,
  weekdayOf,
  type CalendarDay,
  type CalendarEntry,
  type CalendarEntryAdded,
  type CalendarEntryKind,
  type CalendarSuggestion,
  type SchoolCalendar,
} from "./data";

const dateField = z
  .string()
  .trim()
  .refine((value) => typedDateToIso(value) !== null, { error: "invalidDate" })
  .transform((value) => typedDateToIso(value) ?? "");

export const entrySchema = z
  .object({
    kind: z.enum(ENTRY_KINDS as [CalendarEntryKind, ...CalendarEntryKind[]]),
    name: z.string().trim().min(1, { error: "required" }).max(80, { error: "tooLong" }),
    starts_on: dateField,
    ends_on: dateField,
    class_id: z.string().trim(),
    suggestion: z.string().trim(),
  })
  .refine((data) => data.ends_on >= data.starts_on, { path: ["ends_on"], error: "invalidDate" });

const patternSchema = z.object({
  weekly_off: z.array(z.number()),
  saturdays_off: z.array(z.number()),
});

/** The weekly pattern (FR-CAL-001): weekdays off and Saturdays of the month off. */
function PatternDialog({ yearId, calendar }: { yearId: string; calendar: SchoolCalendar }) {
  const t = useTranslations("schoolCalendar");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  return (
    <ActionDialog
      triggerLabel={calendar.set_up ? t("pattern.change") : t("pattern.setUp")}
      triggerVariant={calendar.set_up ? "secondary" : "primary"}
      title={t("pattern.title")}
      description={t("pattern.description")}
      confirmLabel={t("pattern.save")}
      consequence={calendar.set_up ? undefined : t("pattern.consequence")}
      schema={patternSchema}
      errorNamespace="schoolCalendar"
      invalidate={[SCHOOL_CALENDAR_KEYS.all]}
      extra={(form) => ({
        weekly_off: formList(form, "weekly_off").map(Number),
        saturdays_off: formList(form, "saturdays_off").map(Number),
      })}
      submit={async (data) => {
        try {
          return await savePattern(api, yearId, calendar.version, data);
        } catch (failure) {
          if (failure instanceof ApiError && failure.status === 412) {
            void queryClient.invalidateQueries({ queryKey: SCHOOL_CALENDAR_KEYS.all });
          }
          throw failure;
        }
      }}
    >
      {() => (
        <>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("pattern.weeklyOff")}</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {WEEKDAYS.map((day) => (
                <label key={day} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="weekly_off"
                    value={day}
                    defaultChecked={calendar.weekly_off.includes(day)}
                    className="size-4 accent-primary"
                  />
                  {t(`weekday.${day}`)}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("pattern.saturdaysOff")}</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {SATURDAY_NUMBERS.map((n) => (
                <label key={n} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="saturdays_off"
                    value={n}
                    defaultChecked={calendar.saturdays_off.includes(n)}
                    className="size-4 accent-primary"
                  />
                  {t(`saturday.${n}`)}
                </label>
              ))}
            </div>
          </fieldset>
        </>
      )}
    </ActionDialog>
  );
}

interface EntryDefaults {
  kind: CalendarEntryKind;
  name: string;
  starts_on: string;
  ends_on: string;
  suggestion: string;
}

/** Add a holiday, vacation or extra working day (FR-CAL-002, FR-CAL-003, FR-CAL-014). */
function AddEntryDialog({
  yearId,
  classes,
  defaults,
  triggerLabel,
  triggerVariant = "secondary",
  triggerDescription,
}: {
  yearId: string;
  classes: ReadonlyArray<{ id: string; label: string }>;
  defaults: EntryDefaults;
  triggerLabel: string;
  triggerVariant?: "primary" | "secondary" | "ghost";
  triggerDescription?: string | undefined;
}) {
  const t = useTranslations("schoolCalendar");
  const api = useBffClient("staff");
  const dates = useDateInput();
  const hint = dates.hint(defaults.starts_on);
  const [marks, setMarks] = useState<number | null>(null);
  return (
    <>
      <ActionDialog
        triggerLabel={triggerLabel}
        triggerVariant={triggerVariant}
        triggerSize="sm"
        {...(triggerDescription ? { triggerDescription } : {})}
        title={t("add.title")}
        description={defaults.suggestion ? t("add.suggestionNote") : t("add.description")}
        confirmLabel={t("add.save")}
        schema={entrySchema}
        errorNamespace="schoolCalendar"
        invalidate={[SCHOOL_CALENDAR_KEYS.all]}
        onSuccess={(result: CalendarEntryAdded) => setMarks(result.attendance_marks)}
        submit={(data, key) =>
          addEntry(
            api,
            yearId,
            {
              kind: data.kind,
              name: data.name,
              starts_on: data.starts_on,
              ends_on: data.ends_on,
              class_id: data.class_id === "" ? null : data.class_id,
              suggestion: data.suggestion === "" ? null : data.suggestion,
            },
            key,
          )
        }
      >
        {(errors) => (
          <>
            <input type="hidden" name="suggestion" value={defaults.suggestion} readOnly />
            <SelectField
              name="kind"
              label={t("add.kind")}
              defaultValue={defaults.kind}
              error={errors.kind}
              options={ENTRY_KINDS.map((kind) => ({ value: kind, label: t(`entryKind.${kind}`) }))}
            />
            <TextField
              name="name"
              label={t("add.name")}
              hint={t("add.nameHint")}
              maxLength={80}
              autoComplete="off"
              defaultValue={defaults.name}
              error={errors.name}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                name="starts_on"
                label={t("add.from")}
                hint={t("add.dateHint", hint)}
                inputMode="numeric"
                autoComplete="off"
                placeholder={dates.placeholder}
                defaultValue={dates.fromIso(defaults.starts_on)}
                error={errors.starts_on ? t("add.dateInvalid", hint) : undefined}
              />
              <TextField
                name="ends_on"
                label={t("add.to")}
                hint={t("add.dateHint", hint)}
                inputMode="numeric"
                autoComplete="off"
                placeholder={dates.placeholder}
                defaultValue={dates.fromIso(defaults.ends_on)}
                error={errors.ends_on ? t("add.toInvalid", hint) : undefined}
              />
            </div>
            <SelectField
              name="class_id"
              label={t("add.appliesTo")}
              hint={t("add.appliesToHint")}
              defaultValue=""
              error={errors.class_id}
              options={[
                { value: "", label: t("wholeSchool") },
                ...classes.map((c) => ({ value: c.id, label: c.label })),
              ]}
            />
          </>
        )}
      </ActionDialog>
      {marks !== null && marks > 0 ? (
        <Alert tone="warning" live>
          {t("add.marksWarning", { count: marks })}
        </Alert>
      ) : null}
    </>
  );
}

function RemoveEntryDialog({ yearId, entry }: { yearId: string; entry: CalendarEntry }) {
  const t = useTranslations("schoolCalendar");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("remove.open")}
      triggerVariant="ghost"
      triggerSize="sm"
      triggerDescription={entry.name}
      title={t("remove.title")}
      description={t("remove.description", { name: entry.name })}
      confirmLabel={t("remove.confirm")}
      confirmVariant="danger"
      consequence={t("remove.consequence")}
      schema={z.object({})}
      errorNamespace="schoolCalendar"
      invalidate={[SCHOOL_CALENDAR_KEYS.all]}
      submit={() => removeEntry(api, yearId, entry.id)}
    />
  );
}

function dayLabel(t: ReturnType<typeof useTranslations>, day: CalendarDay): string {
  return day.name ? `${t(`dayKind.${day.kind}`)}: ${day.name}` : t(`dayKind.${day.kind}`);
}

/**
 * The month as a keyboard grid (FR-CAL-020): one tab stop; arrow keys move a day or a week,
 * Home and End go to the start and end of the week, Page Up and Page Down change the month.
 * Every cell says its kind in words (colour is never the only signal).
 */
function MonthGrid({
  days,
  selected,
  onSelect,
  onMonth,
}: {
  days: readonly CalendarDay[];
  selected: string;
  onSelect: (iso: string) => void;
  onMonth: (by: number, focus: string) => void;
}) {
  const t = useTranslations("schoolCalendar");
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const focusNext = useRef(false);
  useEffect(() => {
    const target = refs.current.get(selected);
    if (focusNext.current && target) {
      target.focus();
      focusNext.current = false;
    }
  }, [selected, days]);
  const first = days[0];
  if (!first) return null;
  const lead = weekdayOf(first.on_date);
  const cells: Array<CalendarDay | null> = [...Array<null>(lead).fill(null), ...days];
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks: Array<Array<CalendarDay | null>> = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  const inMonth = new Set(days.map((d) => d.on_date));

  function move(event: KeyboardEvent<HTMLButtonElement>, iso: string) {
    const steps: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
      Home: -weekdayOf(iso),
      End: 6 - weekdayOf(iso),
    };
    if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      focusNext.current = true;
      const by = event.key === "PageUp" ? -1 : 1;
      onMonth(by, `${shiftMonth(monthOf(iso), by)}-${iso.slice(8)}`);
      return;
    }
    const step = steps[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const target = addDays(iso, step);
    focusNext.current = true;
    if (inMonth.has(target)) onSelect(target);
    else onMonth(target < iso ? -1 : 1, target);
  }

  return (
    <div role="grid" aria-label={t("gridLabel")} className="w-full space-y-1">
      <div role="rowgroup">
        <div role="row" className="grid grid-cols-7 gap-1">
          {WEEKDAYS.map((d) => (
            <div
              key={d}
              role="columnheader"
              className="py-1 text-center text-xs font-semibold text-ink-muted"
            >
              <abbr title={t(`weekday.${d}`)}>{t(`weekdayShort.${d}`)}</abbr>
            </div>
          ))}
        </div>
      </div>
      <div role="rowgroup" className="space-y-1">
        {weeks.map((week, w) => (
          <div key={w} role="row" className="grid grid-cols-7 gap-1">
            {week.map((day, i) =>
              day === null ? (
                <div key={`blank-${w}-${i}`} role="gridcell" />
              ) : (
                <div key={day.on_date} role="gridcell">
                  <button
                    type="button"
                    ref={(node) => {
                      if (node) refs.current.set(day.on_date, node);
                      else refs.current.delete(day.on_date);
                    }}
                    tabIndex={day.on_date === selected ? 0 : -1}
                    aria-pressed={day.on_date === selected}
                    aria-label={`${formatDate(day.on_date) ?? day.on_date}, ${dayLabel(t, day)}`}
                    onClick={() => onSelect(day.on_date)}
                    onKeyDown={(event) => move(event, day.on_date)}
                    className={[
                      "flex min-h-16 w-full flex-col items-start gap-0.5 rounded-md border px-1.5 py-1 text-left text-xs",
                      "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                      day.working
                        ? "border-border bg-surface text-ink"
                        : "border-transparent bg-canvas text-ink-muted",
                      day.on_date === selected ? "ring-2 ring-primary" : "",
                    ].join(" ")}
                  >
                    <span className="num text-sm font-semibold">
                      {Number(day.on_date.slice(8))}
                    </span>
                    {day.kind === "working" ? null : (
                      <span className="line-clamp-2 break-words">
                        {day.name ?? t(`dayKind.${day.kind}`)}
                      </span>
                    )}
                  </button>
                </div>
              ),
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function dataOf<T>(value: Loadable<T>): T | undefined {
  return value.status === "ready" ? value.data : undefined;
}

function clampMonth(month: string, calendar: SchoolCalendar | undefined): string {
  if (!calendar) return month;
  const first = monthOf(calendar.starts_on);
  const last = monthOf(calendar.ends_on);
  if (month < first) return first;
  if (month > last) return last;
  return month;
}

/**
 * School calendar (FT-06; FR-CAL-001..FR-CAL-020): the year's weekly pattern, holidays,
 * vacations and extra working days, as a month grid and a list. Everyone who can read students
 * sees it; holders of tenant.structure.manage set it up and change it. Holiday suggestions are
 * unverified: the school checks the dates before adding them.
 */
export function SchoolCalendarScreen() {
  const t = useTranslations("schoolCalendar");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const manage = can(CALENDAR_MANAGE);
  const { years, classes } = useStructureLists();
  const yearList = dataOf(years) ?? [];
  const [yearChoice, setYearChoice] = useState<string | null>(null);
  const yearId = yearChoice ?? yearList.find((y) => y.is_current)?.id ?? yearList[0]?.id ?? null;
  const [classId, setClassId] = useState("");
  const today = todayInIndia();
  const calendar = useSchoolCalendar(yearId);
  const [monthChoice, setMonthChoice] = useState<string | null>(null);
  const data = dataOf(calendar);
  const month = clampMonth(monthChoice ?? monthOf(today), data);
  const monthView = useCalendarMonth(yearId, month, classId);
  const monthData = dataOf(monthView);
  const [selectedChoice, setSelected] = useState<string | null>(null);
  const days = monthData?.days ?? [];
  const selected =
    selectedChoice && days.some((d) => d.on_date === selectedChoice)
      ? selectedChoice
      : (days.find((d) => d.on_date === today)?.on_date ?? days[0]?.on_date ?? "");
  const selectedDay = days.find((d) => d.on_date === selected);
  const suggestions = useCalendarSuggestions(yearId, manage && Boolean(data?.set_up));
  const suggestionData = dataOf(suggestions);
  const classList = dataOf(classes);
  const classOptions = useMemo(
    () => (classList ?? []).map((c) => ({ id: c.id, label: c.display_en })),
    [classList],
  );
  const classLabel = (id: string | null) =>
    id === null ? t("wholeSchool") : (classOptions.find((c) => c.id === id)?.label ?? t("aClass"));
  const monthTitle = new Date(`${month}-01T00:00:00Z`).toLocaleDateString("en-IN", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });

  function changeMonth(by: number, focus?: string) {
    const next = clampMonth(shiftMonth(month, by), data);
    setMonthChoice(next);
    if (focus && monthOf(focus) === next) setSelected(focus);
    else setSelected(null);
  }

  const range = (entry: Pick<CalendarEntry, "starts_on" | "ends_on">) =>
    entry.starts_on === entry.ends_on
      ? (formatDate(entry.starts_on) ?? entry.starts_on)
      : t("range", {
          from: formatDate(entry.starts_on) ?? entry.starts_on,
          to: formatDate(entry.ends_on) ?? entry.ends_on,
        });

  const fromSuggestion = (s: CalendarSuggestion): EntryDefaults => ({
    kind: s.kind,
    name: s.name,
    starts_on: s.starts_on,
    ends_on: s.ends_on,
    suggestion: s.code,
  });

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: tn("structure"), href: "/settings/structure" },
          { label: t("title") },
        ]}
        actions={
          manage && data && yearId ? <PatternDialog yearId={yearId} calendar={data} /> : null
        }
      />
      <div className="flex flex-wrap items-end gap-4">
        <SelectField
          name="year"
          label={t("year")}
          value={yearId ?? ""}
          onChange={(event) => {
            setYearChoice(event.target.value);
            setMonthChoice(null);
            setSelected(null);
          }}
          options={yearList.map((y) => ({ value: y.id, label: y.label }))}
        />
        <SelectField
          name="class"
          label={t("showFor")}
          value={classId}
          onChange={(event) => setClassId(event.target.value)}
          options={[
            { value: "", label: t("wholeSchool") },
            ...classOptions.map((c) => ({ value: c.id, label: c.label })),
          ]}
        />
      </div>
      {calendar.status === "error" ? <Alert tone="danger">{t("loadError")}</Alert> : null}
      {data && !data.set_up ? (
        <Alert tone="info" title={t("notSetUpTitle")}>
          <p>{manage ? t("notSetUpManage") : t("notSetUpRead")}</p>
        </Alert>
      ) : null}
      {data?.set_up && data.working_days !== null ? (
        <p className="text-sm text-ink">{t("yearWorkingDays", { count: data.working_days })}</p>
      ) : null}

      <section
        aria-labelledby="calendar-month"
        className="space-y-3 rounded-xl border border-border bg-surface p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="calendar-month" className="text-lg font-semibold text-ink">
            {monthTitle}
          </h2>
          <div className="flex items-center gap-2">
            {monthData ? (
              <span className="text-sm text-ink-muted">
                {t("monthWorkingDays", { count: monthData.working_days })}
              </span>
            ) : null}
            <Button variant="secondary" size="sm" onClick={() => changeMonth(-1)}>
              {t("previousMonth")}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => changeMonth(1)}>
              {t("nextMonth")}
            </Button>
          </div>
        </div>
        <p className="text-xs text-ink-muted">{t("gridHelp")}</p>
        {monthView.status === "error" ? (
          <Alert tone="danger">{t("loadError")}</Alert>
        ) : (
          <MonthGrid days={days} selected={selected} onSelect={setSelected} onMonth={changeMonth} />
        )}
        {selectedDay ? (
          <div
            className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3"
            aria-live="polite"
          >
            <p className="text-sm text-ink">
              <span className="num font-semibold">{formatDate(selectedDay.on_date)}</span>
              {": "}
              {dayLabel(t, selectedDay)}{" "}
              <Badge tone={selectedDay.working ? "success" : "neutral"}>
                {selectedDay.working ? t("schoolDay") : t("dayOff")}
              </Badge>
            </p>
            {manage && data?.set_up && yearId && selectedDay.kind !== "outside_year" ? (
              <AddEntryDialog
                yearId={yearId}
                classes={classOptions}
                triggerLabel={selectedDay.working ? t("add.holidayOnDay") : t("add.workingOnDay")}
                defaults={{
                  kind: selectedDay.working ? "holiday" : "working_day",
                  name: "",
                  starts_on: selectedDay.on_date,
                  ends_on: selectedDay.on_date,
                  suggestion: "",
                }}
              />
            ) : null}
          </div>
        ) : null}
      </section>

      {data?.set_up && yearId ? (
        <section aria-labelledby="calendar-entries" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="calendar-entries" className="text-lg font-semibold text-ink">
              {t("entriesTitle")}
            </h2>
            {manage ? (
              <AddEntryDialog
                yearId={yearId}
                classes={classOptions}
                triggerLabel={t("add.open")}
                triggerVariant="primary"
                defaults={{
                  kind: "holiday",
                  name: "",
                  starts_on: today,
                  ends_on: today,
                  suggestion: "",
                }}
              />
            ) : null}
          </div>
          {data.entries.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("noEntries")}</p>
          ) : (
            <ul className="divide-y divide-border rounded-xl border border-border bg-surface">
              {data.entries.map((entry) => (
                <li
                  key={entry.id}
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2"
                >
                  <span className="text-sm">
                    <span className="block font-semibold text-ink">{entry.name}</span>
                    <span className="block text-ink-muted">
                      <span className="num">{range(entry)}</span> · {t(`entryKind.${entry.kind}`)} ·{" "}
                      {classLabel(entry.class_id)}
                    </span>
                  </span>
                  {manage ? <RemoveEntryDialog yearId={yearId} entry={entry} /> : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      {manage && data?.set_up && yearId && suggestionData ? (
        <section aria-labelledby="calendar-suggestions" className="space-y-3">
          <h2 id="calendar-suggestions" className="text-lg font-semibold text-ink">
            {t("suggestions.title")}
          </h2>
          <Alert tone="warning" title={t("suggestions.unverifiedTitle")}>
            <p>{t("suggestions.unverifiedBody")}</p>
          </Alert>
          {suggestionData.items.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("suggestions.none")}</p>
          ) : (
            <ul className="divide-y divide-border rounded-xl border border-border bg-surface">
              {suggestionData.items.map((s) => (
                <li
                  key={s.code}
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2"
                >
                  <span className="text-sm">
                    <span className="block font-semibold text-ink">{s.name}</span>
                    <span className="block text-ink-muted">
                      <span className="num">{range(s)}</span> · {s.group_label}
                    </span>
                  </span>
                  <AddEntryDialog
                    yearId={yearId}
                    classes={classOptions}
                    triggerLabel={t("suggestions.check")}
                    triggerDescription={s.name}
                    defaults={fromSuggestion(s)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  );
}
