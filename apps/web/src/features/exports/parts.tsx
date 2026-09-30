"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, useState } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { useSectionOptions, type SectionOption } from "@/features/findings/data";
import { useTeluguEnabled } from "@/i18n/LanguagesProvider";
import { uuid } from "@/lib/validation";
import { profileName } from "./data";
import {
  EXPORT_LANGUAGES,
  MAX_CLASSES,
  MAX_SECTIONS,
  type Export,
  type ExportLanguage,
  type ExportProfile,
  type ExportScope,
  type ExportStatus,
} from "./types";

/** Preparing = in progress, ready = done, failed = negative; deleted files are a plain tag. */
const exportPill: Record<ExportStatus, PillVariant> = {
  queued: "progress",
  running: "progress",
  ready: "done",
  failed: "negative",
  expired: "tag",
};

export function ExportStatusBadge({ status }: { status: ExportStatus }) {
  const t = useTranslations("exports.status");
  return <Pill variant={exportPill[status]}>{t(status)}</Pill>;
}

/** "Board pre-check · CISCE registration 2026" or "Student list". */
export function exportTitle(
  row: Pick<Export, "kind" | "profile_key">,
  profiles: readonly ExportProfile[] | undefined,
  locale: string,
  kindLabel: (kind: Export["kind"]) => string,
): string {
  const profile = profileName(profiles, row.profile_key, locale);
  return profile ? `${kindLabel(row.kind)} · ${profile}` : kindLabel(row.kind);
}

/** Who asked for the export: "You", their display name, or "A former staff member". */
export function RequesterName({ row }: { row: Pick<Export, "own" | "requested_by"> }) {
  const t = useTranslations("exports");
  if (row.own) return <>{t("you")}</>;
  return <>{row.requested_by.display_name ?? t("formerStaff")}</>;
}

/** Says before submitting that the server will ask the user to confirm it's them (ADR-0021). */
export function StepUpNotice() {
  const t = useTranslations("exports.new");
  return (
    <Alert tone="info" title={t("stepUpTitle")}>
      {t("stepUpBody")}
    </Alert>
  );
}

// --- scope ----------------------------------------------------------------------------------

export const SCOPE_MODES = ["all", "classes", "sections"] as const;
export type ScopeMode = (typeof SCOPE_MODES)[number];

/** zod fields for the scope picker (API: sections or classes of the current year, or none). */
export const scopeFields = {
  scope: z.enum(SCOPE_MODES, { error: "chooseOption" }),
  class_ids: z.array(uuid).max(MAX_CLASSES, { error: "tooManyClasses" }),
  section_ids: z.array(uuid).max(MAX_SECTIONS, { error: "tooManySections" }),
};

export interface ScopeValues {
  scope: ScopeMode;
  class_ids: string[];
  section_ids: string[];
}

/** Adds "choose at least one" issues for the chosen mode. */
export function refineScope(data: ScopeValues, context: z.RefinementCtx): void {
  if (data.scope === "classes" && data.class_ids.length === 0) {
    context.addIssue({ code: "custom", path: ["class_ids"], message: "chooseClasses" });
  }
  if (data.scope === "sections" && data.section_ids.length === 0) {
    context.addIssue({ code: "custom", path: ["section_ids"], message: "chooseSections" });
  }
}

/** Scope body for the API: `{}` = every student you can see. */
export function scopeBody(data: ScopeValues): ExportScope {
  if (data.scope === "classes") return { class_ids: data.class_ids };
  if (data.scope === "sections") return { section_ids: data.section_ids };
  return {};
}

interface ClassOption {
  id: string;
  label: string;
}

function classesOf(sections: readonly SectionOption[]): ClassOption[] {
  const seen = new Map<string, ClassOption>();
  for (const section of sections) {
    if (!seen.has(section.classId)) {
      seen.set(section.classId, {
        id: section.classId,
        label: section.classLabel || section.label,
      });
    }
  }
  return [...seen.values()];
}

/**
 * Which students: everyone you can see, some classes or some sections of the current academic
 * year (the API offers no other year). Scoped staff only get their own sections from the API.
 */
export function ScopePicker({ errors }: { errors: Record<string, string> }) {
  const t = useTranslations("exports.scope");
  const tc = useTranslations("common");
  const { sections, loading } = useSectionOptions();
  const [mode, setMode] = useState<ScopeMode>("all");
  const errorId = useId();
  const error = errors.scope ?? errors.class_ids ?? errors.section_ids;
  const classes = classesOf(sections);
  const byClass = new Map<string, SectionOption[]>();
  for (const section of sections) {
    byClass.set(section.classId, [...(byClass.get(section.classId) ?? []), section]);
  }
  const box = "inline-flex min-h-8 items-center gap-2 text-sm";
  return (
    <fieldset className="space-y-3" aria-describedby={error ? errorId : undefined}>
      <legend className="text-sm font-semibold text-ink">{t("legend")}</legend>
      <p className="text-sm text-ink-muted">{t("hint")}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {SCOPE_MODES.map((value, index) => (
          <label key={value} className={box}>
            <input
              type="radio"
              name="scope"
              value={value}
              checked={mode === value}
              onChange={() => setMode(value)}
              aria-invalid={(index === 0 && Boolean(errors.scope)) || undefined}
              className="size-4"
            />
            {t(`mode.${value}`)}
          </label>
        ))}
      </div>
      {mode !== "all" && loading ? <p className="text-sm">{tc("loading")}</p> : null}
      {mode !== "all" && !loading && sections.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("noStructure")}</p>
      ) : null}
      {mode === "classes" ? (
        <fieldset className="space-y-1">
          <legend className="sr-only">{t("chooseClasses")}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {classes.map((item, index) => (
              <label key={item.id} className={box}>
                <input
                  type="checkbox"
                  name="class_ids"
                  value={item.id}
                  aria-invalid={(index === 0 && Boolean(errors.class_ids)) || undefined}
                  className="size-4"
                />
                {item.label}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
      {mode === "sections" ? (
        <fieldset className="space-y-2">
          <legend className="sr-only">{t("chooseSections")}</legend>
          {[...byClass.entries()].map(([classId, list], group) => (
            <div key={classId} className="flex flex-wrap gap-x-4 gap-y-1">
              {list.map((section, index) => (
                <label key={section.id} className={box}>
                  <input
                    type="checkbox"
                    name="section_ids"
                    value={section.id}
                    aria-invalid={
                      (group === 0 && index === 0 && Boolean(errors.section_ids)) || undefined
                    }
                    className="size-4"
                  />
                  {section.label}
                </label>
              ))}
            </div>
          ))}
        </fieldset>
      ) : null}
      {error ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** "All students you can see", "3 sections: Class 9 · A, …" or "2 classes". */
export function ScopeSummary({ scope }: { scope: Export["scope"] }) {
  const t = useTranslations("exports.scope");
  const { sections } = useSectionOptions();
  const sectionIds = scope.section_ids ?? [];
  const classIds = scope.class_ids ?? [];
  if (sectionIds.length > 0) {
    const names = sectionIds
      .map((id) => sections.find((item) => item.id === id)?.label)
      .filter((label): label is string => Boolean(label));
    return (
      <>
        {t("sectionsCount", { count: sectionIds.length })}
        {names.length === sectionIds.length ? `: ${names.join(", ")}` : null}
      </>
    );
  }
  if (classIds.length > 0) {
    const names = classIds
      .map((id) => classesOf(sections).find((item) => item.id === id)?.label)
      .filter((label): label is string => Boolean(label));
    return (
      <>
        {t("classesCount", { count: classIds.length })}
        {names.length === classIds.length ? `: ${names.join(", ")}` : null}
      </>
    );
  }
  return <>{t("everyone")}</>;
}

// --- language -------------------------------------------------------------------------------

/** Language of the file's headings and labels (the data itself is as recorded). */
export function LanguageField({ errors }: { errors: Record<string, string> }) {
  const t = useTranslations("exports.new");
  const tl = useTranslations("exports.language");
  const locale = useLocale();
  const errorId = useId();
  const telugu = useTeluguEnabled();
  const initial: ExportLanguage = locale === "te" ? "te" : "en";
  // ADR-0036: while Telugu is switched off every file is in English; nothing to choose.
  if (!telugu) return <input type="hidden" name="language" value="en" />;
  return (
    <fieldset className="space-y-1" aria-describedby={errors.language ? errorId : undefined}>
      <legend className="text-sm font-semibold text-ink">{t("language")}</legend>
      <p className="text-sm text-ink-muted">{t("languageHint")}</p>
      <div className="flex flex-wrap gap-x-6">
        {EXPORT_LANGUAGES.map((value) => (
          <label key={value} className="inline-flex min-h-8 items-center gap-2 text-sm">
            <input
              type="radio"
              name="language"
              value={value}
              defaultChecked={value === initial}
              className="size-4"
            />
            {tl(value)}
          </label>
        ))}
      </div>
      {errors.language ? (
        <p id={errorId} className="text-sm font-semibold text-danger">
          {errors.language}
        </p>
      ) : null}
    </fieldset>
  );
}

/** Server field (dotted body path) → form field name of the export forms. */
export function exportFieldMap(field: string): string | undefined {
  const [head] = field.split(".");
  if (head === "scope") return "scope";
  if (head === "columns") return "columns";
  if (head === "format") return "format";
  return head;
}
