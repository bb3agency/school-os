"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import type { FilterChip } from "@/components/ui/FilterChips";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Value } from "@/components/ui/Value";
import { Link, useRouter } from "@/i18n/navigation";
import { toLoadable, unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan } from "@/lib/bff/staff-me";
import { formatDateTime } from "@/lib/format";
import type { CertificateFilters } from "./filters";
import { CertificateStatusBadge, useCertificateTitle } from "./parts";
import {
  CERT_ISSUE,
  CERT_KEYS,
  CERTIFICATE_STATUSES,
  CERTIFICATE_TYPES,
  REGISTER_READ,
  type Certificate,
} from "./types";

const PAGE_SIZE = 50;

/**
 * Certificates and requests (US-1101..US-1105), newest first: the issue register as a list.
 * Transfer certificates waiting for the principal are marked for approvers; the API never
 * offers anyone their own request to approve. Filters live in the URL (type, status, student).
 */
export function CertificatesScreen({ filters }: { filters: CertificateFilters }) {
  const t = useTranslations("certificates");
  const tc = useTranslations("common");
  const tstatus = useTranslations("certificates.status");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const title = useCertificateTitle();
  const router = useRouter();
  const query = {
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.certificateType ? { certificate_type: filters.certificateType } : {}),
    ...(filters.studentId ? { student_id: filters.studentId } : {}),
  };
  const list = useInfiniteQuery({
    queryKey: [...CERT_KEYS.all, "list", query],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/certificates", {
          params: {
            query: { ...query, limit: PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });
  const rows = list.data?.pages.flatMap((one) => one.data) ?? [];

  const columns: ColumnV2<Certificate>[] = [
    {
      key: "requested",
      stack: "field",
      header: t("colRequested"),
      cell: (row) => (
        <span className="num text-xs whitespace-nowrap text-ink-muted">
          <Value>{formatDateTime(row.issued_at ?? row.requested_at)}</Value>
        </span>
      ),
    },
    {
      key: "certificate",
      stack: "title",
      header: t("colCertificate"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold">{title(row)}</span>
          {row.serial ? null : <span className="text-xs text-ink-muted">{t("noSerialYet")}</span>}
        </span>
      ),
    },
    {
      key: "student",
      header: t("colStudent"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="break-anywhere">
            <Value>{row.student_name}</Value>
          </span>
          {row.admission_no ? (
            <span className="font-mono text-xs text-ink-muted">{row.admission_no}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "status",
      header: t("colStatus"),
      cell: (row) => (
        <span className="flex flex-col items-start gap-1">
          <CertificateStatusBadge status={row.status} />
          {row.can_approve ? <Pill variant="dark">{t("waitingForYou")}</Pill> : null}
        </span>
      ),
    },
    {
      key: "open",
      stack: "actions",
      header: tc("actions"),
      cell: (row) => (
        <Link
          href={`/certificates/${row.id}`}
          className="font-semibold whitespace-nowrap text-primary underline-offset-4 hover:underline"
        >
          {t("open")}
          <span className="sr-only">: {title(row)}</span>
        </Link>
      ),
    },
  ];

  // The active filters as chips. Removing one opens the list without that URL parameter (the
  // same as choosing "All" in the filter form, or "Show all students").
  const chips: FilterChip[] = [
    ...(filters.certificateType
      ? [
          {
            id: "certificate_type",
            label: t("filterType"),
            value: t(`types.${filters.certificateType}`),
          },
        ]
      : []),
    ...(filters.status
      ? [{ id: "status", label: t("filterStatus"), value: tstatus(filters.status) }]
      : []),
    ...(filters.studentId
      ? [{ id: "student_id", label: t("colStudent"), value: t("chipOneStudent") }]
      : []),
  ];
  const hrefWithout = (drop: string | null): string => {
    const params = new URLSearchParams();
    if (filters.certificateType && drop !== "certificate_type") {
      params.set("certificate_type", filters.certificateType);
    }
    if (filters.status && drop !== "status") params.set("status", filters.status);
    if (filters.studentId && drop !== "student_id") params.set("student_id", filters.studentId);
    const search = params.toString();
    return search ? `/certificates?${search}` : "/certificates";
  };
  const issueHere = filters.studentId !== null && can(CERT_ISSUE);

  return (
    <div className="space-y-5">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
        actions={
          <>
            {can(REGISTER_READ) ? (
              <ButtonLink href="/registers" variant="secondary">
                <Icon name="clipboard" className="size-4" />
                {t("registers.nav")}
              </ButtonLink>
            ) : null}
            {/* One student's list: issuing for that student is the page's primary action. */}
            {issueHere ? (
              <ButtonLink href={`/students/${filters.studentId}/certificates/new`}>
                <Icon name="plus" className="size-4" />
                {t("issueForStudent")}
              </ButtonLink>
            ) : null}
          </>
        }
      />
      {filters.studentId ? (
        <p className="text-sm text-ink-muted">
          {t("oneStudentOnly")}{" "}
          <Link href="/certificates" className="text-primary underline underline-offset-4">
            {t("allStudents")}
          </Link>
        </p>
      ) : null}
      <DataTableV2
        id="certificates"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        toolbar={
          <form
            method="get"
            aria-label={t("filtersTitle")}
            className="flex w-full flex-wrap items-end gap-3"
          >
            {filters.studentId ? (
              <input type="hidden" name="student_id" value={filters.studentId} />
            ) : null}
            <SelectField
              name="certificate_type"
              label={t("filterType")}
              defaultValue={filters.certificateType ?? ""}
              placeholder={tc("all")}
              options={CERTIFICATE_TYPES.map((type) => ({
                value: type,
                label: t(`types.${type}`),
              }))}
              className="min-w-44 flex-1 sm:flex-none"
            />
            <SegmentedControl
              name="status"
              legend={t("filterStatus")}
              legendVisible
              size="sm"
              defaultValue={filters.status ?? ""}
              options={[
                { value: "", label: tc("all") },
                ...CERTIFICATE_STATUSES.map((status) => ({
                  value: status,
                  label: tstatus(status),
                })),
              ]}
            />
            <Button type="submit" variant="secondary">
              <Icon name="filter" className="size-4" />
              {tc("applyFilters")}
            </Button>
          </form>
        }
        chips={chips}
        onRemoveChip={(id) => router.replace(hrefWithout(id))}
        onClearFilters={() => router.replace("/certificates")}
        emptyTitle={t("emptyTitle")}
        emptyBody={can(CERT_ISSUE) ? t("emptyBodyIssuer") : t("emptyBody")}
        noResultsTitle={t("emptyTitle")}
        onRetry={() => void list.refetch()}
        footer={
          list.hasNextPage ? (
            <div className="flex justify-center">
              <Button
                variant="secondary"
                onClick={() => void list.fetchNextPage()}
                disabled={list.isFetchingNextPage}
              >
                {list.isFetchingNextPage ? tc("loading") : t("showMore")}
              </Button>
            </div>
          ) : undefined
        }
      />
    </div>
  );
}
