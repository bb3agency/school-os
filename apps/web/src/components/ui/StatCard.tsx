import { useId, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { DeltaPill, type DeltaDirection } from "./Badge";
import { cardClasses } from "./Card";
import { TickValue } from "./TickValue";

function Unavailable({ label }: { label: string }) {
  return (
    <>
      <span aria-hidden="true">—</span>
      <span className="sr-only">{label}</span>
    </>
  );
}

/**
 * A single number with its label, inside a `<dl>`. When the value is unavailable it shows
 * "—" visually and `unavailableLabel` ("Not available") to screen readers.
 */
export function StatCard({
  label,
  value,
  unavailableLabel,
  hint,
}: {
  label: ReactNode;
  value: string | null;
  unavailableLabel: string;
  hint?: ReactNode;
}) {
  return (
    <div className={cardClasses({ padding: "sm" })}>
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="mt-1 font-display text-4xl leading-tight text-ink tabular-nums">
        <TickValue value={value}>{value ?? <Unavailable label={unavailableLabel} />}</TickValue>
      </dd>
      {hint ? <dd className="mt-1 text-xs text-ink-subtle">{hint}</dd> : null}
    </div>
  );
}

export interface KpiDelta {
  /** As shown: "+2.7%". */
  value: string;
  direction?: DeltaDirection;
  /** Read instead of `value`: "up 2.7% on last month". */
  label?: string;
}

export interface KpiCardProps {
  /** What the number is ("Students on roll"). */
  label: ReactNode;
  /** Pre-formatted number ("1,245", "86%"), or null when not available. */
  value: string | null;
  unavailableLabel: string;
  delta?: KpiDelta;
  /** Muted comparison line ("1,157 last month"). */
  comparison?: ReactNode;
  /** Optional `<Sparkline>` (it carries its own text alternative). */
  sparkline?: ReactNode;
  /** Optional icon or action in the top-right corner. */
  aside?: ReactNode;
  className?: string;
}

/**
 * KPI tile: label, big serif number, dark delta pill, comparison line and an optional
 * sparkline. A `role="group"` named by the label, so the number is read with its label.
 */
export function KpiCard({
  label,
  value,
  unavailableLabel,
  delta,
  comparison,
  sparkline,
  aside,
  className,
}: KpiCardProps) {
  const labelId = useId();
  return (
    <div
      role="group"
      aria-labelledby={labelId}
      className={cn(cardClasses({ padding: "md" }), "flex flex-col gap-3", className)}
    >
      <div className="flex items-start justify-between gap-3">
        <p id={labelId} className="text-sm text-ink-muted">
          {label}
        </p>
        {aside}
      </div>
      <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
        <p className="font-display text-5xl leading-none text-ink tabular-nums">
          <TickValue value={value}>{value ?? <Unavailable label={unavailableLabel} />}</TickValue>
        </p>
        {delta && value !== null ? (
          <DeltaPill
            value={delta.value}
            direction={delta.direction ?? "flat"}
            {...(delta.label ? { label: delta.label } : {})}
            className="mb-1"
          />
        ) : null}
      </div>
      {comparison ? <p className="text-sm text-ink-subtle">{comparison}</p> : null}
      {sparkline ? <div className="mt-auto">{sparkline}</div> : null}
    </div>
  );
}
