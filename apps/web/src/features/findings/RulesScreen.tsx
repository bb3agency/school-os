"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge, Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { attributeLabel, useAttributes, useProfiles, useRules } from "./data";
import { SeverityBadge, SourceChip } from "./parts";
import { pick, type DqProfile, type DqRule } from "./types";

/** Loading, error (with "Try again") and empty states of one settings-style list. */
function ListState<T>({
  query,
  label,
  children,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    data: readonly T[] | undefined;
    refetch: () => unknown;
  };
  label: string;
  children: (items: readonly T[]) => ReactNode;
}) {
  const t = useTranslations("findings.rules");
  const tc = useTranslations("common");
  if (query.isPending) return <LoadingState label={tc("loading")} rows={3} />;
  if (query.isError || !query.data) {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        <p>{tc("loadErrorBody")}</p>
        <Button variant="secondary" size="sm" className="mt-3" onClick={() => void query.refetch()}>
          <Icon name="refresh" className="size-4" />
          {tc("retry")}
        </Button>
      </Alert>
    );
  }
  if (query.data.length === 0) return <EmptyState title={t("empty")} />;
  return (
    <ul aria-label={label} className="divide-y divide-border">
      {children(query.data)}
    </ul>
  );
}

/** One row: what it is on the left (label, then the details), how it applies on the right. */
function SettingRow({
  id,
  title,
  details,
  aside,
}: {
  id: string;
  title: ReactNode;
  details: ReactNode;
  aside: ReactNode;
}) {
  return (
    <li
      key={id}
      className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
    >
      <div className="min-w-0 flex-1 space-y-1.5">
        {title}
        <div className="space-y-1 text-sm text-ink-muted">{details}</div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">{aside}</div>
    </li>
  );
}

/**
 * What SchoolOS checks (FR-DQ-001): the rule catalog and the export pre-check profiles, in
 * the reader's language, read-only (nothing on this page changes a setting, so it has no
 * save button).
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

  const ruleRow = (row: DqRule) => (
    <SettingRow
      key={row.id}
      id={row.id}
      title={
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-xs text-ink-muted">{row.id}</span>
          <span className="font-semibold text-ink" lang={locale}>
            {pick(row.explanation, locale)}
          </span>
        </p>
      }
      details={
        <>
          <p>
            <span className="font-semibold">{t("colFields")}: </span>
            <span>{fields(row.attribute_keys)}</span>
          </p>
          {row.sources.length > 0 ? (
            <p className="flex flex-wrap items-center gap-1">
              <span className="font-semibold">{t("colSources")}: </span>
              {row.sources.map((source) =>
                source === "canonical" ? (
                  <span key={source}>{t("canonical")}</span>
                ) : (
                  <SourceChip key={source} source={source} />
                ),
              )}
            </p>
          ) : null}
          {row.routes.length > 0 ? (
            <div>
              <p className="font-semibold">{t("colRoutes")}:</p>
              <ol className="list-decimal space-y-0.5 ps-5" lang={locale}>
                {row.routes.map((route) => (
                  <li key={route.code}>{pick(route, locale)}</li>
                ))}
              </ol>
            </div>
          ) : null}
        </>
      }
      aside={
        <>
          <span className="sr-only">{t("colSeverity")}: </span>
          {row.severity.mode === "fixed" && row.severity.level ? (
            <SeverityBadge severity={row.severity.level} />
          ) : (
            <Badge tone="neutral">{t("dependsOnNames")}</Badge>
          )}
          <span className="sr-only">{t("colProfile")}: </span>
          <Pill variant="tag" icon={null}>
            {row.requires_profile ? t("profileOnly") : t("always")}
          </Pill>
        </>
      }
    />
  );

  const profileRow = (row: DqProfile) => (
    <SettingRow
      key={row.key}
      id={row.key}
      title={
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold text-ink">
            {locale === "te" && row.label_te ? row.label_te : row.label_en}
          </span>
          <span className="font-mono text-xs text-ink-muted">{row.key}</span>
        </p>
      }
      details={
        <p>
          <span className="font-semibold">{t("colRequired")}: </span>
          <span>{fields(row.required_fields)}</span>
        </p>
      }
      aside={
        <Badge tone={row.needs_apaar ? "info" : "neutral"}>
          {t("colApaar")}: {row.needs_apaar ? tc("yes") : tc("no")}
        </Badge>
      }
    />
  );

  return (
    <div className="space-y-5">
      <PageHeader
        breadcrumb={[{ label: tf("title"), href: "/findings" }, { label: t("title") }]}
        title={t("title")}
        description={t("description")}
        actions={
          <ButtonLink href="/findings" variant="secondary">
            {tf("detail.backToList")}
          </ButtonLink>
        }
      />
      <Card title={t("rulesTitle")}>
        <ListState query={rules} label={t("rulesTitle")}>
          {(items) => items.map(ruleRow)}
        </ListState>
      </Card>
      <Card title={t("profilesTitle")} description={t("profilesBody")}>
        <ListState query={profiles} label={t("profilesTitle")}>
          {(items) => items.map(profileRow)}
        </ListState>
      </Card>
    </div>
  );
}
