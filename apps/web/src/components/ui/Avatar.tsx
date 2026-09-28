import { cn } from "@/lib/cn";

/** Soft fills with dark text, all ≥ 4.5:1 (same pairs as the status chips). */
const palettes = [
  "bg-info-soft text-info-ink",
  "bg-teal-soft text-teal-ink",
  "bg-violet-soft text-violet-ink",
  "bg-warning-soft text-warning-ink",
  "bg-positive-soft text-positive-ink",
  "bg-surface-sunken text-ink",
] as const;

const sizes = {
  sm: "size-7 text-[0.6875rem]",
  md: "size-9 text-xs",
  lg: "size-12 text-sm",
} as const;

export type AvatarSize = keyof typeof sizes;

/** Up to two initials from a name ("Sample Staff A" → "SA"); works for any script. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/u).filter(Boolean);
  if (words.length === 0) return "?";
  const first = [...(words[0] as string)][0] ?? "";
  const last = words.length > 1 ? ([...(words[words.length - 1] as string)][0] ?? "") : "";
  return (first + last).toUpperCase();
}

function paletteFor(name: string): string {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + (char.codePointAt(0) ?? 0)) >>> 0;
  return palettes[hash % palettes.length] as string;
}

/**
 * Initials in a coloured circle. No photos (children's data; nothing to upload or leak).
 * `decorative` hides it when the name is already written next to it.
 */
export function Avatar({
  name,
  size = "md",
  decorative = false,
  className,
}: {
  name: string;
  size?: AvatarSize;
  decorative?: boolean;
  className?: string;
}) {
  return (
    <span
      {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": name })}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full border-2 border-surface font-semibold select-none",
        paletteFor(name),
        sizes[size],
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}

/**
 * Overlapping avatars with a "+N" counter. The group is one image named by `label`
 * ("Assigned to Sample Staff A, Sample Staff B and 2 more").
 */
export function AvatarStack({
  names,
  label,
  max = 4,
  size = "sm",
  className,
}: {
  names: readonly string[];
  label: string;
  max?: number;
  size?: AvatarSize;
  className?: string;
}) {
  const shown = names.slice(0, max);
  const extra = names.length - shown.length;
  return (
    <span role="img" aria-label={label} className={cn("inline-flex items-center", className)}>
      {shown.map((name, index) => (
        <Avatar
          key={`${name}-${index}`}
          name={name}
          size={size}
          decorative
          className="-ms-2 first:ms-0"
        />
      ))}
      {extra > 0 ? (
        <span
          aria-hidden="true"
          className={cn(
            "-ms-2 inline-flex items-center justify-center rounded-full border-2 border-surface bg-action font-semibold text-on-action",
            sizes[size],
          )}
        >
          +{extra}
        </span>
      ) : null}
    </span>
  );
}
