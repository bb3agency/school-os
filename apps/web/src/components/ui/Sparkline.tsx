import { cn } from "@/lib/cn";

export type ChartSeries = 1 | 2 | 3 | 4;

const series: Record<ChartSeries, string> = {
  1: "text-chart-1",
  2: "text-chart-2",
  3: "text-chart-3",
  4: "text-chart-4",
};

const WIDTH = 120;
const HEIGHT = 32;
const PAD = 2;

/** SVG points for `values` in a WIDTH×HEIGHT box (exported for tests). */
export function sparklinePoints(values: readonly number[]): string {
  if (values.length === 0) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = values.length > 1 ? (WIDTH - PAD * 2) / (values.length - 1) : 0;
  return values
    .map((value, index) => {
      const x = PAD + index * step;
      const y = HEIGHT - PAD - ((value - min) / span) * (HEIGHT - PAD * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * Tiny trend line from numbers, pure inline SVG (no chart library, no inline styles).
 * The drawing is hidden from assistive tech; `label` is the text alternative
 * ("Rising from 1,020 to 1,245 over six months").
 */
export function Sparkline({
  values,
  label,
  color = 1,
  area = true,
  className,
}: {
  values: readonly number[];
  label: string;
  color?: ChartSeries;
  /** Soft fill under the line. */
  area?: boolean;
  className?: string;
}) {
  const points = sparklinePoints(values);
  return (
    <span className={cn("block", series[color], className)}>
      <svg
        aria-hidden="true"
        focusable="false"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="block h-8 w-full overflow-visible"
      >
        {area && points ? (
          <polygon
            points={`${PAD},${HEIGHT} ${points} ${WIDTH - PAD},${HEIGHT}`}
            fill="currentColor"
            fillOpacity={0.12}
          />
        ) : null}
        {points ? (
          <polyline
            points={points}
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
      </svg>
      <span className="sr-only">{label}</span>
    </span>
  );
}
