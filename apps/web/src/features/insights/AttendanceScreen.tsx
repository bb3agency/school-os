"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
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

/** One day of the register as a form: every student gets one status (US-1701). */
function DayForm({ day, canRecord }: { day: AttendanceDay; canRecord: boolean }) {
  const t = useTranslations("attendance");
  const api = useBffClient("staff");
  const queryClient = useQueryClient();
  // Keyed by section and date in the parent, so a new day starts from its own statuses.
  const [statuses, setStatuses] = useState(() => {
    const out: Record<string, AttendanceStatus> = {};
    for (const row of day.students) out[row.student.student_id] = row.status ?? "present";
    return out;
  });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<number | null>(null);

  if (day.students.length === 0) {
    return <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />;
  }

  async function save() {
    setPending(true);
    setError(undefined);
    setSaved(null);
    try {
      const entries: AttendanceEntry[] = day.students.map((row) => ({
        student_id: row.student.student_id,
        on_date: day.on_date,
        status: statuses[row.student.student_id] ?? "present",
      }));
      const out = await unwrap(
        api.POST("/api/v1/sections/{section_id}/attendance", {
          params: { path: { section_id: day.section.id } },
          body: { entries, source: "mark" },
        }),
      );
      setSaved(out.written);
      await queryClient.invalidateQueries({ queryKey: KEYS.attendance });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  const counts = ATTENDANCE_STATUSES.map(
    (status) =>
      `${t(`status.${status}`)}: ${Object.values(statuses).filter((s) => s === status).length}`,
  ).join(" · ");

  return (
    <div className="space-y-3">
      {!day.marked ? <Alert tone="info">{t("notMarkedYet")}</Alert> : null}
      <p className="text-sm text-ink-muted" aria-live="polite">
        {counts}
      </p>
      <TableScroll label={t("dayTable", { section: day.section.label })}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("columns.roll")}</Th>
              <Th>{t("columns.student")}</Th>
              <Th>{t("columns.status")}</Th>
            </Tr>
          </THead>
          <TBody>
            {day.students.map((row) => {
              const id = row.student.student_id;
              const name = row.student.full_name ?? t("unnamed");
              return (
                <Tr key={id}>
                  <Td>
                    <span className="font-mono">{row.student.roll_no ?? "—"}</span>
                  </Td>
                  <Td>
                    <span className="break-words">{name}</span>
                  </Td>
                  <Td>
                    <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
                      <legend className="sr-only">{t("statusFor", { name })}</legend>
                      {ATTENDANCE_STATUSES.map((status) => (
                        <label
                          key={status}
                          className="inline-flex min-h-6 items-center gap-1 text-sm text-ink"
                        >
                          <input
                            type="radio"
                            name={`status-${id}`}
                            value={status}
                            checked={statuses[id] === status}
                            disabled={!canRecord}
                            onChange={() => setStatuses({ ...statuses, [id]: status })}
                          />
                          {t(`status.${status}`)}
                        </label>
                      ))}
                    </fieldset>
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      </TableScroll>
      {canRecord ? (
        <div className="flex flex-wrap gap-2" data-print="hide">
          <Button
            onClick={() => void save()}
            disabled={pending}
            aria-disabled={pending || undefined}
          >
            {t("save")}
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              setStatuses(Object.fromEntries(Object.keys(statuses).map((k) => [k, "present"])))
            }
          >
            {t("allPresent")}
          </Button>
        </div>
      ) : null}
      {saved !== null ? (
        <Alert tone="success" live>
          {t("saved", { count: saved })}
        </Alert>
      ) : null}
      <ApiErrorAlert error={error} />
      <ProblemList error={error} />
    </div>
  );
}

/** The month register (students × school days), printable on A4 landscape (FR-ATT-003). */
function MonthRegister({ section, month }: { section: string; month: string }) {
  const t = useTranslations("attendance");
  const data = useAttendanceMonth(section, month, true);
  return (
    <LoadGate data={data}>
      {(register) =>
        register.students.length === 0 ? (
          <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-ink-muted">
              {t("schoolDays", { count: register.school_days.length })}
            </p>
            <TableScroll label={t("monthTable", { section: register.section.label, month })}>
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
                    <Th>{t("columns.absent")}</Th>
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
                      <Td>
                        <span className="font-mono">{row.counts.absent ?? 0}</span>
                      </Td>
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
  );
}

/**
 * Attendance (US-1701, US-1702): mark a section's day, see the month register and print it, or
 * import a month from the paper register's sheet. Class teachers see only their sections.
 */
export function AttendanceScreen() {
  const t = useTranslations("attendance");
  const tn = useTranslations("school.nav");
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

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
        actions={
          view === "month" && section ? (
            <Button variant="secondary" onClick={() => window.print()} data-print="hide">
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
          <Card>
            <div className="flex flex-wrap items-end gap-4" data-print="hide">
              <SectionPicker label={t("section")} value={section} onChange={setSection} />
              <SegmentedControl
                legend={t("viewLabel")}
                legendVisible
                size="sm"
                value={view}
                onValueChange={(value) => setView(value as "day" | "month")}
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
                  onChange={(event) => setDate(event.target.value)}
                />
              ) : (
                <TextField
                  type="month"
                  label={t("month")}
                  value={month}
                  onChange={(event) => setMonth(event.target.value)}
                />
              )}
            </div>
          </Card>
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
                  />
                )}
              </LoadGate>
            </Card>
          ) : (
            <Card title={t("monthTitle", { month })}>
              <MonthRegister section={section} month={month} />
            </Card>
          )}
          {canRecord && section ? (
            <SheetImport<AttendanceEntry, AttendanceSheet>
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
          ) : null}
        </>
      )}
    </div>
  );
}
