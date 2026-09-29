import type { AuditEvent } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Badge";
import { cardClasses } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { SearchInput, TextField } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { Link } from "@/i18n/navigation";
import { formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import type { AuditFilters } from "./audit-filters";
import { AuditExportButton } from "./AuditExportButton";

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

/**
 * US-1001 / FR-AUD-005: audit viewer with a filter bar (a GET form: the filters live in the
 * URL and are shown again after filtering), the integrity check and CSV export.
 */
export function AuditView({
  events,
  filters = {},
}: {
  events: Loadable<readonly AuditEvent[]>;
  filters?: AuditFilters;
}) {
  const t = useTranslations("school.audit");
  const tc = useTranslations("common");

  const actor = (row: AuditEvent) => {
    const known = KNOWN_ACTORS.find((type) => type === row.actor_type);
    const label = known ? t(`actor.${known}`) : row.actor_type;
    if (!row.actor_id) return label;
    const text = `${label} · ${row.actor_id.slice(0, 8)}`;
    // The filter needs the full ID but the table shows 8 characters: link to the filter.
    const query: Record<string, string> = { actor: row.actor_id };
    if (filters.action) query.action = filters.action;
    if (filters.from) query.from = filters.from;
    if (filters.to) query.to = filters.to;
    return (
      <Link
        href={{ pathname: "/audit", query }}
        aria-label={t("filterByUser", { who: text })}
        className="underline underline-offset-2 hover:text-primary"
      >
        {text}
      </Link>
    );
  };

  const columns: Column<AuditEvent>[] = [
    {
      key: "when",
      header: t("colWhen"),
      className: "whitespace-nowrap",
      cell: (row) => (
        <span className="font-mono text-xs text-ink-muted">
          <Value>{formatDateTime(row.occurred_at)}</Value>
        </span>
      ),
    },
    { key: "who", header: t("colWho"), cell: (row) => actor(row) },
    {
      key: "action",
      header: t("colAction"),
      cell: (row) => <Pill variant="command">{row.action}</Pill>,
    },
    {
      key: "record",
      header: t("colRecord"),
      cell: (row) => (
        <span className="text-sm">
          {row.resource_type}
          {row.resource_id ? (
            <span className="block font-mono text-xs break-all text-ink-subtle">
              {row.resource_id}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "summary",
      header: t("colSummary"),
      cell: (row) => (
        <span className="text-sm break-words text-ink-muted">
          <Value>{formatSummary(row.summary)}</Value>
        </span>
      ),
    },
  ];

  const active = Boolean(filters.actor || filters.action || filters.from || filters.to);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        breadcrumb={[{ label: t("home"), href: "/" }, { label: t("title") }]}
        actions={
          <>
            <ButtonLink href="/audit/verify" variant="secondary">
              <Icon name="shieldCheck" className="size-4" />
              {t("verify")}
            </ButtonLink>
            <AuditExportButton filters={filters} />
          </>
        }
      />
      <div className={cardClasses({ padding: "sm" })}>
        {/* GET form: filters live in the URL, work without JavaScript and can be bookmarked. */}
        <form
          method="get"
          role="search"
          aria-label={t("filtersTitle")}
          className="grid items-end gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_9rem_9rem_auto]"
        >
          <SearchInput
            name="action"
            label={t("filterAction")}
            labelVisible
            placeholder={t("filterActionPlaceholder")}
            defaultValue={filters.action ?? ""}
            autoComplete="off"
          />
          <SearchInput
            name="actor"
            icon="users"
            label={t("filterUser")}
            labelVisible
            placeholder={t("filterUserPlaceholder")}
            defaultValue={filters.actor ?? ""}
            autoComplete="off"
          />
          <TextField
            name="from"
            label={t("filterFrom")}
            inputMode="numeric"
            pattern="\d{2}/\d{2}/\d{4}"
            placeholder="DD/MM/YYYY"
            defaultValue={filters.from ?? ""}
            autoComplete="off"
          />
          <TextField
            name="to"
            label={t("filterTo")}
            inputMode="numeric"
            pattern="\d{2}/\d{2}/\d{4}"
            placeholder="DD/MM/YYYY"
            defaultValue={filters.to ?? ""}
            autoComplete="off"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit">
              <Icon name="filter" className="size-4" />
              {tc("applyFilters")}
            </Button>
            {active ? (
              <ButtonLink href="/audit" variant="ghost">
                {t("clearFilters")}
              </ButtonLink>
            ) : null}
          </div>
        </form>
        <p className="mt-2 text-xs text-ink-subtle">{tc("dateHint")}</p>
      </div>
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
