"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { useStructureLists } from "@/features/academic-structure/data";
import { LoadGate } from "@/features/insights/parts";
import { downloadSheet } from "@/features/sheets/download";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import { Link } from "@/i18n/navigation";
import { newIdempotencyKey, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import {
  ATTENDANCE_PERM,
  CERTIFICATE_PERM,
  PACK_KEYS,
  SCHEME_PERMS,
  STATS_PERM,
  attendancePrintHref,
  attendanceXlsxPath,
  packApi,
  percentText,
  statsPrintHref,
  statsXlsxPath,
  type AttendanceQuery,
  type EnrolmentStatistics,
  type EnrolmentStatsTable,
  type SchemeAttendanceList,
  type SchemeCertificates,
  type StatsQuery,
} from "./api";

/** Print: check first (step-up, nothing audited), then open the API page in a new tab. */
function PrintAction({ check, href }: { check: () => Promise<unknown>; href: string }) {
  const t = useTranslations("exportPacks");
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  async function prepare() {
    setPending(true);
    setError(undefined);
    try {
      await check();
      setReady(true);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <span className="flex flex-col gap-2">
      {ready ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonClasses("primary", "md")}
        >
          <Icon name="file" className="size-4" />
          {t("openPrint")}
          <span className="sr-only"> ({t("newTab")})</span>
        </a>
      ) : (
        <Button onClick={() => void prepare()} disabled={pending}>
          <Icon name="file" className="size-4" />
          {pending ? t("preparing") : t("print")}
        </Button>
      )}
      <ApiErrorAlert error={error} />
    </span>
  );
}

function DownloadAction({ path, fallbackName }: { path: string; fallbackName: string }) {
  const t = useTranslations("exportPacks");
  const locale = useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<string | null>(null);
  async function download() {
    setBusy(true);
    setError(undefined);
    setSaved(null);
    try {
      setSaved(await downloadSheet({ path, method: "GET", locale, fallbackName }));
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="flex flex-col gap-2">
      <Button variant="secondary" onClick={() => void download()} disabled={busy}>
        <Icon name="arrowDown" className="size-4" />
        {busy ? t("downloading") : t("downloadXlsx")}
      </Button>
      <span role="status" aria-live="polite" className="text-xs text-ink-muted">
        {saved ? t("downloaded", { name: saved }) : ""}
      </span>
      <ApiErrorAlert error={error} />
    </span>
  );
}

function BackToExports() {
  const t = useTranslations("exportPacks");
  return (
    <ButtonLink href="/exports" variant="secondary">
      {t("back")}
    </ButtonLink>
  );
}

/* ------------------------------------------------------------------ FO-06 enrolment statistics */

/**
 * FO-06 (FR-EXP-009..019, FR-EXP-029): enrolment counts per class on one day of a year in the
 * UDISE+ table shapes. Counts only; the restricted tables appear only for school-wide holders
 * of `student.read_sensitive` (the API decides). Print and XLSX need a recent MFA sign-in.
 */
export function EnrolmentStatsScreen() {
  const t = useTranslations("enrolmentStats");
  const can = useStaffCan();
  const allowed = can(STATS_PERM);
  const api = useBffClient("staff");
  const { years } = useStructureLists();
  const [yearId, setYearId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [query, setQuery] = useState<StatsQuery>({});
  const list = years.status === "ready" ? years.data : [];
  const stats = useApiQuery(PACK_KEYS.stats(query), () => packApi.stats(api, query), {
    enabled: allowed,
  });
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} actions={<BackToExports />} />
      {allowed ? (
        <>
          <form
            className="flex flex-wrap items-end gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery({ ...(yearId ? { yearId } : {}), ...(asOf ? { asOf } : {}) });
            }}
          >
            <SelectField
              className="w-56"
              label={t("year")}
              value={yearId}
              disabled={years.status !== "ready"}
              onChange={(event) => setYearId(event.target.value)}
              options={[
                { value: "", label: t("currentYear") },
                ...list.map((y) => ({ value: y.id, label: y.label })),
              ]}
            />
            <TextField
              className="w-56"
              type="date"
              label={t("asOf")}
              hint={t("asOfHint")}
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
            />
            <Button type="submit" variant="secondary">
              {t("show")}
            </Button>
          </form>
          <LoadGate data={stats}>{(data) => <StatsSheet data={data} />}</LoadGate>
        </>
      ) : (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      )}
    </div>
  );
}

function StatsSheet({ data }: { data: EnrolmentStatistics }) {
  const t = useTranslations("enrolmentStats");
  const api = useBffClient("staff");
  const day = formatDate(data.as_of) ?? data.as_of;
  const fixed: StatsQuery = { yearId: data.year.id, asOf: data.as_of };
  return (
    <div className="space-y-6">
      {data.verified ? null : (
        <Alert tone="warning" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      )}
      {data.restricted_hidden ? (
        <Alert tone="info" title={t("restrictedTitle")}>
          {t("restrictedBody")}
        </Alert>
      ) : null}
      <Card
        title={t("summaryTitle", { year: data.year.label, date: day })}
        description={t("summaryBody", { count: data.students })}
        actions={
          <span className="flex flex-wrap items-start gap-3">
            <PrintAction
              check={() => packApi.checkStatsPrint(api, fixed)}
              href={statsPrintHref(fixed)}
            />
            <DownloadAction
              path={statsXlsxPath(fixed)}
              fallbackName={`enrolment-statistics-${data.year.label}.xlsx`}
            />
          </span>
        }
      >
        {data.gaps.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("noGaps")}</p>
        ) : (
          <ul className="flex flex-wrap gap-2" aria-label={t("gapsLabel")}>
            {data.gaps.map((gap) => (
              <li key={gap.attribute_key}>
                <Pill variant="negative">
                  {t("gap", { count: gap.count, field: gap.label.toLowerCase() })}
                </Pill>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {data.students === 0 ? (
        <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />
      ) : (
        data.tables.map((table) => <StatsTable key={table.key} table={table} />)
      )}
    </div>
  );
}

function StatsTable({ table }: { table: EnrolmentStatsTable }) {
  const t = useTranslations("enrolmentStats");
  return (
    <Card title={table.title} description={table.restricted ? t("restrictedTable") : undefined}>
      <TableScroll label={table.title}>
        <Table>
          <THead>
            <Tr>
              <Th>{t("colClass")}</Th>
              {table.columns.map((column) => (
                <Th key={column.key} numeric>
                  {column.label}
                </Th>
              ))}
              <Th numeric>{t(`total.${table.key}`)}</Th>
            </Tr>
          </THead>
          <TBody>
            {[...table.rows, table.totals].map((row, index) => {
              const isTotal = index === table.rows.length;
              return (
                <Tr key={row.class_id ?? "total"}>
                  <Td className={isTotal ? "font-semibold" : undefined}>
                    {isTotal ? t("totalRow") : row.label}
                  </Td>
                  {row.counts.map((count, i) => (
                    <Td key={table.columns[i]?.key ?? i} numeric>
                      {count}
                    </Td>
                  ))}
                  <Td numeric className="font-semibold">
                    {row.total}
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      </TableScroll>
    </Card>
  );
}

/* --------------------------------------------------------------- FO-11 scheme verification pack */

function todayIso(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/**
 * FO-11 (FR-EXP-020..028, FR-EXP-029): a class-wise attendance list for a scheme's verification
 * (names, admission numbers and the percentage only; no eligibility) and bulk study or bonafide
 * certificates for the students chosen on it, each with its own serial.
 */
export function SchemePackScreen() {
  const t = useTranslations("schemePacks");
  const can = useStaffCan();
  const allowed = SCHEME_PERMS.some((p) => can(p));
  const api = useBffClient("staff");
  const config = useApiQuery(PACK_KEYS.schemes, () => packApi.schemes(api), {
    enabled: allowed,
  });
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} actions={<BackToExports />} />
      {allowed ? (
        <LoadGate data={config}>
          {(cfg) => (
            <SchemeForm
              schemes={cfg.schemes}
              note={cfg.note}
              verified={cfg.verified}
              canRead={can(ATTENDANCE_PERM)}
              canIssue={can(CERTIFICATE_PERM)}
            />
          )}
        </LoadGate>
      ) : (
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      )}
    </div>
  );
}

function SchemeForm({
  schemes,
  note,
  verified,
  canRead,
  canIssue,
}: {
  schemes: readonly { key: string; label: string; note: string }[];
  note: string;
  verified: boolean;
  canRead: boolean;
  canIssue: boolean;
}) {
  const t = useTranslations("schemePacks");
  const api = useBffClient("staff");
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const today = todayIso();
  const [schemeKey, setSchemeKey] = useState(schemes[0]?.key ?? "");
  const [sectionId, setSectionId] = useState("");
  const [fromDate, setFromDate] = useState(`${today.slice(0, 8)}01`);
  const [toDate, setToDate] = useState(today);
  const [query, setQuery] = useState<AttendanceQuery | null>(null);
  const scheme = schemes.find((s) => s.key === schemeKey);
  const list = useApiQuery(
    query ? PACK_KEYS.attendance(query) : ["staff", "exports", "scheme-attendance", "none"],
    () => (query ? packApi.attendance(api, query) : Promise.reject(new Error("no query"))),
    { enabled: query !== null },
  );
  return (
    <div className="space-y-6">
      <Alert tone={verified ? "info" : "warning"} title={t("noEligibilityTitle")}>
        {note}
      </Alert>
      {canRead ? (
        <form
          className="grid max-w-4xl gap-4 sm:grid-cols-2 lg:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (sectionId && schemeKey) setQuery({ schemeKey, sectionId, fromDate, toDate });
          }}
        >
          <SelectField
            label={t("scheme")}
            value={schemeKey}
            onChange={(event) => setSchemeKey(event.target.value)}
            options={schemes.map((s) => ({ value: s.key, label: s.label }))}
          />
          <SelectField
            label={t("section")}
            value={sectionId}
            required
            onChange={(event) => setSectionId(event.target.value)}
            options={[{ value: "", label: t("chooseSection") }, ...sections]}
          />
          <TextField
            type="date"
            label={t("from")}
            value={fromDate}
            max={today}
            required
            onChange={(event) => setFromDate(event.target.value)}
          />
          <TextField
            type="date"
            label={t("to")}
            value={toDate}
            max={today}
            required
            onChange={(event) => setToDate(event.target.value)}
          />
          <div className="sm:col-span-2 lg:col-span-4">
            <Button type="submit" variant="secondary" disabled={!sectionId}>
              {t("show")}
            </Button>
          </div>
        </form>
      ) : (
        <Alert tone="info" title={t("noAttendanceTitle")}>
          {t("noAttendanceBody")}
        </Alert>
      )}
      {scheme ? <p className="max-w-3xl text-sm text-ink-muted">{scheme.note}</p> : null}
      {query ? (
        <LoadGate data={list}>
          {(data) => <AttendanceCard data={data} query={query} canIssue={canIssue} />}
        </LoadGate>
      ) : null}
    </div>
  );
}

function AttendanceCard({
  data,
  query,
  canIssue,
}: {
  data: SchemeAttendanceList;
  query: AttendanceQuery;
  canIssue: boolean;
}) {
  const t = useTranslations("schemePacks");
  const api = useBffClient("staff");
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const from = formatDate(data.from_date) ?? data.from_date;
  const to = formatDate(data.to_date) ?? data.to_date;
  const allChosen = data.students.length > 0 && chosen.size === data.students.length;
  function toggle(id: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  if (data.students.length === 0) {
    return <EmptyState icon="users" title={t("emptyTitle")} body={t("emptyBody")} />;
  }
  return (
    <div className="space-y-6">
      <Card
        title={t("listTitle", { section: data.section_label, from, to })}
        description={t("listBody", { count: data.students.length })}
        actions={
          <span className="flex flex-wrap items-start gap-3">
            <PrintAction
              check={() => packApi.checkAttendancePrint(api, query)}
              href={attendancePrintHref(query)}
            />
            <DownloadAction
              path={attendanceXlsxPath(query)}
              fallbackName={`attendance-${data.section_label}.xlsx`}
            />
          </span>
        }
      >
        <TableScroll label={t("tableLabel", { section: data.section_label })}>
          <Table>
            <THead>
              <Tr>
                {canIssue ? (
                  <Th>
                    <label className="inline-flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="size-4"
                        checked={allChosen}
                        onChange={() =>
                          setChosen(
                            allChosen ? new Set() : new Set(data.students.map((s) => s.student_id)),
                          )
                        }
                      />
                      <span className="sr-only">{t("chooseAll")}</span>
                    </label>
                  </Th>
                ) : null}
                <Th>{t("colRoll")}</Th>
                <Th>{t("colAdmission")}</Th>
                <Th>{t("colStudent")}</Th>
                <Th numeric>{t("colCounted")}</Th>
                <Th numeric>{t("colPresent")}</Th>
                <Th numeric>{t("colPercent")}</Th>
              </Tr>
            </THead>
            <TBody>
              {data.students.map((s) => (
                <Tr key={s.student_id}>
                  {canIssue ? (
                    <Td>
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="size-4"
                          checked={chosen.has(s.student_id)}
                          onChange={() => toggle(s.student_id)}
                        />
                        <span className="sr-only">
                          {t("chooseOne", { name: s.full_name ?? s.admission_no ?? "" })}
                        </span>
                      </label>
                    </Td>
                  ) : null}
                  <Td>{s.roll_no ?? "—"}</Td>
                  <Td>
                    <span className="font-mono">{s.admission_no ?? "—"}</span>
                  </Td>
                  <Td>
                    <span className="break-words">{s.full_name ?? t("unnamed")}</span>
                  </Td>
                  <Td numeric>{s.days_counted}</Td>
                  <Td numeric>{s.days_present}</Td>
                  <Td numeric>{percentText(s.percent, t("noMarks"))}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableScroll>
        <p className="mt-3 text-xs text-ink-muted">{t("countingNote")}</p>
      </Card>
      {canIssue ? (
        <BulkCertificates
          schemeKey={query.schemeKey}
          studentIds={[...chosen]}
          names={new Map(data.students.map((s) => [s.student_id, s.full_name ?? ""] as const))}
          certificateType={data.scheme.certificate_type}
        />
      ) : null}
    </div>
  );
}

function BulkCertificates({
  schemeKey,
  studentIds,
  names,
  certificateType,
}: {
  schemeKey: string;
  studentIds: readonly string[];
  names: ReadonlyMap<string, string>;
  certificateType: "study" | "bonafide";
}) {
  const t = useTranslations("schemePacks");
  const api = useBffClient("staff");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [result, setResult] = useState<SchemeCertificates | null>(null);
  const [key] = useState(newIdempotencyKey);
  async function issue() {
    setBusy(true);
    setError(undefined);
    try {
      setResult(await packApi.issueCertificates(api, schemeKey, studentIds, key));
      setConfirming(false);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card
      title={t("certificatesTitle")}
      description={t("certificatesBody", { type: t(`type.${certificateType}`) })}
    >
      <div className="space-y-4">
        {result ? (
          <>
            <Alert tone={result.refused > 0 ? "warning" : "success"} title={t("resultTitle")} live>
              {t("resultBody", { issued: result.issued, refused: result.refused })}
            </Alert>
            <ul className="space-y-1 text-sm">
              {result.results.map((r) => (
                <li key={r.student_id} className="flex flex-wrap gap-2">
                  <span className="font-semibold break-words">
                    {names.get(r.student_id) || t("unnamed")}
                  </span>
                  {r.certificate_id ? (
                    <Link
                      href={`/certificates/${r.certificate_id}`}
                      className="text-primary underline underline-offset-4"
                    >
                      {r.serial ?? t("openCertificate")}
                    </Link>
                  ) : (
                    <span className="text-ink-muted">
                      {t(`refusal.${r.error_code === "not_found" ? "not_found" : "other"}`)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </>
        ) : confirming ? (
          <div className="space-y-3">
            <p className="text-sm">{t("confirmBody", { count: studentIds.length })}</p>
            <span className="flex flex-wrap gap-3">
              <Button onClick={() => void issue()} disabled={busy}>
                {busy ? t("issuing") : t("confirm", { count: studentIds.length })}
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(false)} disabled={busy}>
                {t("cancel")}
              </Button>
            </span>
          </div>
        ) : (
          <Button onClick={() => setConfirming(true)} disabled={studentIds.length === 0}>
            {t("issue", { count: studentIds.length })}
          </Button>
        )}
        <ApiErrorAlert error={error} />
      </div>
    </Card>
  );
}
