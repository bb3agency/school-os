import type { components } from "@schoolos/api-client";
import { z } from "zod";
import { checkbox, requiredInt } from "@/lib/validation";

/**
 * School settings (FR-TEN-012): GET /tenant (any active member), PATCH /tenant
 * (`tenant.settings.manage`, step-up, If-Match). Bounds mirror the API's TenantSettingsPatch;
 * they are for quick feedback only, the API validates every value.
 */

export const SETTINGS_MANAGE = "tenant.settings.manage";
export const TENANT_KEY = ["staff", "tenant"] as const;

export type SchoolSettings = components["schemas"]["TenantSettings"];
export type SettingsPatch = components["schemas"]["TenantSettingsPatch"];

/** `W/"3"` for If-Match from the school's `version` (the API's ETag format). */
export function ifMatch(version: number): string {
  return `W/"${version}"`;
}

export const LANGUAGES = ["en", "te"] as const;
export const DATE_FORMATS = ["DD/MM/YYYY", "DD-MM-YYYY", "YYYY-MM-DD"] as const;
export const IDLE_MIN = 5;
export const IDLE_MAX = 30;
export const BUDGET_MAX = 10_000_000;

export const settingsSchema = z.object({
  languages: z
    .array(z.enum(LANGUAGES, { error: "chooseOption" }))
    .min(1, { error: "chooseOption" })
    .max(2, { error: "chooseOption" }),
  date_format: z.enum(DATE_FORMATS, { error: "chooseOption" }),
  idle_timeout_minutes: requiredInt(IDLE_MIN, IDLE_MAX),
  ai_features_enabled: checkbox,
  ai_monthly_budget_inr: requiredInt(0, BUDGET_MAX),
});

export type SettingsForm = z.output<typeof settingsSchema>;

function sameLanguages(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item) => b.includes(item));
}

/** Only the settings that differ from what was loaded ("send only the settings to change"). */
export function changedSettings(current: SchoolSettings, next: SettingsForm): SettingsPatch {
  const patch: SettingsPatch = {};
  if (!sameLanguages(current.languages ?? [], next.languages)) patch.languages = next.languages;
  if (current.date_format !== next.date_format) patch.date_format = next.date_format;
  if (current.idle_timeout_minutes !== next.idle_timeout_minutes) {
    patch.idle_timeout_minutes = next.idle_timeout_minutes;
  }
  if (current.ai_features_enabled !== next.ai_features_enabled) {
    patch.ai_features_enabled = next.ai_features_enabled;
  }
  if (current.ai_monthly_budget_inr !== next.ai_monthly_budget_inr) {
    patch.ai_monthly_budget_inr = next.ai_monthly_budget_inr;
  }
  return patch;
}
