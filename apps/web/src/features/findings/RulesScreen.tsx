"use client";

import { useLocale, useTranslations } from "next-intl";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { DataTable, type Column } from "@/components/ui/Table";
import { Link } from "@/i18n/navigation";
import { toLoadable } from "@/lib/bff/query";
import { attributeLabel, useAttributes, useProfiles, useRules } from "./data";
import { SeverityBadge, SourceChip } from "./parts";
import { pick, type DqProfile, type DqRule } from "./types";

/**
 * What SchoolOS checks (FR-DQ-001): the rule catalog and the export pre-check profiles, in
 * the reader's language, read-only.
 */
export function RulesScreen() {
  const t = useTranslations("findings.rules");
  const tf = useTranslations("findings");
  const tc = useTranslations("common");
  const locale = useLocale();
  const rules = useRules();
  const profiles = useProfiles();
  const attributes = useAttributes();
  const fields = (keys: readonly string[]) =>
    keys.length === 0
      ? t("fieldsFromProfile")
      : keys.map((key) => attributeLabel(attributes.data, key, locale) ?? key).join(", ");

  const ruleColumns: Column<DqRule>[] = [
    { key: "id", header: t("colId"), cell: (row) => <span className="font-mono">{row.id}</span> },
    {
      key: "what",
      header: t("colWhat"),
      className: "min-w-64",
      cell: (row) => <span lang={locale}>{pick(row.explanation, locale)}</span>,
    },
    { key: "fields", header: t("colFields"), cell: (row) => fields(row.attribute_keys) },
    {
      key: "sources",
      header: t("colSources"),
      cell: (row) => (
        <span className="flex flex-wrap gap-1">
          {row.sources.map((source) =>
            source === "canonical" ? (
              <span key={source} className="text-sm">
                {t("canonical")}
              </span>
            ) : (
              <SourceChip key={source} source={source} />
            ),
          )}
        </span>
      ),
    },
    {
      key: "severity",
      header: t("colSeverity"),
      cell: (row) =>
        row.severity.mode === "fixed" && row.severity.level ? (
          <SeverityBadge severity={row.severity.level} />
        ) : (
          <span className="text-sm">{t("dependsOnNames")}</span>
        ),
    },
    {
      key: "profile",
      header: t("colProfile"),
      cell: (row) => (row.requires_profile ? t("profileOnly") : t("always")),
    },
    {
      key: "routes",
      header: t("colRoutes"),
      className: "min-w-56",
      cell: (row) => (
        <ol className="list-decimal space-y-1 pl-4" lang={locale}>
          {row.routes.map((route) => (
            <li key={route.code}>{pick(route, locale)}</li>
          ))}
        </ol>
      ),
    },
  ];

  const profileColumns: Column<DqProfile>[] = [
    {
      key: "name",
      header: t("colProfileName"),
      cell: (row) => (
        <span>
          <span className="block font-semibold">
            {locale === "te" && row.label_te ? row.label_te : row.label_en}
          </span>
          <span className="block font-mono text-xs text-ink-muted">{row.key}</span>
        </span>
      ),
    },
    { key: "required", header: t("colRequired"), cell: (row) => fields(row.required_fields) },
    { key: "apaar", header: t("colApaar"), cell: (row) => (row.needs_apaar ? tc("yes") : tc("no")) },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Card title={t("rulesTitle")}>
        <DataTable
          caption={t("rulesTitle")}
          captionHidden
          columns={ruleColumns}
          state={toLoadable(rules)}
          rowKey={(row) => row.id}
          emptyTitle={t("empty")}
        />
      </Card>
      <Card title={t("profilesTitle")} description={t("profilesBody")}>
        <DataTable
          caption={t("profilesTitle")}
          captionHidden
          columns={profileColumns}
          state={toLoadable(profiles)}
          rowKey={(row) => row.key}
          emptyTitle={t("empty")}
        />
      </Card>
      <p>
        <Link href="/findings" className="text-primary underline">
          {tf("detail.backToList")}
        </Link>
      </p>
    </div>
  );
}
