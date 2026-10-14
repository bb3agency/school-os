"use client";

import type { components } from "@schoolos/api-client";
import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { SelectField } from "@/components/ui/Select";
import { downloadSheet } from "@/features/sheets/download";
import { ProblemAlert } from "@/features/students/ProblemAlert";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import type { ImportBatch } from "./types";

/**
 * Import template library (US-403, FR-IMP-030..032, ADR-0041): packaged starting files with a
 * header-only Excel template each, and presets that fill in the column mapping. Applying a
 * preset only pre-selects fields in the mapping form; nothing is saved until the clerk checks
 * the rows (PUT /imports/{id}/mapping, audited there).
 */

export type ImportPreset = components["schemas"]["PresetOut"];
export type PresetMapping = components["schemas"]["PresetMappingOut"];

export const PRESETS_KEY = ["staff", "imports", "presets"] as const;
/** The preset the "Refresh from your ERP export" path starts with (FR-IMP-033). */
export const ERP_PRESET = "erp-student-export";
export const PRESET_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function usePresets() {
  const api = useBffClient("staff");
  return useApiQuery(PRESETS_KEY, () => unwrap(api.GET("/api/v1/import-presets")));
}

function UnverifiedBadge({ verified }: { verified: boolean }) {
  const t = useTranslations("imports.library");
  return verified ? (
    <Badge tone="success">{t("verified")}</Badge>
  ) : (
    <Badge tone="warning">{t("unverified")}</Badge>
  );
}

/** The library on the imports page: one row per preset with its Excel template. */
export function TemplateLibrary() {
  const t = useTranslations("imports.library");
  const ts = useTranslations("students.sources");
  const locale = useLocale();
  const presets = usePresets();
  const [downloading, setDownloading] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(undefined);
  const [saved, setSaved] = useState<string | null>(null);

  if (presets.status !== "ready") return null;

  async function download(preset: ImportPreset) {
    setFailure(undefined);
    setSaved(null);
    setDownloading(preset.key);
    try {
      const name = await downloadSheet({
        path: `/api/v1/import-presets/template?preset=${encodeURIComponent(preset.key)}`,
        method: "GET",
        locale,
        fallbackName: `schoolos-${preset.key}.xlsx`,
      });
      setSaved(name);
    } catch (error) {
      setFailure(error);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <Card title={t("title")} description={t("description")}>
      <div className="space-y-4">
        <ul className="divide-y divide-border" aria-label={t("title")}>
          {presets.data.map((preset) => (
            <li
              key={preset.key}
              className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0"
            >
              <div className="min-w-0 max-w-2xl space-y-1">
                <p className="flex flex-wrap items-center gap-2 font-semibold text-ink">
                  {preset.label_en}
                  <UnverifiedBadge verified={preset.verified} />
                </p>
                <p className="text-sm text-ink-muted">{preset.description_en}</p>
                <p className="text-sm text-ink-muted">
                  {t("recordedAs", { source: ts(preset.import_source) })}
                </p>
              </div>
              {preset.template ? (
                <Button
                  variant="secondary"
                  onClick={() => void download(preset)}
                  disabled={downloading !== null}
                  aria-label={t("downloadFor", { name: preset.label_en })}
                >
                  <Icon name="arrowDown" className="size-4" />
                  {downloading === preset.key ? t("downloading") : t("download")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="text-sm text-ink-muted">{t("unverifiedNote")}</p>
        <ProblemAlert error={failure} namespace="imports.errors" />
        {saved ? (
          <Alert tone="success" live>
            {t("downloaded", { name: saved })}
          </Alert>
        ) : null}
      </div>
    </Card>
  );
}

export interface PresetResult {
  /** column index -> target for the columns the preset knows */
  targets: Record<number, string>;
  matched: number;
  sourceMatches: boolean;
  missing: readonly string[];
}

/** The mapping step's preset picker: GET /imports/{id}/preset-mapping, then pre-select. */
export function PresetPicker({
  batch,
  initialPreset,
  disabled,
  onApply,
}: {
  batch: ImportBatch;
  initialPreset?: string | undefined;
  disabled?: boolean;
  onApply: (result: PresetResult) => void;
}) {
  const t = useTranslations("imports.presets");
  const api = useBffClient("staff");
  const presets = usePresets();
  const statusId = useId();
  const [choice, setChoice] = useState(
    initialPreset && PRESET_PATTERN.test(initialPreset) ? initialPreset : "",
  );
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(undefined);
  const [applied, setApplied] = useState<PresetResult | null>(null);

  if (presets.status !== "ready" || presets.data.length === 0) return null;
  const known = presets.data.some((preset) => preset.key === choice);

  async function apply() {
    if (!known) return;
    setPending(true);
    setFailure(undefined);
    try {
      const result = await unwrap(
        api.GET("/api/v1/imports/{import_id}/preset-mapping", {
          params: { path: { import_id: batch.id }, query: { preset: choice } },
        }),
      );
      const targets: Record<number, string> = {};
      for (const column of result.columns) {
        if (column.target) targets[column.index] = column.target;
      }
      const outcome: PresetResult = {
        targets,
        matched: Object.keys(targets).length,
        sourceMatches: result.source_matches,
        missing: result.missing,
      };
      setApplied(outcome);
      onApply(outcome);
    } catch (error) {
      setFailure(error);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface-muted p-4">
      <div className="flex flex-wrap items-end gap-3">
        <SelectField
          name="preset"
          label={t("label")}
          hint={t("hint")}
          className="min-w-64 flex-1"
          placeholder={t("placeholder")}
          value={known ? choice : ""}
          onChange={(event) => setChoice(event.currentTarget.value)}
          disabled={disabled || pending}
          options={presets.data.map((preset) => ({
            value: preset.key,
            label: preset.verified
              ? preset.label_en
              : t("unverifiedOption", { name: preset.label_en }),
          }))}
        />
        <Button
          variant="secondary"
          onClick={() => void apply()}
          disabled={disabled || pending || !known}
          aria-describedby={statusId}
        >
          {pending ? t("applying") : t("apply")}
        </Button>
      </div>
      <div id={statusId} role="status" aria-live="polite" className="space-y-2">
        {applied ? (
          <p className="text-sm">
            {t("applied", { count: applied.matched })}
            {applied.missing.length > 0
              ? ` ${t("missing", { columns: applied.missing.join(", ") })}`
              : ""}
          </p>
        ) : null}
        {applied && !applied.sourceMatches ? (
          <Alert tone="warning">{t("otherSource")}</Alert>
        ) : null}
      </div>
      <ProblemAlert error={failure} namespace="imports.errors" />
    </div>
  );
}
