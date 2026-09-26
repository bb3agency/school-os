import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
  badge,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-3xl space-y-1">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-ink">{title}</h1>
          {badge}
        </div>
        {description ? <p className="text-ink-muted">{description}</p> : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap gap-2" data-print="hide">
          {actions}
        </div>
      ) : null}
    </header>
  );
}
