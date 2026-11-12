"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useExams, todayIst } from "@/features/insights/data";
import type { Locale } from "@/i18n/routing";
import { useApiMutation, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatCount, formatDate } from "@/lib/format";
import {
  COMPLETENESS_KEYS,
  COMPLETENESS_READ,
  COMPLETENESS_REMIND,
  fetchAttendanceCompleteness,
  fetchMarksCompleteness,
  sendReminder,
  takenShare,
  waitingFirst,
  type AttendanceCompleteness,
  type MarksCompleteness,
} from "./data";

const TONES: Record<string, BadgeTone> = {
  complete: "success",
  partial: "warning",
  not_taken: "danger",
  not_started: "danger",
  missing: "danger",
  day_off: "neutral",
  no_students: "neutral",
};

function Status({ status }: { status: string }) {
  const t = useTranslations("completeness.status");
  return <Badge tone={TONES[status] ?? "neutral"}>{t(status as Parameters<typeof t>[0])}</Badge>;
}

function RemindButton({
  input,
  label,
}: {
  input: { section_id: string; kind: "attendance" | "marks"; exam_id?: string };
  label: string;
}) {
  const t = useTranslations("completeness");
  const api = useBffClient("staff");
  const [sent, setSent] = useState<number | null>(null);
  const remind = useApiMutation((value: typeof input) => sendReminder(api, value), {
    onSuccess: (out) => setSent(out.sent),
  });
  if (sent !== null) {
    return (
      <span className="text-sm text-ink-muted" role="status">
        {sent > 0 ? t("reminded") : t("alreadyReminded")}
      </span>
    );
  }
  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        aria-label={label}
        disabled={remind.isPending}
        onClick={() => remind.mutate(input)}
      >
        {t("remind")}
      </Button>
      {remind.error ? <ApiErrorAlert error={remind.error} /> : null}
    </>
  );
}

function AttendanceTable({ out, canRemind }: { out: AttendanceCompleteness; canRemind: boolean }) {
  const t = useTranslations("completeness");
  const locale = useLocale() as Locale;
  const share = takenShare(out);
  const remind = canRemind && out.reminders_enabled;
  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold text-ink">
        {t("attendance.taken", {
          taken: formatCount(share.taken, locale) ?? "",
          of: formatCount(share.of, locale) ?? "",
        })}
      </h2>
      <p className="text-sm text-ink-muted">
        {t("attendance.breakdown", {
          partial: out.partial,
          notTaken: out.not_taken,
          dayOff: out.day_off,
        })}
      </p>
      <TableScroll label={t("attendance.tableLabel")}>
        <Table stickyFirstColumn>
          <THead>
            <Tr>
              <Th scope="col">{t("section")}</Th>
              <Th scope="col">{t("status.label")}</Th>
              <Th scope="col" numeric>
                {t("attendance.marked")}
              </Th>
              <Th scope="col">{t("waitingOn")}</Th>
              {remind ? <Th scope="col">{t("action")}</Th> : null}
            </Tr>
          </THead>
          <TBody>
            {waitingFirst(out.rows).map((row) => {
              const open = row.status === "partial" || row.status === "not_taken";
              return (
                <Tr key={row.section_id}>
                  <Th scope="row">{row.label}</Th>
                  <Td>
                    <Status status={row.status} />
                  </Td>
                  <Td numeric>
                    {t("ofStudents", { entered: row.marked, students: row.students })}
                  </Td>
                  <Td>
                    {open ? (row.class_teacher?.display_name ?? t("noClassTeacher")) : t("nobody")}
                  </Td>
                  {remind ? (
                    <Td>
                      {open && row.class_teacher ? (
                        <RemindButton
                          input={{ section_id: row.section_id, kind: "attendance" }}
                          label={t("remindSection", { section: row.label })}
                        />
                      ) : null}
                    </Td>
                  ) : null}
                </Tr>
              );
            })}
          </TBody>
        </Table>
      </TableScroll>
    </Card>
  );
}

function MarksGrid({ out, canRemind }: { out: MarksCompleteness; canRemind: boolean }) {
  const t = useTranslations("completeness");
  const remind = canRemind && out.reminders_enabled;
  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold text-ink">
        {t("marks.summary", {
          complete: out.complete,
          of: out.sections - out.no_students,
          exam: out.exam_name,
        })}
      </h2>
      {out.locked ? <p className="text-sm text-ink-muted">{t("marks.locked")}</p> : null}
      {out.subjects.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("marks.noSubjects")}</p>
      ) : null}
      <TableScroll label={t("marks.tableLabel")}>
        <Table stickyFirstColumn>
          <THead>
            <Tr>
              <Th scope="col">{t("section")}</Th>
              <Th scope="col">{t("status.label")}</Th>
              {out.subjects.map((subject) => (
                <Th scope="col" key={subject}>
                  {subject}
                </Th>
              ))}
              <Th scope="col">{t("waitingOn")}</Th>
              {remind ? <Th scope="col">{t("action")}</Th> : null}
            </Tr>
          </THead>
          <TBody>
            {waitingFirst(out.rows).map((row) => {
              const open = row.status === "partial" || row.status === "not_started";
              return (
                <Tr key={row.section_id}>
                  <Th scope="row">{row.label}</Th>
                  <Td>
                    <Status status={row.status} />
                  </Td>
                  {row.subjects.map((cell) => (
                    <Td key={cell.subject}>
                      <span className="block">
                        {t("ofStudents", { entered: cell.entered, students: row.students })}
                      </span>
                      <Status status={cell.status} />
                    </Td>
                  ))}
                  <Td>
                    {open ? (row.class_teacher?.display_name ?? t("noClassTeacher")) : t("nobody")}
                  </Td>
                  {remind ? (
                    <Td>
                      {open && row.class_teacher ? (
                        <RemindButton
                          input={{
                            section_id: row.section_id,
                            kind: "marks",
                            exam_id: out.exam_id,
                          }}
                          label={t("remindSection", { section: row.label })}
                        />
                      ) : null}
                    </Td>
                  ) : null}
                </Tr>
              );
            })}
          </TBody>
        </Table>
      </TableScroll>
    </Card>
  );
}

/**
 * Records completeness for the principal and the exam coordinator (FT-14, FR-ATT-024,
 * FR-MRK-024): per section, how many students have today's attendance and each paper's
 * marks, and whose entry it waits on. Counts only; never a teacher score or ranking.
 */
export function CompletenessScreen() {
  const t = useTranslations("completeness");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COMPLETENESS_READ);
  const canRemind = can(COMPLETENESS_REMIND);
  const [date, setDate] = useState(() => todayIst());
  const [examId, setExamId] = useState("");
  const attendance = useQuery({
    queryKey: COMPLETENESS_KEYS.attendance(date),
    queryFn: () => fetchAttendanceCompleteness(api, date),
    enabled: allowed && /^\d{4}-\d{2}-\d{2}$/.test(date),
    retry: false,
  });
  const exams = useExams(allowed);
  const marks = useQuery({
    queryKey: COMPLETENESS_KEYS.exam(examId),
    queryFn: () => fetchMarksCompleteness(api, examId),
    enabled: allowed && examId !== "",
    retry: false,
  });
  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
    />
  );
  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  const examList = exams.status === "ready" ? exams.data : [];
  return (
    <div className="space-y-5">
      {header}
      <section aria-labelledby="completeness-attendance" className="space-y-3">
        <h2 id="completeness-attendance" className="text-base font-semibold text-ink">
          {t("attendance.title")}
        </h2>
        <TextField
          label={t("attendance.date")}
          type="date"
          className="max-w-xs"
          value={date}
          max={todayIst()}
          onChange={(event) => setDate(event.target.value)}
        />
        {attendance.error ? <ApiErrorAlert error={attendance.error} /> : null}
        {attendance.data ? (
          <>
            <p className="text-sm text-ink-muted">
              {formatDate(attendance.data.on_date) ?? attendance.data.on_date}
            </p>
            <AttendanceTable out={attendance.data} canRemind={canRemind} />
          </>
        ) : attendance.error ? null : (
          <LoadingState label={tc("loading")} />
        )}
      </section>
      <section aria-labelledby="completeness-marks" className="space-y-3">
        <h2 id="completeness-marks" className="text-base font-semibold text-ink">
          {t("marks.title")}
        </h2>
        <SelectField
          label={t("marks.exam")}
          className="max-w-sm"
          value={examId}
          onChange={(event) => setExamId(event.target.value)}
          options={[
            { value: "", label: t("marks.chooseExam") },
            ...examList.map((exam) => ({
              value: exam.id,
              label: `${exam.name} (${formatDate(exam.held_on) ?? exam.held_on})`,
            })),
          ]}
        />
        {marks.error ? <ApiErrorAlert error={marks.error} /> : null}
        {marks.data ? <MarksGrid out={marks.data} canRemind={canRemind} /> : null}
      </section>
      <p className="text-xs text-ink-subtle">{t("footnote")}</p>
    </div>
  );
}
