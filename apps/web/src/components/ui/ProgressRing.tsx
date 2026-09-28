import { cn } from "@/lib/cn";
import type { ChartSeries } from "./Sparkline";

const series: Record<ChartSeries, string> = {
  1: "text-chart-1",
  2: "text-chart-2",
  3: "text-chart-3",
  4: "text-chart-4",
};

const sizes = {
  sm: { box: "size-10", text: "text-[0.625rem]", stroke: 4 },
  md: { box: "size-14", text: "text-xs", stroke: 5 },
  lg: { box: "size-20", text: "text-sm", stroke: 6 },
} as const;

export type ProgressRingSize = keyof typeof sizes;

const RADIUS = 20;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/**
 * Circular progress with the percentage in the middle. Exposed as a `progressbar`
 * named by `label` with the value as text, so the ring itself is never the only signal.
 */
export function ProgressRing({
  value,
  label,
  size = "md",
  color = 3,
  showValue = true,
  className,
}: {
  /** 0–100 (clamped). */
  value: number;
  /** Accessible name, e.g. "Records checked". */
  label: string;
  size?: ProgressRingSize;
  color?: ChartSeries;
  /** Show the number inside the ring (always announced either way). */
  showValue?: boolean;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const rounded = Math.round(clamped);
  const offset = CIRCUMFERENCE * (1 - clamped / 100);
  const config = sizes[size];
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={rounded}
      aria-valuetext={`${rounded}%`}
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center",
        config.box,
        className,
      )}
    >
      <svg
        aria-hidden="true"
        focusable="false"
        viewBox="0 0 48 48"
        className="absolute inset-0 -rotate-90"
      >
        <circle
          cx="24"
          cy="24"
          r={RADIUS}
          fill="none"
          strokeWidth={config.stroke}
          className="stroke-chart-track"
        />
        <circle
          cx="24"
          cy="24"
          r={RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth={config.stroke}
          strokeLinecap="round"
          strokeDasharray={`${CIRCUMFERENCE} ${CIRCUMFERENCE}`}
          strokeDashoffset={offset}
          className={series[color]}
        />
      </svg>
      {showValue ? (
        <span
          aria-hidden="true"
          className={cn("relative font-medium text-ink tabular-nums", config.text)}
        >
          {rounded}%
        </span>
      ) : null}
    </div>
  );
}
