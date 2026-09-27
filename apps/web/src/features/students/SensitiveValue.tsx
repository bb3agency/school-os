"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { aadhaarDisplay } from "./aadhaar";
import { ProblemAlert } from "./ProblemAlert";

/** Shared office PCs: a revealed value hides itself again after this long. */
export const REVEAL_SECONDS = 60;
const AADHAAR_LAST4 = "aadhaar_last4";

export interface SensitiveValueProps {
  studentId: string;
  attributeKey: string;
  /** Field name for the button's accessible name ("Show Aadhaar last 4 digits"). */
  fieldLabel: string;
  valueId?: string | undefined;
  guardianId?: string | undefined;
  /** False when the user lacks `student.read_sensitive` (or the API says not revealable). */
  canReveal: boolean;
}

/**
 * A restricted (C3) value shown as •••• until the user asks for it (US-301 AC3). "Show" calls
 * POST /students/{id}/sensitive-reveal, which the API audits; the answer is kept only in this
 * component's state (never in the query cache or browser storage) and hidden again after
 * REVEAL_SECONDS, on "Hide", or when the page changes.
 */
export function SensitiveValue({
  studentId,
  attributeKey,
  fieldLabel,
  valueId,
  guardianId,
  canReveal,
}: SensitiveValueProps) {
  const t = useTranslations("students.sensitive");
  const api = useBffClient("staff");
  const [shown, setShown] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function hide() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setShown(null);
  }

  async function reveal() {
    setPending(true);
    setError(undefined);
    try {
      const result = await unwrap(
        api.POST("/api/v1/students/{student_id}/sensitive-reveal", {
          params: { path: { student_id: studentId } },
          body: {
            attribute_key: attributeKey,
            ...(valueId ? { value_id: valueId } : {}),
            ...(guardianId ? { guardian_id: guardianId } : {}),
          },
        }),
      );
      const text = result.display ?? result.value ?? "";
      // Belt and braces for invariant 4: Aadhaar is only ever shown as XXXX XXXX 1234.
      setShown(attributeKey === AADHAAR_LAST4 ? aadhaarDisplay(text) : text);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(hide, REVEAL_SECONDS * 1000);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span aria-live="polite" className="font-mono">
        {shown !== null ? (
          <>
            <span className="sr-only">{t("revealedLabel", { field: fieldLabel })} </span>
            {shown}
          </>
        ) : (
          <>
            <span aria-hidden="true">••••</span>
            <span className="sr-only">{t("hiddenLabel", { field: fieldLabel })}</span>
          </>
        )}
      </span>
      {canReveal ? (
        shown === null ? (
          <Button size="sm" variant="ghost" onClick={reveal} disabled={pending} data-print="hide">
            {pending ? t("showing") : t("show")}
            <span className="sr-only">: {fieldLabel}</span>
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={hide} data-print="hide">
            {t("hide")}
            <span className="sr-only">: {fieldLabel}</span>
          </Button>
        )
      ) : null}
      {error !== undefined ? (
        <ProblemAlert error={error} namespace="students.errors" className="w-full" />
      ) : null}
    </div>
  );
}
