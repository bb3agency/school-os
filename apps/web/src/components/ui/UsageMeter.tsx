import { useId, type ReactNode } from "react";

/**
 * Usage against a plan limit using the native <meter> (no inline styles, CSP-safe).
 * The text next to it carries the numbers, so colour is not the only signal.
 */
export function UsageMeter({
  label,
  used,
  limit,
  valueText,
}: {
  label: ReactNode;
  used: number;
  limit: number | null;
  valueText: string;
}) {
  const labelId = useId();
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        <span id={labelId} className="font-semibold">
          {label}
        </span>
        <span className="text-ink-muted tabular-nums">{valueText}</span>
      </div>
      {limit !== null && limit > 0 ? (
        <meter
          aria-labelledby={labelId}
          min={0}
          max={limit}
          low={limit * 0.8}
          high={limit}
          optimum={0}
          value={Math.min(used, limit)}
        >
          {valueText}
        </meter>
      ) : null}
    </div>
  );
}
