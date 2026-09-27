/**
 * The `?after=` notice carried to the next row after a save. Only these two words are ever
 * accepted (never data): anything else is ignored. Server-safe (no "use client").
 */
export type AfterAction = "confirmed" | "rejected";

export function afterActionOf(value: string | string[] | undefined): AfterAction | undefined {
  return value === "confirmed" || value === "rejected" ? value : undefined;
}
