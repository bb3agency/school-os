import type { AuditEvent } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

const KNOWN_ACTORS = ["user", "system", "operator"] as const;
const MAX_SUMMARY = 240;

/**
 * Audit summaries hold IDs, field names, counts and codes only (the API rejects personal
 * data). Show them as "key: value" pairs, as text (never HTML).
 */
export function formatSummary(summary: Readonly<Record<string, unknown>>): string {
  const text = Object.entries(summary)
    .map(([key, value]) => {
      const shown =
        value === null || ["string", "number", "boolean"].includes(typeof value)
          ? String(value)
          : Array.isArray(value) && value.every((item) => typeof item !== "object")
            ? value.join(", ")
            : JSON.stringify(value);
      return `${key}: ${shown}`;
    })
    .join("; ");
  return text.length > MAX_SUMMARY ? `${text.slice(0, MAX_SUMMARY - 1)}…` : text;
}

/** US-1001 / FR-AUD-005: audit viewer with filters, integrity check and CSV export. */
export function AuditView({ events }: { events: Loadable<readonly AuditEvent[]> }) {
  const t = useTranslations("school.audit");
  const tc = useTranslations("common");

  const actor = (row: AuditEvent) => {
    const known = KNOWN_ACTORS.find((type) => type === row.actor_type);
    const label = known ? t(`actor.${known}`) : row.actor_type;
    return row.actor_id ? `${label} · ${row.actor_id.slice(0, 8)}` : label;
  };

  const columns: Column<AuditEvent>[] = [
    {
      key: "when",
      header: t("colWhen"),
      cell: (row) => <Value>{formatDateTime(row.occurred_at)}</Value>,
    },
    { key: "who", header: t("colWho"), cell: (row) => actor(row) },
    {
      key: "action",
      header: t("colAction"),
      cell: (row) => <code className="font-mono text-xs">{row.action}</code>,
    },
    {
      key: "record",
      header: t("colRecord"),
      cell: (row) => `${row.resource_type}${row.resource_id ? ` · ${row.resource_id}` : ""}`,
    },
    {
      key: "summary",
      header: t("colSummary"),
      cell: (row) => <Value>{formatSummary(row.summary)}</Value>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <ButtonLink href="/audit/verify" variant="secondary">
              {t("verify")}
            </ButtonLink>
            <Button variant="secondary" disabled>
              {t("exportCsv")}
            </Button>
          </>
        }
      />
      <Card title={t("filtersTitle")}>
        {/* GET form: filters live in the URL, work without JavaScript and can be bookmarked. */}
        <form method="get" className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-5">
          <TextField name="actor" label={t("filterUser")} autoComplete="off" />
          <TextField name="action" label={t("filterAction")} autoComplete="off" />
          <TextField
            name="from"
            label={t("filterFrom")}
            hint={tc("dateHint")}
            inputMode="numeric"
            pattern="\d{2}/\d{2}/\d{4}"
            placeholder="DD/MM/YYYY"
            autoComplete="off"
          />
          <TextField
            name="to"
            label={t("filterTo")}
            hint={tc("dateHint")}
            inputMode="numeric"
            pattern="\d{2}/\d{2}/\d{4}"
            placeholder="DD/MM/YYYY"
            autoComplete="off"
          />
          <div>
            <Button type="submit" variant="secondary">
              {tc("applyFilters")}
            </Button>
          </div>
        </form>
      </Card>
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={events}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </div>
  );
}
