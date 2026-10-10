"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { useSchoolStructure, useSectionOptions } from "@/features/students/StudentList";
import { Link } from "@/i18n/navigation";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { ConsentStatusPill } from "./parts";
import {
  APAAR_KEYS,
  APAAR_READ,
  APAAR_RECORD,
  CONSENT_STATUSES,
  FORM_LANGUAGES,
  SETTINGS_MANAGE,
  isConsentStatus,
  sectionFormsHref,
  type ConsentRow,
  type ConsentStatus,
  type FormLanguage,
  type StatusCounts,
} from "./types";

const PAGE_SIZE = 50;

/**
 * APAAR consent register (US-1901..US-1903, FR-APC-001..006, ADR-0039): given / refused /
 * pending counts per section, the students with their state (the pending ones are the
 * follow-up list), printable forms that let parents refuse, and the school's form language.
 * Class teachers see only their sections (the API decides). Staff screens stay English; only
 * the printed parent form may be Telugu (owner decision D9).
 */
export function ApaarScreen() {
  const t = useTranslations("apaar");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const structure = useSchoolStructure();
  const sections = useSectionOptions(structure);
  const [sectionId, setSectionId] = useState("");
  const [status, setStatus] = useState<ConsentStatus | "">("");

  const summary = useQuery({
    queryKey: APAAR_KEYS.summary(sectionId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/apaar/consents/summary", {
          params: { query: sectionId ? { section_id: sectionId } : {} },
        }),
      ),
    enabled: can(APAAR_READ),
    retry: false,
  });
  const list = useInfiniteQuery({
    queryKey: APAAR_KEYS.list(sectionId, status),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/apaar/consents", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(sectionId ? { section_id: sectionId } : {}),
              ...(status ? { status } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: can(APAAR_READ),
    retry: false,
  });
  const rows = list.data?.pages.flatMap((page) => page.data) ?? [];

  if (!can([APAAR_READ, APAAR_RECORD])) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const columns: Column<ConsentRow>[] = [
    {
      key: "student",
      stack: "title",
      header: t("colStudent"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <Link
            href={`/apaar/students/${row.student_id}`}
            className="font-semibold text-primary underline-offset-4 hover:underline"
          >
            <Value>{row.display_name}</Value>
          </Link>
          {row.admission_no ? (
            <span className="font-mono text-xs text-ink-muted">{row.admission_no}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "class",
      header: t("colClass"),
      cell: (row) => <Value>{row.class_section}</Value>,
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => <ConsentStatusPill status={row.status} />,
    },
    {
      key: "decided",
      stack: "field",
      header: t("colDecidedOn"),
      cell: (row) => <Value>{row.decided_on ? formatDate(row.decided_on) : null}</Value>,
    },
    {
      key: "form",
      header: t("colForm"),
      cell: (row) => (row.has_form ? t("formOnFile") : t("noForm")),
    },
    {
      key: "apaar",
      header: t("colApaarId"),
      cell: (row) => (row.has_apaar_id ? t("apaarRecorded") : t("apaarNone")),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
      />
      <Alert tone="info" title={t("refusalTitle")}>
        <p>{t("refusalBody")}</p>
        <p className="mt-1 text-xs">{t("refusalSource")}</p>
      </Alert>

      <Card title={t("filtersTitle")}>
        <div className="grid gap-4 md:grid-cols-[minmax(0,18rem)_1fr] md:items-end">
          <SelectField
            label={t("section")}
            placeholder={t("allSections")}
            value={sectionId}
            onChange={(event) => setSectionId(event.currentTarget.value)}
            options={sections}
          />
          <SegmentedControl
            legend={t("filterStatus")}
            legendVisible
            size="sm"
            value={status}
            onValueChange={(value) => setStatus(isConsentStatus(value) ? value : "")}
            options={[
              { value: "", label: tc("all") },
              ...CONSENT_STATUSES.map((value) => ({ value, label: t(`status.${value}`) })),
            ]}
          />
        </div>
      </Card>

      {can(APAAR_READ) ? (
        <section aria-labelledby="apaar-summary" className="space-y-3">
          <h2 id="apaar-summary" className="text-lg font-semibold text-ink">
            {t("summaryTitle")}
          </h2>
          {summary.data ? (
            <>
              <Totals counts={summary.data.totals} />
              <SectionTable sections={summary.data.sections} />
            </>
          ) : (
            <ApiErrorAlert error={summary.error ?? undefined} />
          )}
        </section>
      ) : null}

      {can(APAAR_READ) ? (
        <section aria-labelledby="apaar-students" className="space-y-3">
          <h2 id="apaar-students" className="text-lg font-semibold text-ink">
            {status === "pending" ? t("followUpTitle") : t("studentsTitle")}
          </h2>
          <DataTable
            stacked
            caption={t("studentsTitle")}
            captionHidden
            columns={columns}
            state={toLoadable({ ...list, data: rows })}
            rowKey={(row) => row.student_id}
            emptyTitle={t("emptyTitle")}
            emptyBody={t("emptyBody")}
          />
          {list.hasNextPage ? (
            <div className="flex justify-center">
              <Button
                variant="secondary"
                onClick={() => void list.fetchNextPage()}
                disabled={list.isFetchingNextPage}
              >
                {list.isFetchingNextPage ? tc("loading") : t("showMore")}
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}

      <FormLanguageCard />
    </div>
  );
}

function Totals({ counts }: { counts: StatusCounts }) {
  const t = useTranslations("apaar");
  const tc = useTranslations("common");
  const items = [
    { key: "total", label: t("totalStudents"), value: counts.total },
    { key: "given", label: t("status.given"), value: counts.given },
    { key: "refused", label: t("status.refused"), value: counts.refused },
    { key: "pending", label: t("status.pending"), value: counts.pending },
    { key: "withdrawn", label: t("status.withdrawn"), value: counts.withdrawn },
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-5">
      {items.map((item) => (
        <StatCard
          key={item.key}
          label={item.label}
          value={String(item.value)}
          unavailableLabel={tc("notAvailableYetTitle")}
        />
      ))}
    </dl>
  );
}

function SectionTable({
  sections,
}: {
  sections: readonly { section_id: string; class_section: string; counts: StatusCounts }[];
}) {
  const t = useTranslations("apaar");
  type Row = (typeof sections)[number];
  const columns: Column<Row>[] = [
    { key: "section", stack: "title", header: t("colClass"), cell: (row) => row.class_section },
    { key: "total", header: t("totalStudents"), cell: (row) => row.counts.total },
    { key: "given", header: t("status.given"), cell: (row) => row.counts.given },
    { key: "refused", header: t("status.refused"), cell: (row) => row.counts.refused },
    { key: "pending", header: t("status.pending"), cell: (row) => row.counts.pending },
    { key: "withdrawn", header: t("status.withdrawn"), cell: (row) => row.counts.withdrawn },
    {
      key: "print",
      stack: "actions",
      header: t("colPrint"),
      cell: (row) => (
        <span className="flex flex-col gap-1">
          <a
            href={sectionFormsHref(row.section_id, "pending")}
            target="_blank"
            rel="noopener"
            className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
          >
            {t("printPending")}
            <span className="sr-only">: {row.class_section}</span>
          </a>
          <a
            href={sectionFormsHref(row.section_id)}
            target="_blank"
            rel="noopener"
            className="whitespace-nowrap text-primary underline-offset-4 hover:underline"
          >
            {t("printAll")}
            <span className="sr-only">: {row.class_section}</span>
          </a>
        </span>
      ),
    },
  ];
  return (
    <DataTable
      stacked
      caption={t("summaryTitle")}
      captionHidden
      columns={columns}
      state={{ status: "ready", data: [...sections] }}
      rowKey={(row) => row.section_id}
      emptyTitle={t("noSectionsTitle")}
      emptyBody={t("noSectionsBody")}
    />
  );
}

/** The printed parent form's language, per school (owner decision D9; step-up to change). */
function FormLanguageCard() {
  const t = useTranslations("apaar.settings");
  const tc = useTranslations("common");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [saved, setSaved] = useState(false);
  const settings = useQuery({
    queryKey: APAAR_KEYS.settings,
    queryFn: () => unwrap(api.GET("/api/v1/apaar/settings")),
    retry: false,
  });
  const manage = can(SETTINGS_MANAGE);
  const current = settings.data?.form_language ?? "en";

  async function save(language: FormLanguage) {
    if (!settings.data) return;
    setPending(true);
    setError(undefined);
    setSaved(false);
    try {
      await unwrap(
        api.PUT("/api/v1/apaar/settings", {
          headers: { "If-Match": `W/"${settings.data.version}"` },
          body: { form_language: language },
        }),
      );
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: APAAR_KEYS.settings });
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-3">
        <p className="text-sm">
          {t("current")} <strong>{t(`languages.${current}`)}</strong>
        </p>
        {manage && settings.data ? (
          <div className="flex flex-wrap gap-2">
            {FORM_LANGUAGES.filter((language) => language !== current).map((language) => (
              <Button
                key={language}
                variant="secondary"
                size="sm"
                disabled={pending}
                onClick={() => void save(language)}
              >
                <Icon name="check" className="size-4" />
                {pending ? tc("working") : t("switchTo", { language: t(`languages.${language}`) })}
              </Button>
            ))}
          </div>
        ) : null}
        <div role="status" aria-live="polite">
          {saved ? <p className="text-sm">{t("saved")}</p> : null}
        </div>
        <ApiErrorAlert error={error} />
      </div>
    </Card>
  );
}
