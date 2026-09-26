import type { ReactNode } from "react";

/**
 * A single number with its label. When the value is unavailable it shows "—" visually and
 * "Not available" to screen readers.
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
    <div className="rounded-lg border border-border bg-surface p-4">
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="mt-1 text-2xl font-bold text-ink tabular-nums">
        {value ?? (
          <>
            <span aria-hidden="true">—</span>
            <span className="sr-only">{unavailableLabel}</span>
          </>
        )}
      </dd>
      {hint ? <dd className="mt-1 text-xs text-ink-muted">{hint}</dd> : null}
    </div>
  );
}
