"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorSummary, type SummaryError } from "@/components/ui/ErrorSummary";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SaveBar } from "@/components/ui/SaveBar";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import { zodErrorKeys } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
import { NARROW_QUERY, useMediaQuery } from "@/lib/use-media-query";
import {
  KEYS,
  PERM,
  examSchema,
  todayIst,
  useExams,
  useMarks,
  type MarkIn,
  type MarksGrid,
  type MarksSheet,
} from "./data";
import { LoadGate, ProblemList, SectionPicker } from "./parts";
import { SheetImport } from "./SheetImport";

/** Add an exam to the current year (exam.manage; FR-MRK-001). */
function NewExamCard({ onCreated }: { onCreated: (id: string) => void }) {
  const t = useTranslations("marks.newExam");
  const tv = useTranslations("validation");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = examSchema.safeParse(Object.fromEntries(new FormData(form)));
    if (!parsed.success) {
      setErrors(zodErrorKeys(parsed.error));
      return;
    }
    setErrors({});
    setError(undefined);
    setPending(true);
    try {
      const exam = await unwrap(
        api.POST("/api/v1/exams", {
          headers: { "Idempotency-Key": newIdempotencyKey() },
          body: parsed.data,
        }),
      );
      form.reset();
      await queryClient.invalidateQueries({ queryKey: KEYS.exams });
      onCreated(exam.id);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const fieldError = (name: string) =>
    errors[name] ? translateOr(tv, errors[name] ?? "invalid", "invalid") : undefined;
  return (
    <Card title={t("title")} description={t("description")}>
      <form onSubmit={(event) => void submit(event)} noValidate className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <TextField name="name" label={t("name")} maxLength={80} error={fieldError("name")} />
          <TextField
            name="held_on"
            type="date"
            label={t("heldOn")}
            defaultValue={todayIst()}
            error={fieldError("held_on")}
          />
        </div>
        <Button
          type="submit"
          variant="secondary"
          disabled={pending}
          aria-disabled={pending || undefined}
        >
          {t("submit")}
        </Button>
        <ApiErrorAlert error={error} />
        <ProblemList error={error} />
      </form>
    </Card>
  );
}

function MarksTable({ grid }: { grid: MarksGrid }) {
  const t = useTranslations("marks");
  if (grid.students.length === 0) {
    return <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />;
  }
  return (
    <TableScroll label={t("gridLabel", { section: grid.section.label, exam: grid.exam.name })}>
      <Table>
        <THead>
          <Tr>
            <Th>{t("columns.roll")}</Th>
            <Th>{t("columns.student")}</Th>
            {grid.subjects.map((subject) => (
              <Th key={subject} numeric>
                {subject}
              </Th>
            ))}
            <Th numeric>{t("columns.percent")}</Th>
          </Tr>
        </THead>
        <TBody>
          {grid.students.map((row) => (
            <Tr key={row.student.student_id}>
              <Td>
                <span className="num font-mono">{row.student.roll_no ?? "—"}</span>
              </Td>
              <Td>{row.student.full_name ?? t("unnamed")}</Td>
              {grid.subjects.map((subject) => {
                const mark = row.marks.find((m) => m.subject === subject);
                return (
                  <Td key={subject} numeric>
                    <span>
                      {mark
                        ? mark.absent
                          ? t("absentShort")
                          : `${Number(mark.marks)}/${Number(mark.max_marks)}`
                        : ""}
                    </span>
                  </Td>
                );
              })}
              <Td numeric>
                <span>
                  {row.percent === null || row.percent === undefined ? "—" : `${row.percent}%`}
                </span>
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableScroll>
  );
}

const SUBJECT_ID = "marks-subject";
const MAX_ID = "marks-max";
const cellId = (studentId: string) => `marks-${studentId}`;

/**
 * Enter one subject's marks for the whole section (marks.record; FR-MRK-002). One form for both
 * layouts: a keyboard grid from 640px (Enter or the down arrow moves to the next student) and
 * one student at a time with Previous and Next on phones. Values live in state, so switching
 * layout or student never loses what was typed.
 */
function SubjectEntry({ grid, onSaved }: { grid: MarksGrid; onSaved: () => Promise<void> }) {
  const t = useTranslations("marks.entry");
  const api = useBffClient("staff");
  const narrow = useMediaQuery(NARROW_QUERY);
  const [subject, setSubject] = useState("");
  const [max, setMax] = useState("100");
  const [values, setValues] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<SummaryError[]>([]);
  const [bad, setBad] = useState<ReadonlySet<string>>(new Set());
  const [attempt, setAttempt] = useState(0);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const students = grid.students;
  const ids = students.map((row) => row.student.student_id);
  const current = Math.min(index, Math.max(students.length - 1, 0));

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrors([]);
    setBad(new Set());
    setError(undefined);
    setSaved(null);
    const maximum = Number(max);
    const problems: SummaryError[] = [];
    if (!subject.trim()) {
      problems.push({ fieldId: SUBJECT_ID, message: t("problem.subjectRequired") });
    }
    const maxValid = maximum > 0 && maximum <= 1000;
    if (!maxValid) problems.push({ fieldId: MAX_ID, message: t("problem.maxInvalid") });
    const entries: MarkIn[] = [];
    const invalid: string[] = [];
    for (const row of students) {
      const id = row.student.student_id;
      const raw = (values[id] ?? "").trim();
      if (!raw) continue;
      if (/^ab$/i.test(raw)) {
        entries.push({
          student_id: id,
          subject: subject.trim(),
          max_marks: max,
          marks: null,
          absent: true,
        });
        continue;
      }
      const value = Number(raw);
      if (!maxValid) continue;
      if (!Number.isFinite(value) || value < 0 || value > maximum) {
        invalid.push(id);
        continue;
      }
      entries.push({
        student_id: id,
        subject: subject.trim(),
        max_marks: max,
        marks: raw,
        absent: false,
      });
    }
    const first = invalid[0];
    if (first) problems.push({ fieldId: cellId(first), message: t("problem.marksInvalid") });
    if (problems.length === 0 && entries.length === 0 && ids[0]) {
      problems.push({ fieldId: cellId(ids[0]), message: t("problem.nothingEntered") });
    }
    if (problems.length > 0) {
      setErrors(problems);
      setBad(new Set(invalid));
      setAttempt((value) => value + 1);
      // On a phone, show the first student whose marks need fixing.
      if (first) setIndex(Math.max(ids.indexOf(first), 0));
      return;
    }
    setPending(true);
    try {
      const out = await unwrap(
        api.POST("/api/v1/sections/{section_id}/exams/{exam_id}/marks", {
          params: { path: { section_id: grid.section.id, exam_id: grid.exam.id } },
          body: { entries, source: "mark" },
        }),
      );
      setSaved(out.written);
      setValues({});
      await onSaved();
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  function change(id: string, value: string) {
    setValues({ ...values, [id]: value });
    if (bad.has(id)) {
      const next = new Set(bad);
      next.delete(id);
      setBad(next);
    }
  }

  /** Enter or arrow down: the next student's box; arrow up: the one before (desktop grid). */
  function moveFrom(position: number, event: KeyboardEvent<HTMLInputElement>) {
    const step = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    const enter = event.key === "Enter";
    if (!step && !enter) return;
    const target = position + (step || 1);
    if (narrow) {
      if (enter && target < students.length) {
        event.preventDefault();
        setIndex(target);
      }
      return;
    }
    const next = ids[target];
    if (next === undefined) return; // Enter on the last student submits the form.
    event.preventDefault();
    document.getElementById(cellId(next))?.focus();
  }

  const fieldError = (fieldId: string) => errors.find((e) => e.fieldId === fieldId)?.message;
  const dirty = Object.values(values).some((value) => value.trim() !== "");

  const markInput = (position: number, extraClass?: string) => {
    const row = students[position];
    if (!row) return null;
    const id = row.student.student_id;
    const name = row.student.full_name ?? t("unnamed");
    const invalid = bad.has(id);
    return (
      <div className="space-y-1">
        <input
          id={cellId(id)}
          aria-label={t("marksFor", { name })}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? `${cellId(id)}-error` : undefined}
          className={cn(
            "num min-h-10 rounded-md border border-border-control bg-surface px-2 text-end text-base text-ink pointer-coarse:min-h-11",
            "aria-invalid:border-2 aria-invalid:border-danger",
            extraClass ?? "w-24",
          )}
          inputMode="decimal"
          enterKeyHint={position < students.length - 1 ? "next" : "done"}
          autoComplete="off"
          value={values[id] ?? ""}
          onChange={(event) => change(id, event.target.value)}
          onKeyDown={(event) => moveFrom(position, event)}
        />
        {invalid ? (
          <p id={`${cellId(id)}-error`} className="text-sm font-semibold text-danger">
            {t("cellInvalid", { max })}
          </p>
        ) : null}
      </div>
    );
  };

  const one = students[current];
  return (
    <Card title={t("title")} description={t("description")}>
      <form onSubmit={(event) => void save(event)} noValidate className="space-y-4">
        <ErrorSummary errors={errors} attempt={attempt} />
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            id={SUBJECT_ID}
            label={t("subject")}
            value={subject}
            maxLength={60}
            error={fieldError(SUBJECT_ID)}
            onChange={(event) => setSubject(event.target.value)}
          />
          <TextField
            id={MAX_ID}
            label={t("max")}
            type="number"
            inputMode="decimal"
            min={1}
            max={1000}
            value={max}
            error={fieldError(MAX_ID)}
            onChange={(event) => setMax(event.target.value)}
          />
        </div>
        {narrow ? (
          one ? (
            <div className="space-y-4 rounded-xl border border-border p-4">
              <p className="num text-sm text-ink-muted" aria-live="polite">
                {t("position", { current: current + 1, total: students.length })}
              </p>
              <div className="flex items-baseline gap-3">
                <span className="num font-mono text-sm text-ink-muted">
                  {one.student.roll_no ?? "—"}
                </span>
                <span className="text-lg font-semibold break-words text-ink">
                  {one.student.full_name ?? t("unnamed")}
                </span>
              </div>
              {markInput(current, "w-full")}
              <div className="grid grid-cols-2 gap-2">
                <Button
                  variant="secondary"
                  disabled={current === 0}
                  onClick={() => setIndex(current - 1)}
                >
                  <Icon name="chevronLeft" className="size-4" />
                  {t("previous")}
                </Button>
                <Button
                  variant="secondary"
                  disabled={current >= students.length - 1}
                  onClick={() => setIndex(current + 1)}
                >
                  {t("next")}
                  <Icon name="chevronRight" className="size-4" />
                </Button>
              </div>
            </div>
          ) : null
        ) : (
          <TableScroll label={t("title")}>
            <Table density="compact">
              <THead>
                <Tr>
                  <Th>{t("roll")}</Th>
                  <Th>{t("student")}</Th>
                  <Th numeric>{t("marks")}</Th>
                </Tr>
              </THead>
              <TBody>
                {students.map((row, position) => (
                  <Tr key={row.student.student_id}>
                    <Td>
                      <span className="num font-mono">{row.student.roll_no ?? "—"}</span>
                    </Td>
                    <Td>{row.student.full_name ?? t("unnamed")}</Td>
                    <Td className="text-end">{markInput(position)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </TableScroll>
        )}
        <p className="text-xs text-ink-subtle">
          {t("hint")} {narrow ? null : t("keyboardHint")}
        </p>
        {saved !== null ? (
          <Alert tone="success" live>
            {t("saved", { count: saved })}
          </Alert>
        ) : null}
        <ApiErrorAlert error={error} />
        <ProblemList error={error} />
        <SaveBar dirty={dirty}>
          <Button
            variant="secondary"
            disabled={!dirty || pending}
            onClick={() => {
              setValues({});
              setBad(new Set());
            }}
          >
            {t("discard")}
          </Button>
          <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
            {t("save")}
          </Button>
        </SaveBar>
      </form>
    </Card>
  );
}

/**
 * Marks (US-1703): choose a section and an exam, see the grid with each student's overall
 * percentage, enter a subject's marks or import the exam's sheet. Exams are added by the exam
 * coordinator or principal.
 */
export function MarksScreen() {
  const t = useTranslations("marks");
  const tn = useTranslations("school.nav");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can([PERM.marksRead, PERM.marksRecord, PERM.examManage]);
  const canRead = can(PERM.marksRead);
  const canRecord = can(PERM.marksRecord);
  const [section, setSection] = useState("");
  const [exam, setExam] = useState("");
  const exams = useExams(allowed);
  const grid = useMarks(section, exam, canRead);
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: KEYS.marks(section, exam) });

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      />
      {meLoaded && !allowed ? (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      ) : (
        <>
          <Card padding="sm">
            <div className="grid gap-3 sm:flex sm:flex-wrap sm:items-end sm:gap-4">
              <SectionPicker label={t("section")} value={section} onChange={setSection} />
              <SelectField
                label={t("exam")}
                value={exam}
                placeholder={t("chooseExam")}
                onChange={(event) => setExam(event.target.value)}
                options={
                  exams.status === "ready"
                    ? exams.data.map((e) => ({
                        value: e.id,
                        label: `${e.name} (${formatDate(e.held_on) ?? e.held_on})`,
                      }))
                    : []
                }
              />
            </div>
          </Card>
          {!section || !exam ? (
            <EmptyState icon="clipboard" title={t("pickTitle")} body={t("pickBody")} />
          ) : canRead ? (
            <LoadGate data={grid}>
              {(value) => (
                <>
                  <Card title={t("gridTitle", { exam: value.exam.name })}>
                    <MarksTable grid={value} />
                  </Card>
                  {canRecord ? <SubjectEntry grid={value} onSaved={refresh} /> : null}
                </>
              )}
            </LoadGate>
          ) : null}
          {canRecord && section && exam ? (
            <SheetImport<MarkIn, MarksSheet>
              section={section}
              title={t("import.title")}
              description={t("import.description")}
              sample={
                <TableScroll label={t("import.sampleLabel")}>
                  <Table density="compact">
                    <THead>
                      <Tr>
                        <Th>{t("import.admissionNo")}</Th>
                        <Th>{t("import.subjectA")}</Th>
                        <Th>{t("import.subjectB")}</Th>
                      </Tr>
                    </THead>
                    <TBody>
                      <Tr>
                        <Td>{t("import.maxRow")}</Td>
                        <Td>100</Td>
                        <Td>50</Td>
                      </Tr>
                      <Tr>
                        <Td>SYN-101</Td>
                        <Td>72</Td>
                        <Td>AB</Td>
                      </Tr>
                    </TBody>
                  </Table>
                </TableScroll>
              }
              preview={(documentId) =>
                unwrap(
                  api.POST("/api/v1/sections/{section_id}/exams/{exam_id}/marks/sheet", {
                    params: { path: { section_id: section, exam_id: exam } },
                    body: { document_id: documentId },
                  }),
                )
              }
              commit={(entries) =>
                unwrap(
                  api.POST("/api/v1/sections/{section_id}/exams/{exam_id}/marks", {
                    params: { path: { section_id: section, exam_id: exam } },
                    body: { entries, source: "import" },
                  }),
                )
              }
              summary={(sheet) =>
                t("import.summary", {
                  entries: sheet.entries.length,
                  students: sheet.students,
                  subjects: sheet.subjects.length,
                })
              }
              onSaved={refresh}
            />
          ) : null}
          {can(PERM.examManage) ? <NewExamCard onCreated={setExam} /> : null}
        </>
      )}
    </div>
  );
}
