/** Placeholder rows while data loads. Announced once via role="status". */
export function LoadingState({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="h-6 rounded-sm bg-surface-muted motion-safe:animate-pulse"
        />
      ))}
    </div>
  );
}
