import { Icon } from "./Icon";

/** The round "×" in a dialog's header: 36px, 44px on touch screens, named by `label`. */
export function DialogCloseButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="pressable inline-flex size-9 shrink-0 items-center pointer-coarse:size-11 justify-center rounded-full border border-border-soft text-ink-muted hover:bg-surface-muted hover:text-ink"
    >
      <Icon name="close" className="size-5" />
    </button>
  );
}
