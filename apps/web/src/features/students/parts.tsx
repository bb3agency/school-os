"use client";

import { useLocale, useTranslations } from "next-intl";
import { useMemo } from "react";
import { Alert } from "@/components/ui/Alert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { LoadingState } from "@/components/ui/LoadingState";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { aadhaarDisplay } from "./aadhaar";
import {
  isStudentStatus,
  isValueSource,
  type Attribute,
  type SourceValue,
  type StudentStatus,
} from "./types";

type Loose = ((key: string) => string) & { has: (key: string) => boolean };

/* ------------------------------------------------------------------ status badges */

export const studentStatusTone: Record<StudentStatus, BadgeTone> = {
  provisional: "warning",
  active: "success",
  left: "neutral",
  graduated: "info",
};

export function StudentStatusBadge({ status }: { status: string }) {
  const t = useTranslations("students.status");
  if (!isStudentStatus(status)) return <Badge>{status}</Badge>;
  return <Badge tone={studentStatusTone[status]}>{t(status)}</Badge>;
}

/* ------------------------------------------------------------------ source chips */

function CheckIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth={3}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m5 12.5 4.5 4.5L19 7.5" />
    </svg>
  );
}

/**
 * Where a value came from (PRD §8: "Register · Aadhaar · UDISE+ · Board"), with a tick when it
 * is verified. The tick is never the only signal: screen readers hear "verified".
 */
export function SourceChip({
  source,
  verification,
}: {
  source: string;
  verification?: SourceValue["verification_status"] | "provisional" | undefined;
}) {
  const t = useTranslations("students");
  const label = isValueSource(source) ? t(`sourceShort.${source}`) : source;
  const full = isValueSource(source) ? t(`sources.${source}`) : source;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        verification === "verified"
          ? "border-positive-border bg-positive-soft text-positive-ink"
          : verification === "rejected"
            ? "border-danger/30 bg-danger-soft text-danger line-through"
            : "border-border-soft bg-surface-muted text-ink",
      )}
    >
      <span aria-hidden="true">{label}</span>
      <span className="sr-only">{full}</span>
      {verification === "verified" ? <CheckIcon /> : null}
      {verification ? (
        <span className="sr-only">
          {", "}
          {t(`verification.${verification}`)}
        </span>
      ) : null}
    </span>
  );
}

/* ------------------------------------------------------------------ attributes */

export function attributeLabel(attribute: Attribute | undefined, key: string, locale: string) {
  if (!attribute) return key;
  return locale === "te" && attribute.label_te ? attribute.label_te : attribute.label_en;
}

/** GET /attributes: labels (en/te), classification, identity flag, allowed sources and values. */
export function useAttributes(enabled = true): Loadable<readonly Attribute[]> {
  const api = useBffClient("staff");
  return useApiQuery(["staff", "attributes"], () => unwrap(api.GET("/api/v1/attributes")), {
    enabled,
  });
}

export function useAttributeIndex(attributes: Loadable<readonly Attribute[]>) {
  const locale = useLocale();
  return useMemo(() => {
    const list = attributes.status === "ready" ? attributes.data : [];
    const byKey = new Map(list.map((item) => [item.key, item] as const));
    return {
      byKey,
      sorted: [...list].sort((a, b) => a.sort_order - b.sort_order),
      label: (key: string) => attributeLabel(byKey.get(key), key, locale),
    };
  }, [attributes, locale]);
}

/** A stored value for display: ISO dates as DD/MM/YYYY, known codes (gender…) translated. */
export function useValueFormatter() {
  const t = useTranslations("students.enumValues") as unknown as Loose;
  return (value: string | null | undefined, attribute: Attribute | undefined): string | null => {
    if (value === null || value === undefined || value === "") return null;
    // Aadhaar is only ever shown as XXXX XXXX 1234 (ADR-0007, invariant 4).
    if (attribute?.data_type === "digits4") return aadhaarDisplay(value);
    if (attribute?.data_type === "date") return formatDate(value) ?? value;
    if (attribute?.data_type === "enum" && t.has(value)) return t(value);
    return value;
  };
}

/* ------------------------------------------------------------------ load states */

/** Loading/error/unavailable for one resource; renders nothing when it is ready. */
export function LoadGate<T>({ state }: { state: Loadable<T> }) {
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (state.status === "loading") return <LoadingState label={tc("loading")} rows={6} />;
  if (state.status === "error") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {state.reason ? te(`load.${state.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  }
  if (state.status === "unavailable") {
    return (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  }
  return null;
}
