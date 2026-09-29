"use client";

import type { AcademicYear } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { useStructureLists } from "@/features/academic-structure/data";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import { PROMOTE } from "./data";

/**
 * Year-end promotion, from the menu (FR-TEN-011, US-202 AC2): the academic years in use, each
 * with a link to promote its students (the current year first). Holders of
 * `tenant.structure.manage` only; the promotion itself is on the year's own page.
 */
export function PromotionsIndexScreen() {
  const t = useTranslations("academicStructure.promotions");
  const ts = useTranslations("school.structure");
  const ty = useTranslations("academicStructure.years");
  const me = useStaffMe();
  const can = useStaffCan();
  const manage = can(PROMOTE);
  const { years } = useStructureLists(false);
  const tn = useTranslations("school.nav");
  const breadcrumb = [
    { label: tn("home"), href: "/" },
    { label: tn("structure"), href: "/settings/structure" },
    { label: t("titlePlain") },
  ];

  if (me === undefined) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("titlePlain")} breadcrumb={breadcrumb} />
        <LoadingState label={t("loading")} />
      </div>
    );
  }
  if (!manage) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("titlePlain")} breadcrumb={breadcrumb} />
        <Alert tone="info" title={t("noAccessTitle")}>
          {t("noAccessBody")}
        </Alert>
      </div>
    );
  }

  const rows =
    years.status === "ready"
      ? {
          status: "ready" as const,
          data: [...years.data].sort((a, b) => Number(b.is_current) - Number(a.is_current)),
        }
      : years;
  const columns: Column<AcademicYear>[] = [
    {
      key: "year",
      header: ts("years.colYear"),
      cell: (row) => (
        <span className="inline-flex flex-wrap items-center gap-2">
          <span>{row.label}</span>
          {row.is_current ? <Pill variant="done">{ts("currentBadge")}</Pill> : null}
        </span>
      ),
    },
    {
      key: "ends",
      header: ts("years.colEnds"),
      cell: (row) => <Value>{formatDate(row.ends_on)}</Value>,
    },
    {
      key: "promote",
      header: t("indexColAction"),
      cell: (row) => (
        <ButtonLink
          href={`/settings/structure/years/${row.id}/promotions`}
          variant="secondary"
          size="sm"
        >
          {ty("promote")} <span className="sr-only">{ty("promoteFor", { label: row.label })}</span>
        </ButtonLink>
      ),
    },
  ];
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("titlePlain")}
        description={t("indexDescription")}
        breadcrumb={breadcrumb}
      />
      <Card title={t("indexTitle")}>
        <DataTable
          caption={t("indexTitle")}
          captionHidden
          columns={columns}
          state={rows}
          rowKey={(row) => row.id}
          emptyTitle={ts("years.emptyTitle")}
          emptyBody={ts("years.emptyBody")}
        />
      </Card>
    </div>
  );
}
