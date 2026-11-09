"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { ApiError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import {
  ATTENDANCE_STATUSES,
  KEYS,
  PERM,
  REGISTER_CODE,
  todayIst,
  useAttendanceDay,
  useAttendanceMonth,
  type AttendanceDay,
  type AttendanceEntry,
  type AttendanceSheet,
  type AttendanceStatus,
} from "./data";
import { LoadGate, ProblemList, SectionPicker } from "./parts";
import { SheetImport } from "./SheetImport";
import { useUnsavedChangesWarning } from "@/features/sheets/unsaved";
import { DialogCloseButton } from "@/components/ui/DialogCloseButton";
import type { CalendarDay } from "@/features/school-calendar/data";

/** One day of the register as a form: every student gets one status (US-1701). */
function DayForm({
  day,
  canRecord,
  onUnsavedChange,
}: {
  day: AttendanceDay;
  canRecord: boolean;
  /** How many students' statuses are not saved yet (0 once saved or unmounted). */
  onUnsavedChange: (count: number) => void;
}) {
  const t = useTranslations("attendance");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  // What the register says now (unmarked students start as present). Keyed by section and
  // date in the parent, so a new day starts from its own statuses.
  const baseline: Record<string, AttendanceStatus> = {};
  for (const row of day.students) baseline[row.student.student_id] = row.status ?? "present";
  const [statuses, setStatuses] = useState<Record<string, AttendanceStatus>>(() => ({
    ...baseline,
  }));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<number | null>(null);
  // Students whose status this person chose (YA-01): kept over a reload after a conflict.
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const [conflict, setConflict] = useState(false);
  const reloading = useRef(false);
  // YA-01: after 412 attendance_changed the day is read again; once it arrives, the register
  // shows the other person's statuses and keeps only the ones this person chose.
  useEffect(() => {
    if (!reloading.current) return;
    reloading.current = false;
    setStatuses((mine) => {
      const next: Record<string, AttendanceStatus> = {};
      for (const row of day.students) {
        const id = row.student.student_id;
        next[id] = touched.has(id) ? (mine[id] ?? "present") : (row.status ?? "present");
      }
      return next;
    });
  }, [day, touched]);
  // XA-06: only the students whose status differs from what the server holds (all of them on
  // a day not marked yet). The API upserts per student and date and never clears the ones
  // left out, so a correction does not overwrite what someone else marked for other students.
  const entries: AttendanceEntry[] = day.students
    .filter((row) => row.status === null || statuses[row.student.student_id] !== row.status)
    .map((row) => ({
      student_id: row.student.student_id,
      on_date: day.on_date,
      status: statuses[row.student.student_id] ?? "present",
      // YA-01: the version read, so a save cannot overwrite someone else's change unseen.
      expected_version: row.version,
    }));
  const unsaved = canRecord ? entries.length : 0;
  useEffect(() => {
    onUnsavedChange(unsaved);
  }, [onUnsavedChange, unsaved]);
  useEffect(() => () => onUnsavedChange(0), [onUnsavedChange]);
  const tcal = useTranslations("schoolCalendar");
  // XA-05: closing, reloading or leaving the page asks first while statuses are unsaved.
  useUnsavedChangesWarning(unsaved > 0, t("unsavedLeave"));

  if (day.students.length === 0) {
    return <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />;
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setSaved(null);
    setConflict(false);
    if (entries.length === 0) {
      setSaved(0);
      return;
    }
    setPending(true);
    try {
      const out = await unwrap(
        api.POST("/api/v1/sections/{section_id}/attendance", {
          params: { path: { section_id: day.section.id } },
          body: { entries, source: "mark" },
        }),
      );
      setSaved(out.written);
      setTouched(new Set());
      await queryClient.invalidateQueries({ queryKey: KEYS.attendance });
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "attendance_changed") {
        setConflict(true);
        reloading.current = true;
        await queryClient.invalidateQueries({ queryKey: KEYS.attendance });
      } else {
        setError(failure);
      }
    } finally {
      setPending(false);
    }
  }

  // FT-06 (FR-CAL-010): the day in the school calendar. Not in the generated client yet,
  // so read through a local type until the lead regenerates it.
  const dayOff = (day as typeof day & { calendar?: CalendarDay | null }).calendar ?? null;
  const changed = day.students.some(
    (row) => statuses[row.student.student_id] !== baseline[row.student.student_id],
  );
  // A day not marked yet is not saved, even when everyone is present.
  const dirty = entries.length > 0;
  const count = (status: AttendanceStatus) =>
    Object.values(statuses).filter((value) => value === status).length;
  // "32 present, 3 absent" (late and leave only when someone is).
  const tally = ATTENDANCE_STATUSES.filter(
    (status) => status === "present" || status === "absent" || count(status) > 0,
  ).map((status, index) => (
    <span key={status}>
      {index > 0 ? ", " : null}
      <span className="num font-semibold">{t(`tally.${status}`, { count: count(status) })}</span>
    </span>
  ));

  return (
    <form onSubmit={(event) => void save(event)} noValidate className="space-y-4">
      {dayOff && !dayOff.working ? (
        <Alert tone="warning" title={tcal("attendanceWarning.title")}>
          {tcal("attendanceWarning.body", {
            kind: dayOff.name ?? tcal(`dayKind.${dayOff.kind}`),
          })}
        </Alert>
      ) : null}
      {!day.marked ? <Alert tone="info">{t("notMarkedYet")}</Alert> : null}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-base text-ink" aria-live="polite">
          {tally}
        </p>
        {canRecord ? (
          <Button
            variant="secondary"
            size="sm"
            data-print="hide"
            onClick={() => {
              setStatuses(Object.fromEntries(Object.keys(statuses).map((k) => [k, "present"])));
              setTouched(new Set(Object.keys(statuses)));
            }}
          >
            <Icon name="check" className="size-4" />
            {t("allPresent")}
          </Button>
        ) : null}
      </div>
      <ul
        aria-label={t("dayTable", { section: day.section.label })}
        className="divide-y divide-border overflow-hidden rounded-xl border border-border"
      >
        {day.students.map((row) => {
          const id = row.student.student_id;
          const name = row.student.full_name ?? t("unnamed");
          const status = statuses[id] ?? "present";
          return (
            <li
              key={id}
              data-status={status}
              className={cn(
                "flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4",
                status === "absent" ? "bg-danger-soft" : "bg-surface",
              )}
            >
              <span className="flex min-w-0 items-baseline gap-3">
                <span className="num w-8 shrink-0 text-end font-mono text-sm text-ink-muted">
                  {row.student.roll_no ?? "—"}
                </span>
                <span className="min-w-0 font-semibold break-words text-ink">{name}</span>
              </span>
              <SegmentedControl
                legend={t("statusFor", { name })}
                block
                className="w-full sm:w-auto sm:min-w-[22rem] sm:shrink-0"
                name={`status-${id}`}
                value={status}
                onValueChange={(value) => {
                  setStatuses({ ...statuses, [id]: value as AttendanceStatus });
                  setTouched(new Set(touched).add(id));
                }}
                options={ATTENDANCE_STATUSES.map((value) => ({
                  value,
                  label: t(`status.${value}`),
                  disabled: !canRecord,
                }))}
              />
            </li>
          );
        })}
      </ul>
      {saved !== null ? (
        <Alert tone="success" live>
          {t("saved", { count: saved })}
        </Alert>
      ) : null}
      {conflict ? (
        <Alert tone="warning" live>
          {t("changedBySomeone")}
        </Alert>
      ) : null}
      <ApiErrorAlert error={error} />
      <ProblemList error={error} />
      {canRecord ? (
        <SaveBar dirty={dirty}>
          <Button
            variant="secondary"
            disabled={!changed || pending}
            onClick={() => {
              setStatuses({ ...baseline });
              setTouched(new Set());
            }}
          >
            {t("undo")}
          </Button>
          <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
            {t("save")}
          </Button>
        </SaveBar>
      ) : null}
    </form>
  );
}

/**
 * The month register (students × school days), printable on A4 landscape (FR-ATT-003). The
 * card title names the section and the month in words (XA-02): the pickers do not print, so
 * the paper register says which class it is.
 */
function MonthRegister({ section, month }: { section: string; month: string }) {
  const t = useTranslations("attendance");
  const locale = useLocale();
  const data = useAttendanceMonth(section, month, true);
  const monthName = monthLabel(month, locale);
  return (
    <Card
      title={
        data.status === "ready"
          ? t("monthTable", { section: data.data.section.label, month: monthName })
          : t("monthTitle", { month: monthName })
      }
    >
      <LoadGate data={data}>
        {(register) =>
          register.students.length === 0 ? (
            <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-ink-muted">
                {t("schoolDays", { count: register.school_days.length })}
              </p>
              <TableScroll
                label={t("monthTable", { section: register.section.label, month: monthName })}
              >
                <Table density="compact">
                  <THead>
                    <Tr>
                      <Th>{t("columns.roll")}</Th>
                      <Th>{t("columns.student")}</Th>
                      {register.school_days.map((day) => (
                        <Th key={day}>
                          <span className="font-mono text-xs">{day.slice(8, 10)}</span>
                        </Th>
                      ))}
                      <Th numeric>{t("columns.absent")}</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {register.students.map((row) => (
                      <Tr key={row.student.student_id}>
                        <Td>
                          <span className="font-mono">{row.student.roll_no ?? "—"}</span>
                        </Td>
                        <Td>{row.student.full_name ?? t("unnamed")}</Td>
                        {register.school_days.map((day) => {
                          const status = row.days[day];
                          return (
                            <Td key={day}>
                              <span className="font-mono text-xs">
                                {status ? REGISTER_CODE[status] : ""}
                              </span>
                              {status ? (
                                <span className="sr-only">{t(`status.${status}`)}</span>
                              ) : null}
                            </Td>
                          );
                        })}
                        <Td numeric>{row.counts.absent ?? 0}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </TableScroll>
              <p className="text-xs text-ink-subtle">{t("legend")}</p>
            </div>
          )
        }
      </LoadGate>
    </Card>
  );
}

/** The current month and the 11 before it, as `YYYY-MM` (a select: `<input type="month">` is not Baseline). */
export function recentMonths(today: string, count = 12): string[] {
  const year = Number(today.slice(0, 4));
  const month = Number(today.slice(5, 7));
  return Array.from({ length: count }, (_, back) => {
    const index = year * 12 + (month - 1) - back;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
  });
}

function monthLabel(value: string, locale: string): string {
  const date = new Date(`${value}-01T00:00:00Z`);
  return new Intl.DateTimeFormat(locale, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

/**
 * XA-05: asked before a change of section, date or view drops statuses not saved yet. A
 * native modal `<dialog>` (focus kept inside, Escape keeps marking); "Keep marking" is first so
 * Enter does not discard by accident.
 */
function DiscardDialog({
  count,
  onDiscard,
  onKeep,
}: {
  count: number;
  onDiscard: () => void;
  onKeep: () => void;
}) {
  const t = useTranslations("attendance.discard");
  const tc = useTranslations("common");
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);
  const close = (then: () => void) => {
    ref.current?.close();
    then();
  };
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => {
        event.preventDefault();
        close(onKeep);
      }}
      className="dialog-motion m-auto w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-0 text-ink shadow-popover"
    >
      <div className="flex items-start justify-between gap-4 p-4 pb-2 sm:p-6 sm:pb-2">
        <h2 id={titleId} className="text-lg font-semibold">
          {t("title")}
        </h2>
        <DialogCloseButton label={tc("close")} onClick={() => close(onKeep)} />
      </div>
      <p id={bodyId} className="px-4 py-3 text-sm text-ink sm:px-6">
        {t("body", { count })}
      </p>
      <div className="dialog-footer flex flex-wrap justify-end gap-2 rounded-b-xl border-t border-border bg-surface-muted px-4 py-3 max-sm:[&>*]:flex-1 sm:px-6 sm:py-4">
        <Button variant="secondary" onClick={() => close(onKeep)}>
          {t("keep")}
        </Button>
        <Button variant="danger" onClick={() => close(onDiscard)}>
          {t("discard")}
        </Button>
      </div>
    </dialog>
  );
}

/**
 * Attendance (US-1701, US-1702): mark a section's day, see the month register and print it, or
 * import a month from the paper register's sheet. Class teachers see only their sections.
 */
export function AttendanceScreen() {
  const t = useTranslations("attendance");
  const tn = useTranslations("school.nav");
  const locale = useLocale();
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(PERM.attendanceRead);
  const canRecord = can(PERM.attendanceRecord);
  const [section, setSection] = useState("");
  const [date, setDate] = useState(todayIst());
  const [view, setView] = useState<"day" | "month">("day");
  const [month, setMonth] = useState(todayIst().slice(0, 7));
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const day = useAttendanceDay(section, date, allowed && view === "day");
  // XA-05: statuses not saved yet in the day form, and the switch waiting for an answer.
  const [unsaved, setUnsaved] = useState(0);
  const [held, setHeld] = useState<(() => void) | null>(null);
  const onUnsavedChange = useCallback((count: number) => setUnsaved(count), []);
  const guard = (change: () => void) => {
    if (view === "day" && unsaved > 0) setHeld(() => change);
    else change();
  };

  return (
    <div className="space-y-5" data-print={view === "month" ? "landscape" : undefined}>
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        actions={
          view === "month" && section ? (
            <Button variant="secondary" onClick={() => window.print()} data-print="hide">
              <Icon name="printer" className="size-4" />
              {t("print")}
            </Button>
          ) : null
        }
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <>
          <div data-print="hide">
            <Card padding="sm">
              <div className="grid gap-3 sm:flex sm:flex-wrap sm:items-end sm:gap-4">
                <SectionPicker
                  label={t("section")}
                  value={section}
                  onChange={(id) => guard(() => setSection(id))}
                />
                <SegmentedControl
                  legend={t("viewLabel")}
                  legendVisible
                  value={view}
                  onValueChange={(value) => guard(() => setView(value as "day" | "month"))}
                  options={[
                    { value: "day", label: t("view.day") },
                    { value: "month", label: t("view.month") },
                  ]}
                />
                {view === "day" ? (
                  <TextField
                    type="date"
                    label={t("date")}
                    value={date}
                    max={todayIst()}
                    onChange={(event) => {
                      const next = event.target.value;
                      guard(() => setDate(next));
                    }}
                  />
                ) : (
                  <SelectField
                    label={t("month")}
                    value={month}
                    onChange={(event) => setMonth(event.target.value)}
                    options={recentMonths(todayIst()).map((value) => ({
                      value,
                      label: monthLabel(value, locale),
                    }))}
                  />
                )}
              </div>
            </Card>
          </div>
          {!section ? (
            <EmptyState icon="calendar" title={t("pickTitle")} body={t("pickBody")} />
          ) : view === "day" ? (
            <Card title={t("dayTitle", { date: formatDate(date) ?? date })}>
              <LoadGate data={day}>
                {(value) => (
                  <DayForm
                    key={`${value.section.id}:${value.on_date}`}
                    day={value}
                    canRecord={canRecord}
                    onUnsavedChange={onUnsavedChange}
                  />
                )}
              </LoadGate>
            </Card>
          ) : (
            <MonthRegister section={section} month={month} />
          )}
          {held ? (
            <DiscardDialog
              count={unsaved}
              onKeep={() => setHeld(null)}
              onDiscard={() => {
                setHeld(null);
                held();
              }}
            />
          ) : null}
          {canRecord && section ? (
            <div data-print="hide">
              <SheetImport<AttendanceEntry, AttendanceSheet>
                // XA-01: a sheet read for one section is never offered for another.
                key={section}
                section={section}
                title={t("import.title")}
                description={t("import.description")}
                sample={
                  <TableScroll label={t("import.sampleLabel")}>
                    <Table density="compact">
                      <THead>
                        <Tr>
                          <Th>{t("import.admissionNo")}</Th>
                          <Th>{t("import.name")}</Th>
                          <Th>01/09/2026</Th>
                          <Th>02/09/2026</Th>
                        </Tr>
                      </THead>
                      <TBody>
                        <Tr>
                          <Td>SYN-101</Td>
                          <Td>{t("import.sampleName")}</Td>
                          <Td>P</Td>
                          <Td>A</Td>
                        </Tr>
                      </TBody>
                    </Table>
                  </TableScroll>
                }
                preview={(documentId) =>
                  unwrap(
                    api.POST("/api/v1/sections/{section_id}/attendance/sheet", {
                      params: { path: { section_id: section } },
                      body: { document_id: documentId },
                    }),
                  )
                }
                commit={(entries) =>
                  unwrap(
                    api.POST("/api/v1/sections/{section_id}/attendance", {
                      params: { path: { section_id: section } },
                      body: { entries, source: "import" },
                    }),
                  )
                }
                summary={(sheet) =>
                  t("import.summary", {
                    entries: sheet.entries.length,
                    students: sheet.students,
                    days: sheet.dates.length,
                  })
                }
                onSaved={() => queryClient.invalidateQueries({ queryKey: KEYS.attendance })}
              />
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
