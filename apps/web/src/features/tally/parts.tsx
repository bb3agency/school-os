"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { LoadingState } from "@/components/ui/LoadingState";
import type { Locale } from "@/i18n/routing";
import { formatInr } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import { balanceKind, isConnectorOff } from "./data";

/**
 * Loading, not available, "the connector is not switched on" (404 while the school's flag is
 * off, ADR-0032), an error, or the content.
 */
export function TallyGate<T>({
  data,
  children,
}: {
  data: Loadable<T>;
  children: (value: T) => ReactNode;
}) {
  const t = useTranslations("tally");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  if (data.status === "loading") return <LoadingState label={tc("loading")} />;
  if (isConnectorOff(data)) {
    return (
      <Alert tone="info" title={t("off.title")}>
        {t("off.body")}
      </Alert>
    );
  }
  if (data.status === "unavailable") {
    return (
      <Alert tone="info" title={tc("notAvailableYetTitle")}>
        {tc("notAvailableYetBody")}
      </Alert>
    );
  }
  if (data.status === "error") {
    return (
      <Alert tone="danger" title={tc("loadErrorTitle")}>
        {data.reason ? te(`load.${data.reason}`) : tc("loadErrorBody")}
      </Alert>
    );
  }
  return <>{children(data.data)}</>;
}

const BALANCE_PILL: Record<"due" | "advance" | "settled", PillVariant> = {
  due: "negative",
  advance: "positive",
  settled: "tag",
};

/**
 * An amount from Tally in rupees (lakh grouping) with words for what it means: colour is never
 * the only signal (WCAG 1.4.1).
 */
export function Amount({ value, withKind = false }: { value: string; withKind?: boolean }) {
  const t = useTranslations("tally.balance");
  const locale = useLocale() as Locale;
  const kind = balanceKind(value);
  const shown = formatInr(value, locale) ?? value;
  return (
    <span className="inline-flex flex-wrap items-center gap-2 tabular-nums">
      <span>{shown}</span>
      {withKind ? <Pill variant={BALANCE_PILL[kind]}>{t(kind)}</Pill> : null}
    </span>
  );
}

/** A fact of the connector status: label and value in a description list. */
export function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="mt-0.5 break-words text-ink">{children}</dd>
    </div>
  );
}
