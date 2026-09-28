import type { TenantStatus } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Badge, Pill, type PillVariant } from "@/components/ui/Badge";
import { Value } from "@/components/ui/Value";
import { formatDateTime } from "@/lib/format";

/**
 * Platform status pills (docs/17 §5: gradient pills for workflow states, soft badges for
 * record states). The text always names the state; the colour only repeats it.
 */
const SCHOOL_PILL: Record<TenantStatus, PillVariant> = {
  provisioning: "progress",
  active: "done",
  suspended: "negative",
  offboarding: "review",
  deleted: "tag",
};

export function SchoolStatusPill({ status }: { status: TenantStatus }) {
  const t = useTranslations("status.school");
  return <Pill variant={SCHOOL_PILL[status]}>{t(status)}</Pill>;
}

/** Deployment tier tag: dedicated in the platform's own colours, shared as a plain chip. */
export function TierTag({ tier }: { tier: "shared" | "dedicated" }) {
  const t = useTranslations("deploymentMode");
  return tier === "dedicated" ? (
    <Badge tone="platform">{t(tier)}</Badge>
  ) : (
    <Pill variant="tag">{t(tier)}</Pill>
  );
}

/** Timestamp in mono (fleet, audit, heartbeats); "—" when there is none. */
export function MonoTime({ value }: { value: string | null | undefined }) {
  const text = formatDateTime(value);
  return text ? (
    <time dateTime={value ?? undefined} className="font-mono text-xs whitespace-nowrap text-ink">
      {text}
    </time>
  ) : (
    <Value>{null}</Value>
  );
}

/** Money as the API sends it, in mono tabular figures (plans, invoices). */
export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-sm whitespace-nowrap tabular-nums">{children}</span>;
}
