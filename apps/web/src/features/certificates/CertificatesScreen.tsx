"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
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

  const columns: Column<Certificate>[] = [
    {
      key: "requested",
      header: t("colRequested"),
      cell: (row) => (
        <span className="font-mono text-xs whitespace-nowrap text-ink-muted">
          <Value>{formatDateTime(row.issued_at ?? row.requested_at)}</Value>
        </span>
      ),
    },
    {
      key: "certificate",
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

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("crumbHome"), href: "/" }, { label: t("title") }]}
        actions={
          can(REGISTER_READ) ? (
            <ButtonLink href="/registers" variant="secondary">
              <Icon name="clipboard" className="size-4" />
              {t("registers.nav")}
            </ButtonLink>
          ) : null
        }
      />
      <Card title={t("filtersTitle")}>
        <form method="get" className="space-y-4">
          {filters.studentId ? (
            <div className="flex flex-wrap items-center gap-3">
              <input type="hidden" name="student_id" value={filters.studentId} />
              <Pill variant="date" size="md">
                {t("oneStudentOnly")}
              </Pill>
              <Link href="/certificates" className="text-sm text-primary underline">
                {t("allStudents")}
              </Link>
              {can(CERT_ISSUE) ? (
                <ButtonLink
                  href={`/students/${filters.studentId}/certificates/new`}
                  variant="secondary"
                  size="sm"
                >
                  <Icon name="plus" className="size-4" />
                  {t("issueForStudent")}
                </ButtonLink>
              ) : null}
            </div>
          ) : null}
          <div className="grid gap-4 md:grid-cols-[minmax(0,16rem)_1fr_auto] md:items-end">
            <SelectField
              name="certificate_type"
              label={t("filterType")}
              defaultValue={filters.certificateType ?? ""}
              placeholder={tc("all")}
              options={CERTIFICATE_TYPES.map((type) => ({
                value: type,
                label: t(`types.${type}`),
              }))}
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
            <Button type="submit">
              <Icon name="filter" className="size-4" />
              {tc("applyFilters")}
            </Button>
          </div>
        </form>
      </Card>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...list, data: rows })}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={can(CERT_ISSUE) ? t("emptyBodyIssuer") : t("emptyBody")}
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
    </div>
  );
}
