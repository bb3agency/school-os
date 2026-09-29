"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { Timeline, type TimelineItem } from "@/components/ui/Timeline";
import { Link } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import {
  ERASE_REASONS,
  INDICATORS,
  KEYS,
  NOTE_CATEGORIES,
  PERM,
  eraseSchema,
  noteSchema,
  raiseSchema,
  todayIst,
  useTimeline,
  type Note,
  type NoteCategory,
  type Timeline as TimelineData,
  type TimelineEntry,
} from "./data";
import { ActionLog } from "./FlagDetailScreen";
import { FlagReason, FlagStatusPill, LoadGate, OverduePill, PurposeNote } from "./parts";

const NOTE_MAX = 500;
const FLAG_NOTE_MAX = 1000;

const categoryPill: Record<NoteCategory, PillVariant> = {
  positive: "positive",
  observation: "tag",
  concern: "negative",
};

/** ABC indicators from stored records (FR-EW-001): numbers, never a score or a label. */
function Indicators({ data }: { data: TimelineData }) {
  const t = useTranslations("insights.indicators");
  const { attendance, behaviour, course } = data.indicators;
  const change =
    course.change === null
      ? null
      : t("change", { change: course.change > 0 ? `+${course.change}` : String(course.change) });
  return (
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label={t("attendanceRate")}
        value={attendance.rate === null ? null : `${attendance.rate}%`}
        unavailableLabel={t("noData")}
        hint={t("attendanceDays", { days: attendance.days, absent: attendance.absent })}
      />
      <StatCard
        label={t("streak")}
        value={String(attendance.streak)}
        unavailableLabel={t("noData")}
        hint={t("streakHint")}
      />
      <StatCard
        label={t("concerns")}
        value={String(behaviour.concerns)}
        unavailableLabel={t("noData")}
        hint={t("concernsHint", { days: behaviour.window_days })}
      />
      <StatCard
        label={t("course")}
        value={course.percent === null ? null : `${course.percent}%`}
        unavailableLabel={t("noData")}
        {...(change ? { hint: change } : {})}
      />
    </dl>
  );
}

function EraseNote({ note }: { note: Note }) {
  const t = useTranslations("insights");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("notes.erase")}
      triggerVariant="secondary"
      triggerSize="sm"
      title={t("notes.eraseTitle")}
      description={t("notes.eraseDescription")}
      confirmLabel={t("erase.save")}
      confirmVariant="danger"
      stepUp
      schema={eraseSchema}
      invalidate={[KEYS.timeline(note.student_id)]}
      errorNamespace="insights"
      submit={(input) =>
        unwrap(
          api.POST("/api/v1/behaviour-notes/{note_id}/erase", {
            params: { path: { note_id: note.id } },
            body: input,
          }),
        )
      }
    >
      {(errors) => (
        <SelectField
          name="reason"
          label={t("erase.reasonLabel")}
          placeholder={t("action.choose")}
          error={errors.reason}
          options={ERASE_REASONS.map((value) => ({ value, label: t(`erase.reason.${value}`) }))}
        />
      )}
    </ActionDialog>
  );
}

/** One timeline entry as a Timeline item: exactly the field named by `kind` is set. */
function useItem(): (entry: TimelineEntry, index: number) => TimelineItem | null {
  const t = useTranslations("insights.timeline");
  const ti = useTranslations("insights");
  const can = useStaffCan();
  const canManage = can(PERM.insightsManage);
  return (entry, index) => {
    const id = `${entry.kind}-${entry.on}-${index}`;
    const time = <time dateTime={entry.on}>{formatDate(entry.on)}</time>;
    if (entry.enrolment) {
      const e = entry.enrolment;
      return {
        id,
        time,
        title: t("enrolment", { section: e.section_label ?? t("noSection") }),
        chips: (
          <Pill variant="tag">
            {translateOr(t, `enrolmentStatus.${e.status}`, "enrolmentStatus.other")}
          </Pill>
        ),
        ...(e.ended_on ? { body: t("ended", { date: formatDate(e.ended_on) ?? e.ended_on }) } : {}),
      };
    }
    if (entry.attendance) {
      const a = entry.attendance;
      return {
        id,
        time,
        title: t("attendanceMonth", { month: a.month }),
        body: t("attendanceBody", {
          days: a.days,
          present: a.present,
          late: a.late,
          absent: a.absent,
          leave: a.leave,
        }),
      };
    }
    if (entry.exam) {
      const x = entry.exam;
      return {
        id,
        time,
        title: x.name,
        body:
          x.percent === null
            ? t("examNoMarks")
            : t("examBody", {
                percent: String(x.percent),
                papers: x.papers,
                absent: x.absent_papers,
              }),
      };
    }
    if (entry.note) {
      const n = entry.note;
      return {
        id,
        time,
        title: t("note"),
        chips: <Pill variant={categoryPill[n.category]}>{ti(`notes.category.${n.category}`)}</Pill>,
        body: (
          <div className="space-y-1">
            <p className="break-words whitespace-pre-line text-ink">{n.text}</p>
            <p className="text-xs text-ink-subtle">
              {t("by", { name: n.by?.display_name ?? ti("someone") })}
            </p>
            {canManage ? (
              <div data-print="hide">
                <EraseNote note={n} />
              </div>
            ) : null}
          </div>
        ),
        status: "current",
      };
    }
    if (entry.flag) {
      const f = entry.flag;
      return {
        id,
        time,
        title: (
          <Link href={`/flags/${f.id}`} className="text-primary underline underline-offset-4">
            {t("flag", { indicator: ti(`indicator.${f.indicator}`) })}
          </Link>
        ),
        chips: (
          <span className="inline-flex flex-wrap gap-1">
            <FlagStatusPill status={f.status} />
            <OverduePill overdue={f.overdue} />
          </span>
        ),
        body: (
          <div className="space-y-2">
            <p className="break-words">
              <FlagReason flag={f} />
            </p>
            <ActionLog actions={f.actions} label={t("flagLog")} />
          </div>
        ),
        status: f.status === "closed" ? "done" : "current",
      };
    }
    if (entry.certificate) {
      const c = entry.certificate;
      return {
        id,
        time,
        title: translateOr(t, `certificate.${c.certificate_type}`, "certificate.other"),
        ...(c.serial ? { body: <span className="font-mono">{c.serial}</span> } : {}),
      };
    }
    return null;
  };
}

function AddNote({ studentId }: { studentId: string }) {
  const t = useTranslations("insights.notes");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("add")}
      triggerVariant="primary"
      title={t("title")}
      description={t("description")}
      confirmLabel={t("save")}
      schema={noteSchema}
      invalidate={[KEYS.timeline(studentId)]}
      errorNamespace="insights"
      submit={(input, key) =>
        unwrap(
          api.POST("/api/v1/students/{student_id}/behaviour-notes", {
            params: { path: { student_id: studentId } },
            headers: { "Idempotency-Key": key },
            body: input,
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="category"
            label={t("categoryLabel")}
            placeholder={t("choose")}
            error={errors.category}
            options={NOTE_CATEGORIES.map((value) => ({ value, label: t(`category.${value}`) }))}
          />
          <TextField
            name="noted_on"
            type="date"
            label={t("notedOn")}
            defaultValue={todayIst()}
            max={todayIst()}
            error={errors.noted_on}
          />
          <TextAreaField
            name="text"
            label={t("text")}
            hint={t("textHint")}
            error={errors.text}
            maxLength={NOTE_MAX}
            rows={4}
          />
        </>
      )}
    </ActionDialog>
  );
}

function RaiseFlag({ studentId }: { studentId: string }) {
  const t = useTranslations("insights");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      triggerLabel={t("raise.open")}
      title={t("raise.title")}
      description={t("raise.description")}
      confirmLabel={t("raise.save")}
      schema={raiseSchema}
      invalidate={[KEYS.all]}
      errorNamespace="insights"
      submit={(input, key) =>
        unwrap(
          api.POST("/api/v1/students/{student_id}/flags", {
            params: { path: { student_id: studentId } },
            headers: { "Idempotency-Key": key },
            body: { indicator: input.indicator, ...(input.note ? { note: input.note } : {}) },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="indicator"
            label={t("raise.indicator")}
            placeholder={t("action.choose")}
            error={errors.indicator}
            options={INDICATORS.map((value) => ({ value, label: t(`indicator.${value}`) }))}
          />
          <TextAreaField
            name="note"
            label={t("action.note")}
            hint={t("action.noteHint")}
            error={errors.note}
            maxLength={FLAG_NOTE_MAX}
            rows={3}
          />
        </>
      )}
    </ActionDialog>
  );
}

function TimelineBody({ data, studentId }: { data: TimelineData; studentId: string }) {
  const t = useTranslations("insights.timeline");
  const can = useStaffCan();
  const toItem = useItem();
  const items = data.items
    .map((entry, index) => toItem(entry, index))
    .filter((item): item is TimelineItem => item !== null);
  const actions: ReactNode[] = [];
  if (can(PERM.insightsNote)) actions.push(<AddNote key="note" studentId={studentId} />);
  if (can(PERM.insightsAct)) actions.push(<RaiseFlag key="flag" studentId={studentId} />);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PurposeNote />
        <div className="flex flex-wrap gap-2" data-print="hide">
          {actions}
          <Button variant="secondary" onClick={() => window.print()}>
            {t("print")}
          </Button>
        </div>
      </div>
      <Indicators data={data} />
      <Card title={t("title")} description={t("description")}>
        {items.length === 0 ? (
          <EmptyState icon="clock" title={t("emptyTitle")} body={t("emptyBody")} />
        ) : (
          <Timeline items={items} label={t("title")} />
        )}
      </Card>
    </div>
  );
}

/**
 * The student's timeline tab (US-1704, US-1705, US-1707): indicators, notes, flags with their
 * logs, exams, attendance months and enrolments, newest first. Only the class teacher of the
 * student's section and the principal may open it (the API answers 404 to anyone else and
 * audits each read, FR-EW-011, FR-EW-012).
 */
export function StudentInsights({ studentId }: { studentId: string }) {
  const t = useTranslations("insights.timeline");
  const data = useTimeline(studentId, true);
  if (data.status === "error" && data.reason === "not_found") {
    return (
      <Alert tone="info" title={t("notInScopeTitle")}>
        {t("notInScopeBody")}
      </Alert>
    );
  }
  return (
    <LoadGate data={data}>
      {(value) => <TimelineBody data={value} studentId={studentId} />}
    </LoadGate>
  );
}
