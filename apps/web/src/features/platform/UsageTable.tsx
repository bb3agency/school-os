import type { UsageDaily } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { formatBytes, formatCount, formatDate, formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

/** FR-PLT-020..021: daily aggregates (counts, bytes, tokens, cost). Never personal data. */
export function UsageTable({
  usage,
  schoolName,
  caption,
}: {
  usage: Loadable<readonly UsageDaily[]>;
  schoolName?: (tenantId: string) => string;
  caption: string;
}) {
  const t = useTranslations("platform.usage");
  const locale = useLocale();
  const n = (value: number) => <Value>{formatCount(value, locale)}</Value>;
  const num = "text-right tabular-nums";
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
      className: num,
      cell: (row) => n(row.active_users),
    },
    { key: "staff", header: t("colStaffUsers"), className: num, cell: (row) => n(row.staff_users) },
    {
      key: "students",
      header: t("colStudents"),
      className: num,
      cell: (row) => n(row.students_active),
    },
    {
      key: "storage",
      header: t("colStorage"),
      className: num,
      cell: (row) => <Value>{formatBytes(row.storage_bytes, locale)}</Value>,
    },
    { key: "docs", header: t("colDocuments"), className: num, cell: (row) => n(row.documents) },
    { key: "queries", header: t("colAiQueries"), className: num, cell: (row) => n(row.ai_queries) },
    {
      key: "tokens",
      header: t("colAiTokens"),
      className: num,
      cell: (row) => n(row.ai_input_tokens + row.ai_output_tokens),
    },
    {
      key: "cost",
      header: t("colAiCost"),
      className: num,
      cell: (row) => <Value>{formatInr(row.ai_cost_inr, locale)}</Value>,
    },
  ];
  return (
    <DataTable
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
