import { cn } from "@/lib/cn";

/**
 * The illustration set (docs/17 §5.6; docs/research/2026-10-ui/skills-review.md §5): line
 * interiors of a school office, drawn as static SVG files in apps/web/public/illustrations/
 * and served from 'self' (CSP-safe, cacheable, no script). `illustrations.test.ts` lints every
 * file: no script, style or class attributes, no external references, at most 30 KB.
 *
 * Decorative by default (`alt=""`: the words beside it say the same); pass `alt` only when the
 * picture adds meaning. Decorative art is hidden in forced-colours mode (its fixed colours
 * would ignore the user's palette) and always prints.
 */
export const ILLUSTRATIONS = {
  "office-register": { width: 480, height: 360 },
  "fee-counter": { width: 480, height: 360 },
  "certificate-check": { width: 480, height: 360 },
  "timetable-board": { width: 480, height: 360 },
  "records-almirah": { width: 480, height: 360 },
} as const;

export type IllustrationName = keyof typeof ILLUSTRATIONS;

export function illustrationSrc(name: IllustrationName): string {
  return `/illustrations/${name}.svg`;
}

export function Illustration({
  name,
  alt = "",
  className,
  priority = false,
}: {
  name: IllustrationName;
  /** Leave empty for decorative art (the default). */
  alt?: string;
  className?: string;
  /** Above the fold: load eagerly. */
  priority?: boolean;
}) {
  const { width, height } = ILLUSTRATIONS[name];
  return (
    // A static SVG from 'self': next/image would add nothing (no raster to resize) and a
    // loader request per size.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={illustrationSrc(name)}
      alt={alt}
      width={width}
      height={height}
      loading={priority ? "eager" : "lazy"}
      decoding="async"
      className={cn("sos-illustration block h-auto max-w-full", className ?? "w-full")}
    />
  );
}
