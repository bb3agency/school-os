import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type AlertTone = "info" | "success" | "warning" | "danger";

const tones: Record<AlertTone, string> = {
  info: "border-info-ink/40 bg-info-soft text-info-ink",
  success: "border-success-ink/40 bg-success-soft text-success-ink",
  warning: "border-warning-border bg-warning-soft text-warning-ink",
  danger: "border-danger bg-danger-soft text-danger",
};

const icons: Record<AlertTone, ReactNode> = {
  info: <path d="M12 8h.01M11 12h1v5h1" />,
  success: <path d="m8 12.5 2.5 2.5L16 9.5" />,
  warning: <path d="M12 8v5m0 3h.01" />,
  danger: <path d="M12 8v5m0 3h.01" />,
};

export interface AlertProps {
  tone?: AlertTone;
  title?: ReactNode;
  children?: ReactNode;
  className?: string | undefined;
  /**
   * Announce to screen readers when the alert appears after an action
   * (e.g. a form result). Leave false for alerts present on page load.
   */
  live?: boolean;
}

export function Alert({ tone = "info", title, children, className, live = false }: AlertProps) {
  const role = live ? (tone === "danger" ? "alert" : "status") : undefined;
  return (
    <div
      role={role}
      className={cn("flex gap-3 rounded-md border-l-4 border p-4", tones[tone], className)}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="mt-0.5 size-5 shrink-0"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="12" cy="12" r="9" />
        {icons[tone]}
      </svg>
      <div className="space-y-1 text-sm">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div>{children}</div> : null}
      </div>
    </div>
  );
}
