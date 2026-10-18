import type { ReactNode } from "react";
import type { UsageDaily } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { DataTableV2 } from "@/components/console/DataTableV2";
import type { Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { formatBytes, formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/** FR-PLT-020..021: daily aggregates (counts, bytes, tokens, cost). Never personal data. */
export function UsageTable({
  usage,
  schoolName,
  caption,
  toolbar,
}: {
  usage: Loadable<readonly UsageDaily[]>;
  schoolName?: (tenantId: string) => string;
  caption: string;
  /** Filters above the rows, inside the table card (docs/17 §4.3). */
  toolbar?: ReactNode;
}) {
  const t = useTranslations("platform.usage");
  const locale = useLocale();
  const n = (value: number) => <Value>{formatCount(value, locale)}</Value>;
  const columns: Column<UsageDaily>[] = [
    ...(schoolName
      ? [
          {
            key: "school",
            header: t("colSchool"),
            cell: (row: UsageDaily) => schoolName(row.tenant_id),
          },
        ]
      : []),
    {
      key: "date",
      header: t("colDate"),
      cell: (row) => <Value>{formatDate(row.usage_date)}</Value>,
    },
    {
      key: "users",
      header: t("colActiveUsers"),
      numeric: true,
      cell: (row) => n(row.active_users),
    },
    { key: "staff", header: t("colStaffUsers"), numeric: true, cell: (row) => n(row.staff_users) },
    {
      key: "students",
      header: t("colStudents"),
      numeric: true,
      cell: (row) => n(row.students_active),
    },
    {
      key: "storage",
      header: t("colStorage"),
      numeric: true,
      cell: (row) => <Value>{formatBytes(row.storage_bytes, locale)}</Value>,
    },
    { key: "docs", header: t("colDocuments"), numeric: true, cell: (row) => n(row.documents) },
    { key: "queries", header: t("colAiQueries"), numeric: true, cell: (row) => n(row.ai_queries) },
    {
      key: "tokens",
      header: t("colAiTokens"),
      numeric: true,
      cell: (row) => n(row.ai_input_tokens + row.ai_output_tokens),
    },
    {
      key: "cost",
      header: t("colAiCost"),
      numeric: true,
      cell: (row) => <Value>{formatInr(row.ai_cost_inr, locale)}</Value>,
    },
  ];
  return (
    <DataTableV2
      id="platform.usage"
      toolbar={toolbar}
      caption={caption}
      captionHidden
      columns={columns}
      state={usage}
      rowKey={(row) => `${row.tenant_id}:${row.usage_date}:${row.source}`}
      emptyTitle={t("emptyTitle")}
      emptyBody={t("emptyBody")}
    />
  );
}
