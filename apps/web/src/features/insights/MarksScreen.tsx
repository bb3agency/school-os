"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { newIdempotencyKey, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { zodErrorKeys } from "@/lib/forms";
import { translateOr } from "@/lib/i18n-dynamic";
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
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
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
              <Th key={subject}>{subject}</Th>
            ))}
            <Th>{t("columns.percent")}</Th>
          </Tr>
        </THead>
        <TBody>
          {grid.students.map((row) => (
            <Tr key={row.student.student_id}>
              <Td>
                <span className="font-mono">{row.student.roll_no ?? "—"}</span>
              </Td>
              <Td>{row.student.full_name ?? t("unnamed")}</Td>
              {grid.subjects.map((subject) => {
                const mark = row.marks.find((m) => m.subject === subject);
                return (
                  <Td key={subject}>
                    <span className="font-mono">
                      {mark
                        ? mark.absent
                          ? t("absentShort")
                          : `${Number(mark.marks)}/${Number(mark.max_marks)}`
                        : ""}
                    </span>
                  </Td>
                );
              })}
              <Td>
                <span className="font-mono">
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

/** Enter one subject's marks for the whole section (marks.record; FR-MRK-002). */
function SubjectEntry({ grid, onSaved }: { grid: MarksGrid; onSaved: () => Promise<void> }) {
  const t = useTranslations("marks.entry");
  const api = useBffClient("staff");
  const [subject, setSubject] = useState("");
  const [max, setMax] = useState("100");
  const [values, setValues] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<number | null>(null);
  const [pending, setPending] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProblem(null);
    setError(undefined);
    setSaved(null);
    const maximum = Number(max);
    if (!subject.trim()) return setProblem("subjectRequired");
    if (!(maximum > 0 && maximum <= 1000)) return setProblem("maxInvalid");
    const entries: MarkIn[] = [];
    for (const row of grid.students) {
      const raw = (values[row.student.student_id] ?? "").trim();
      if (!raw) continue;
      if (/^ab$/i.test(raw)) {
        entries.push({
          student_id: row.student.student_id,
          subject: subject.trim(),
          max_marks: max,
          marks: null,
          absent: true,
        });
        continue;
      }
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0 || value > maximum)
        return setProblem("marksInvalid");
      entries.push({
        student_id: row.student.student_id,
        subject: subject.trim(),
        max_marks: max,
        marks: raw,
        absent: false,
      });
    }
    if (entries.length === 0) return setProblem("nothingEntered");
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

  return (
    <Card title={t("title")} description={t("description")}>
      <form onSubmit={(event) => void save(event)} noValidate className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <TextField
            label={t("subject")}
            value={subject}
            maxLength={60}
            onChange={(event) => setSubject(event.target.value)}
          />
          <TextField
            label={t("max")}
            type="number"
            inputMode="decimal"
            min={1}
            max={1000}
            value={max}
            onChange={(event) => setMax(event.target.value)}
          />
        </div>
        <TableScroll label={t("title")}>
          <Table density="compact">
            <THead>
              <Tr>
                <Th>{t("roll")}</Th>
                <Th>{t("student")}</Th>
                <Th>{t("marks")}</Th>
              </Tr>
            </THead>
            <TBody>
              {grid.students.map((row) => {
                const id = row.student.student_id;
                const name = row.student.full_name ?? t("unnamed");
                return (
                  <Tr key={id}>
                    <Td>
                      <span className="font-mono">{row.student.roll_no ?? "—"}</span>
                    </Td>
                    <Td>{name}</Td>
                    <Td>
                      <input
                        aria-label={t("marksFor", { name })}
                        className="min-h-10 w-24 rounded-md border border-border-control bg-surface px-2 font-mono text-ink"
                        inputMode="decimal"
                        value={values[id] ?? ""}
                        onChange={(event) => setValues({ ...values, [id]: event.target.value })}
                      />
                    </Td>
                  </Tr>
                );
              })}
            </TBody>
          </Table>
        </TableScroll>
        <p className="text-xs text-ink-subtle">{t("hint")}</p>
        <Button type="submit" disabled={pending} aria-disabled={pending || undefined}>
          {t("save")}
        </Button>
        {problem ? (
          <Alert tone="danger" live>
            {translateOr(t, `problem.${problem}`, "problem.marksInvalid")}
          </Alert>
        ) : null}
        {saved !== null ? (
          <Alert tone="success" live>
            {t("saved", { count: saved })}
          </Alert>
        ) : null}
        <ApiErrorAlert error={error} />
        <ProblemList error={error} />
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
    <div className="space-y-6">
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
          <Card>
            <div className="flex flex-wrap items-end gap-4">
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
