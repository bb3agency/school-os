import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { isSourceKey, type FindingStatus, type FindingValue, type Severity } from "./types";

export const severityTone: Record<Severity, BadgeTone> = {
  blocker: "danger",
  high: "warning",
  medium: "warning",
  low: "info",
  info: "neutral",
};

export const findingStatusTone: Record<FindingStatus, BadgeTone> = {
  open: "warning",
  reopened: "danger",
  resolved: "success",
  waived: "neutral",
};

/** A shape per severity, so the badge never depends on colour alone (WCAG 1.4.1). */
const severityIcon: Record<Severity, ReactNode> = {
  blocker: <path d="M8 3h8l5 5v8l-5 5H8l-5-5V8zM9 9l6 6m0-6-6 6" />,
  high: <path d="M12 3 2 20h20zM12 10v4m0 3h.01" />,
  medium: <path d="M12 3 2 20h20zM12 10v4m0 3h.01" />,
  low: <path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 5h.01M11 12h1v5h1" />,
  info: <path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 5h.01M11 12h1v5h1" />,
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const t = useTranslations("findings.severity");
  return (
    <Badge tone={severityTone[severity]} className="gap-1">
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="size-3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {severityIcon[severity]}
      </svg>
      {t(severity)}
    </Badge>
  );
}

export function FindingStatusBadge({ status }: { status: FindingStatus }) {
  const t = useTranslations("findings.status");
  return <Badge tone={findingStatusTone[status]}>{t(status)}</Badge>;
}

/** Source chip (Register · Aadhaar · UDISE+ · Board …), PRD §8. */
export function SourceChip({ source }: { source: string }) {
  const t = useTranslations("findings.sources");
  return (
    <span className="inline-flex items-center rounded-full border border-border-soft bg-surface-muted px-2 py-0.5 text-xs font-medium whitespace-nowrap text-ink">
      {isSourceKey(source) ? t(source) : source}
    </span>
  );
}

/**
 * The conflicting values with their sources. The API sends `masked` always and `value` only
 * for current non-sensitive (C2) values; sensitive (C3) values stay masked.
 */
export function FindingValues({ values }: { values: readonly FindingValue[] }) {
  const t = useTranslations("findings");
  if (values.length === 0) return <span className="text-ink-muted">{t("noValues")}</span>;
  return (
    <ul className="space-y-1.5">
      {values.map((item) => (
        <li key={item.value_id} className="flex flex-wrap items-center gap-2">
          <SourceChip source={item.source} />
          <span className="font-mono text-sm break-words">
            {item.value ?? item.masked ?? t("noValue")}
            {item.sensitive || item.value === null ? (
              <span className="sr-only"> ({t("maskedNote")})</span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}
