import { useTranslations } from "next-intl";
import { cn } from "@/lib/cn";
import { Icon } from "./Icon";

export interface FilterChip {
  id: string;
  /** What the filter is ("Class"). */
  label: string;
  /** Its value as shown ("7-B"). */
  value: string;
}

/**
 * The active filters of a list as removable chips, plus "Clear all" (docs/17 §4.3). Each
 * chip's remove button is named "Remove filter Class: 7-B". Renders nothing without chips.
 */
export function FilterChips({
  chips,
  onRemove,
  onClear,
  className,
}: {
  chips: readonly FilterChip[];
  onRemove: (id: string) => void;
  onClear?: () => void;
  className?: string;
}) {
  const t = useTranslations("ui.filters");
  if (chips.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <ul aria-label={t("active")} className="flex flex-wrap items-center gap-2">
        {chips.map((chip) => (
          <li
            key={chip.id}
            className="inline-flex min-h-7 items-center gap-1 rounded-full border border-info-border bg-accent-soft ps-2.5 pe-1 text-sm text-accent-strong"
          >
            <span>
              <span className="text-ink-muted">{chip.label}:</span>{" "}
              <span className="font-semibold">{chip.value}</span>
            </span>
            <button
              type="button"
              onClick={() => onRemove(chip.id)}
              aria-label={t("remove", { label: chip.label, value: chip.value })}
              className="inline-flex size-6 items-center justify-center rounded-full hover:bg-surface pointer-coarse:size-11 pointer-coarse:-my-2"
            >
              <Icon name="close" className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
      {onClear && chips.length > 1 ? (
        <button
          type="button"
          onClick={onClear}
          className="min-h-7 rounded-md px-1.5 text-sm font-semibold text-accent underline-offset-4 hover:underline pointer-coarse:min-h-11"
        >
          {t("clearAll")}
        </button>
      ) : null}
    </div>
  );
}
