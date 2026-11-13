"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
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
import { ApiError, isTransientError, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime } from "@/lib/format";
import {
  ATTENDANCE_STATUSES,
  draftKeepHours,
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
import {
  clearDraft,
  draftIdempotencyKey,
  listDrafts,
  nextDraft,
  readDraft,
  retryDelay,
  writeDraft,
  type AttendanceDraft,
  type AttendanceDraftEntry,
} from "./attendance-draft";
import { LoadGate, ProblemList, SectionPicker } from "./parts";
import { SheetImport } from "./SheetImport";
import { useUnsavedChangesWarning } from "@/features/sheets/unsaved";
import { DialogCloseButton } from "@/components/ui/DialogCloseButton";

/** Where a day's unsent statuses stand (FT-11, FR-ATT-033): shown while anything is unsaved. */
type SyncState =
  { kind: "idle" } | { kind: "sending" } | { kind: "offline" } | { kind: "retrying"; at: number };

function draftEntriesOf(entries: readonly AttendanceEntry[]): AttendanceDraftEntry[] {
  return entries.map(({ student_id, status, expected_version }) => ({
    student_id,
    status,
    expected_version: expected_version ?? 0,
  }));
}

/**
 * XA-06: only the students whose status differs from what the server holds (all of them on a
 * day not marked yet). The API upserts per student and date and never clears the ones left
 * out, so a correction does not overwrite what someone else marked for other students. Each
 * carries the version read (YA-01), so a save cannot overwrite someone else's change unseen.
 */
function entriesFor(
  day: AttendanceDay,
  statuses: Record<string, AttendanceStatus>,
  versions: Record<string, number>,
): AttendanceEntry[] {
  return day.students
    .filter((row) => row.status === null || statuses[row.student.student_id] !== row.status)
    .map((row) => ({
      student_id: row.student.student_id,
      on_date: day.on_date,
      status: statuses[row.student.student_id] ?? "present",
      expected_version: versions[row.student.student_id] ?? row.version,
    }));
}

function versionsOf(day: AttendanceDay): Record<string, number> {
  return Object.fromEntries(day.students.map((row) => [row.student.student_id, row.version]));
}

/**
 * One day of the register as a form: every student gets one status (US-1701). FT-11: what is
 * not saved yet is kept on this device (ids and statuses only), restored after a reload, and
 * sent again when the network returns; a draft read before someone else's save is never sent
 * over it (412 `attendance_changed`, explained on screen).
 */
function DayForm({
  day,
  canRecord,
  owner,
  onUnsavedChange,
}: {
  day: AttendanceDay;
  canRecord: boolean;
  /** The signed-in membership (drafts are kept per person); null until /me has loaded. */
  owner: string | null;
  /** How many students' statuses are not saved yet (0 once saved or unmounted). */
  onUnsavedChange: (count: number) => void;
}) {
  const t = useTranslations("attendance");
  const td = useTranslations("attendance.draft");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const keepHours = draftKeepHours(day);
  // What the register says now (unmarked students start as present). Keyed by section and
  // date in the parent, so a new day starts from its own statuses.
  const baseline: Record<string, AttendanceStatus> = {};
  for (const row of day.students) baseline[row.student.student_id] = row.status ?? "present";
  // FT-11: this person's draft for this section and day kept on the device (read once).
  const [restored] = useState<AttendanceDraft | null>(() =>
    canRecord && owner ? readDraft(owner, day.section.id, day.on_date) : null,
  );
  // FR-ATT-029: students whose mark changed since the draft read it (someone else saved).
  // A mark saved since with the same status is no conflict (the draft's own save, whose
  // answer was lost, or the same choice by someone else).
  const [restoredConflicts] = useState<ReadonlySet<string>>(() => {
    const out = new Set<string>();
    const fresh = new Map(day.students.map((row) => [row.student.student_id, row]));
    for (const entry of restored?.entries ?? []) {
      const row = fresh.get(entry.student_id);
      if (row && row.version !== entry.expected_version && row.status !== entry.status) {
        out.add(entry.student_id);
      }
    }
    return out;
  });
  const [statuses, setStatuses] = useState<Record<string, AttendanceStatus>>(() => {
    const next = { ...baseline };
    for (const entry of restored?.entries ?? []) {
      if (entry.student_id in next) next[entry.student_id] = entry.status;
    }
    return next;
  });
  // The version each mark had when this form read it (YA-01, FR-ATT-029): a refetch of the
  // day (window focus) never moves it; only a conflict the screen explained does. A restored
  // draft starts from today's versions: its conflicts were found above and are shown.
  const [versions, setVersions] = useState<Record<string, number>>(() => versionsOf(day));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<number | null>(null);
  // Students whose status this person chose (YA-01): kept over a reload after a conflict.
  const [touched, setTouched] = useState<ReadonlySet<string>>(
    () => new Set((restored?.entries ?? []).map((entry) => entry.student_id)),
  );
  const [conflict, setConflict] = useState(restoredConflicts.size > 0);
  const [sync, setSync] = useState<SyncState>({ kind: "idle" });
  // The teacher pressed Save and it has not reached the school yet (retried automatically).
  const [queued, setQueued] = useState(Boolean(restored?.queued) && restoredConflicts.size === 0);
  // true: kept on this device; false: the browser refused; null: nothing kept.
  const [kept, setKept] = useState<boolean | null>(restored ? true : null);
  const [draft, setDraft] = useState<AttendanceDraft | null>(restored);
  const attempt = useRef(0);
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
    setVersions(versionsOf(day));
  }, [day, touched]);
  // XA-06: only the students whose status differs from what the server holds (all of them on
  // a day not marked yet). The API upserts per student and date and never clears the ones
  // left out, so a correction does not overwrite what someone else marked for other students.
  const entries = entriesFor(day, statuses, versions);
  const unsaved = canRecord ? entries.length : 0;
  useEffect(() => {
    onUnsavedChange(unsaved);
  }, [onUnsavedChange, unsaved]);
  useEffect(() => () => onUnsavedChange(0), [onUnsavedChange]);
  const tcal = useTranslations("schoolCalendar");
  // XA-05: closing, reloading or leaving the page asks first while statuses are unsaved.
  useUnsavedChangesWarning(unsaved > 0, t("unsavedLeave"));

  // FT-11 (FR-ATT-032): only what this person changed is a draft (an untouched unmarked day is
  // not kept until Save is pressed); written on every change, at once, because a phone may
  // close the tab without warning. Dropped when nothing is left to save.
  const keep = (list: AttendanceDraftEntry[], queue: boolean): AttendanceDraft | null => {
    if (!owner || list.length === 0) return null;
    const next = nextDraft(draft, {
      owner,
      section: day.section.id,
      section_label: day.section.label,
      date: day.on_date,
      entries: list,
      queued: queue,
      keepHours,
    });
    setDraft(next);
    setKept(writeDraft(next));
    return next;
  };
  // FT-11 (FR-ATT-032): after each choice, keep the students this person chose (or drop the
  // draft when nothing of theirs is left to save).
  const persist = (
    nextStatuses: Record<string, AttendanceStatus>,
    nextTouched: ReadonlySet<string>,
  ) => {
    if (!canRecord || !owner) return;
    const list = draftEntriesOf(
      entriesFor(day, nextStatuses, versions).filter((entry) => nextTouched.has(entry.student_id)),
    );
    if (list.length > 0) {
      keep(list, false);
      return;
    }
    clearDraft(day.section.id, day.on_date);
    setDraft(null);
    setKept(null);
  };

  async function send(current: AttendanceDraft | null) {
    const body: AttendanceEntry[] = current
      ? current.entries.map((entry) => ({ ...entry, on_date: day.on_date }))
      : entries;
    if (body.length === 0) return;
    if (current && typeof navigator !== "undefined" && navigator.onLine === false) {
      setSync({ kind: "offline" });
      return;
    }
    setPending(true);
    setSync({ kind: "sending" });
    try {
      const out = await unwrap(
        api.POST("/api/v1/sections/{section_id}/attendance", {
          params: { path: { section_id: day.section.id } },
          body: { entries: body, source: "mark" },
          headers: current ? { "Idempotency-Key": draftIdempotencyKey(current) } : {},
        }),
      );
      clearDraft(day.section.id, day.on_date);
      setDraft(null);
      setKept(null);
      attempt.current = 0;
      setQueued(false);
      setSync({ kind: "idle" });
      setSaved(out.written);
      setTouched(new Set());
      await queryClient.invalidateQueries({ queryKey: KEYS.attendance });
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "attendance_changed") {
        setQueued(false);
        setSync({ kind: "idle" });
        setConflict(true);
        reloading.current = true;
        await queryClient.invalidateQueries({ queryKey: KEYS.attendance });
      } else if (
        current &&
        (isTransientError(failure) ||
          (failure instanceof ApiError && failure.code === "idempotency_in_progress"))
      ) {
        // FR-ATT-033: kept and sent again (5 s, 15 s, 30 s, 1 min, then every 2 min).
        const wait = retryDelay(attempt.current);
        attempt.current += 1;
        setSync(
          typeof navigator !== "undefined" && navigator.onLine === false
            ? { kind: "offline" }
            : { kind: "retrying", at: Date.now() + wait },
        );
      } else {
        setQueued(false);
        setSync({ kind: "idle" });
        setError(failure);
      }
    } finally {
      setPending(false);
    }
  }
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });

  // FR-ATT-033: a queued save is sent at once, again when the network returns, and on a timer
  // with backoff while the network is up; a draft restored as queued is sent once it is back.
  const retryAt = sync.kind === "retrying" ? sync.at : null;
  const waiting = queued && !pending && sync.kind !== "sending";
  const firstTry = waiting && sync.kind === "idle";
  useEffect(() => {
    if (!waiting) return;
    const go = () => void sendRef.current(draft);
    window.addEventListener("online", go);
    let timer: number | undefined;
    if (retryAt !== null) timer = window.setTimeout(go, Math.max(0, retryAt - Date.now()));
    else if (firstTry) timer = window.setTimeout(go, 0);
    return () => {
      window.removeEventListener("online", go);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [waiting, firstTry, retryAt, draft]);

  if (day.students.length === 0) {
    return <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />;
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setSaved(null);
    setConflict(false);
    if (entries.length === 0) {
      setSaved(0);
      return;
    }
    attempt.current = 0;
    // Keep the whole day being sent (also "everyone present" on an unmarked day), queued.
    const list = draftEntriesOf(entries);
    const next = keep(list, true);
    setSync({ kind: "idle" });
    if (next) setQueued(true);
    else void send(null);
  }

  function discardDraft() {
    clearDraft(day.section.id, day.on_date);
    setDraft(null);
    setKept(null);
    attempt.current = 0;
    setQueued(false);
    setSync({ kind: "idle" });
    setConflict(false);
    setStatuses({ ...baseline });
    setVersions(versionsOf(day));
    setTouched(new Set());
  }

  // FT-06 (FR-CAL-010): the day in the school calendar.
  const dayOff = day.calendar ?? null;
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
  const keptUntil = draft ? formatDateTime(new Date(draft.keep_until).toISOString()) : null;

  return (
    <form onSubmit={save} noValidate className="space-y-4">
      {dayOff && !dayOff.working ? (
        <Alert tone="warning" title={tcal("attendanceWarning.title")}>
          {tcal("attendanceWarning.body", {
            kind: dayOff.name ?? tcal(`dayKind.${dayOff.kind}`),
          })}
        </Alert>
      ) : null}
      {!day.marked ? <Alert tone="info">{t("notMarkedYet")}</Alert> : null}
      {canRecord && restored && draft && !conflict ? (
        <Alert tone="info" title={td("restoredTitle")}>
          {td("restoredBody", {
            count: restored.entries.length,
            time: formatDateTime(new Date(restored.updated_at).toISOString()) ?? "",
          })}
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-base text-ink" aria-live="polite">
          {tally}
        </p>
        {canRecord ? (
          <Button
            variant="secondary"
            size="sm"
            data-print="hide"
            disabled={queued}
            onClick={() => {
              const next: Record<string, AttendanceStatus> = Object.fromEntries(
                Object.keys(statuses).map((k) => [k, "present"]),
              );
              const all = new Set(Object.keys(statuses));
              setStatuses(next);
              setTouched(all);
              persist(next, all);
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
          // FR-ATT-029: after a conflict, say what is saved now where it differs from mine.
          const theirs =
            conflict && touched.has(id) && row.status !== null && row.status !== status
              ? row.status
              : null;
          return (
            <li
              key={id}
              data-status={status}
              className={cn(
                "flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4",
                status === "absent" ? "bg-danger-soft" : "bg-surface",
              )}
            >
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="flex min-w-0 items-baseline gap-3">
                  <span className="num w-8 shrink-0 text-end font-mono text-sm text-ink-muted">
                    {row.student.roll_no ?? "—"}
                  </span>
                  <span className="min-w-0 font-semibold break-words text-ink">{name}</span>
                </span>
                {theirs ? (
                  <span className="ps-11 text-sm text-ink-muted">
                    {td("savedNow", { status: t(`status.${theirs}`) })}
                  </span>
                ) : null}
              </span>
              <SegmentedControl
                legend={t("statusFor", { name })}
                block
                className="w-full sm:w-auto sm:min-w-[22rem] sm:shrink-0"
                name={`status-${id}`}
                value={status}
                onValueChange={(value) => {
                  const next = { ...statuses, [id]: value as AttendanceStatus };
                  const mine = new Set(touched).add(id);
                  setStatuses(next);
                  setTouched(mine);
                  persist(next, mine);
                }}
                options={ATTENDANCE_STATUSES.map((value) => ({
                  value,
                  label: t(`status.${value}`),
                  disabled: !canRecord || queued,
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
      {canRecord && dirty ? (
        <DraftStatus
          sync={sync}
          queued={queued}
          kept={kept}
          keptUntil={keptUntil}
          busy={pending}
          onRetry={() => {
            attempt.current = 0;
            void send(draft);
          }}
        />
      ) : null}
      <ApiErrorAlert error={error} />
      <ProblemList error={error} />
      {canRecord ? (
        <SaveBar dirty={dirty}>
          <Button
            variant="secondary"
            disabled={pending || (!changed && !queued && !draft)}
            onClick={discardDraft}
          >
            {queued || draft ? td("discard") : t("undo")}
          </Button>
          <Button
            type="submit"
            disabled={pending || queued}
            aria-disabled={pending || queued || undefined}
          >
            {t("save")}
          </Button>
        </SaveBar>
      ) : null}
    </form>
  );
}

/** FR-ATT-033: "not saved yet", in words, with what happens next. */
function DraftStatus({
  sync,
  queued,
  kept,
  keptUntil,
  busy,
  onRetry,
}: {
  sync: SyncState;
  queued: boolean;
  kept: boolean | null;
  keptUntil: string | null;
  busy: boolean;
  onRetry: () => void;
}) {
  const td = useTranslations("attendance.draft");
  const where =
    kept === false ? td("notKept") : kept ? td("keptUntil", { time: keptUntil ?? "" }) : null;
  if (!queued) {
    return where ? (
      <p className="text-sm text-ink-muted" data-print="hide">
        <span className="font-semibold text-ink">{td("notSavedYet")}</span> {where}
      </p>
    ) : null;
  }
  const message =
    sync.kind === "offline"
      ? td("offline")
      : sync.kind === "retrying"
        ? td("retrying")
        : td("sending");
  return (
    <div data-print="hide" role="status">
      <Alert tone="warning" title={td("notSavedYet")}>
        <span className="block">{message}</span>
        {where ? <span className="block">{where}</span> : null}
        {sync.kind === "retrying" || sync.kind === "offline" ? (
          <Button variant="secondary" size="sm" className="mt-2" disabled={busy} onClick={onRetry}>
            {td("tryNow")}
          </Button>
        ) : null}
      </Alert>
    </div>
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
 * FT-11 (FR-ATT-034): days this person marked on this device that are not saved yet, so none is
 * lost unseen when the teacher opens another section or day. Read when the screen opens and
 * when the open day changes (the list is not live across tabs).
 */
function PendingDrafts({
  owner,
  current,
  onOpen,
}: {
  owner: string;
  /** `section:date` of the day open now (left out of the list). */
  current: string;
  onOpen: (draft: AttendanceDraft) => void;
}) {
  const td = useTranslations("attendance.draft");
  // Read from the device when the open day changes (`current` is the trigger, not an input).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const drafts = useMemo(() => listDrafts(owner), [owner, current]);
  const others = drafts.filter((draft) => `${draft.section}:${draft.date}` !== current);
  if (others.length === 0) return null;
  return (
    <div data-print="hide">
      <Alert tone="warning" title={td("pendingTitle", { count: others.length })}>
        <p>{td("pendingBody")}</p>
        <ul className="mt-2 flex flex-wrap gap-2">
          {others.map((draft) => (
            <li key={`${draft.section}:${draft.date}`}>
              <Button variant="secondary" size="sm" onClick={() => onOpen(draft)}>
                {td("pendingOpen", {
                  section: draft.section_label,
                  date: formatDate(draft.date) ?? draft.date,
                })}
              </Button>
            </li>
          ))}
        </ul>
      </Alert>
    </div>
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
  const me = useStaffMe();
  const meLoaded = me !== undefined;
  const owner = me?.membership_id ?? null;
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
          {canRecord && owner ? (
            <PendingDrafts
              owner={owner}
              current={view === "day" ? `${section}:${date}` : ""}
              onOpen={(draft) =>
                guard(() => {
                  setView("day");
                  setSection(draft.section);
                  setDate(draft.date);
                })
              }
            />
          ) : null}
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
                    owner={owner}
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
                // FT-11: discarding also forgets the copy kept on this device.
                if (section) clearDraft(section, date);
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
