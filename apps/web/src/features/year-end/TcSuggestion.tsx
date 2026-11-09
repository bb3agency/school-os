"use client";

import type { components } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/Button";

/**
 * FT-08 (FR-YRE-014..FR-YRE-016): a "From SchoolOS" value for one TC input (qualified for
 * promotion, working days, days attended), shown under the input with a "Use" button. The
 * value goes into the input only when the person clicks; they may change it afterwards, and
 * the certificate keeps what the form sends.
 */
export type TcInputSuggestion = components["schemas"]["TcInputSuggestion"];

/** The "From SchoolOS" values on a certificate preview (none without a preview). */
export function suggestionsOf(
  preview: Pick<components["schemas"]["CertificatePreview"], "input_suggestions"> | null,
): TcInputSuggestion[] {
  return preview?.input_suggestions ?? [];
}

/** Put ``value`` into the form control ``id`` as if typed, so the form sees the change. */
export function fillControl(id: string, value: string): void {
  const element = document.getElementById(id);
  if (!(element instanceof HTMLInputElement) && !(element instanceof HTMLSelectElement)) return;
  element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.focus();
}

export function TcSuggestionHint({
  suggestion,
  controlId,
  fieldLabel,
  valueLabel,
}: {
  suggestion: TcInputSuggestion | undefined;
  controlId: string;
  fieldLabel: string;
  /** The value as the person reads it (a choice's label). */
  valueLabel: string;
}) {
  const t = useTranslations("yearEnd.tc");
  if (!suggestion) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
      <span>
        {t("fromSchoolOs", { value: valueLabel })} ({t(`source.${suggestion.source}`)})
      </span>
      <Button
        variant="ghost"
        size="sm"
        aria-label={t("useLabel", { field: fieldLabel })}
        onClick={() => fillControl(controlId, suggestion.value)}
      >
        {t("use")}
      </Button>
    </p>
  );
}
