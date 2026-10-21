"use client";

import { useTranslations } from "next-intl";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";
import { translateOr } from "@/lib/i18n-dynamic";
import type { BreachOrigin, BreachStatus, DutyClock, DutyKey } from "./types";

const STATUS: Record<BreachStatus, PillVariant> = {
  open: "review",
  contained: "progress",
  closed: "done",
};

/** A breach's state in words (colour only repeats the text, WCAG 1.4.1). */
export function BreachStatusPill({ status }: { status: BreachStatus }) {
  const t = useTranslations("breaches.status");
  return <Pill variant={STATUS[status]}>{t(status)}</Pill>;
}

/** Where the record came from: "From SchoolOS" (a processor notice) or "Our school". */
export function OriginPill({ origin }: { origin: BreachOrigin }) {
  const t = useTranslations("breaches.origin");
  return origin === "processor_notice" ? (
    <Pill variant="sample" size="sm" icon={null}>
      {t("processor_notice")}
    </Pill>
  ) : (
    <Pill variant="tag" size="sm" icon={null}>
      {t("school")}
    </Pill>
  );
}

/** Category codes in words, joined ("Contact details, Attendance"); unknown codes as "Other". */
export function useCategoryNames(): (codes: readonly string[]) => string {
  const t = useTranslations("breaches.categories");
  return (codes) => codes.map((code) => translateOr(t, code, "other")).join(", ");
}

/**
 * A duty's clock as one line of text: "Board report due in 41 h", "Board notice overdue by
 * 6 h", "Families told: 12/10/2026 14:05", "Families told: as soon as possible".
 */
export function useDutyClockText(): (key: DutyKey, clock: DutyClock) => string {
  const t = useTranslations("breaches.clock");
  const td = useTranslations("breaches.duties");
  return (key, clock) => {
    const duty = td(`${key}.name`);
    switch (clock.state) {
      case "done":
        return t("done", { duty, when: formatDateTime(clock.doneAt) ?? "" });
      case "noClock":
        return t("noClock", { duty });
      case "dueSoon":
        return t("dueSoon", { duty });
      case "due":
        return t("dueIn", { duty, hours: clock.hours });
      case "overdue":
        return t("overdueBy", { duty, hours: clock.hours });
    }
  };
}

/** The clock as a pill: overdue in the danger tint, done in the done tint (always with words). */
export function DutyClockPill({ duty, clock }: { duty: DutyKey; clock: DutyClock }) {
  const text = useDutyClockText()(duty, clock);
  const variant: PillVariant =
    clock.state === "overdue"
      ? "negative"
      : clock.state === "done"
        ? "done"
        : clock.state === "noClock"
          ? "tag"
          : "review";
  return (
    <Pill variant={variant} size="sm" {...(clock.state === "noClock" ? { icon: null } : {})}>
      <span className="num">{text}</span>
    </Pill>
  );
}
