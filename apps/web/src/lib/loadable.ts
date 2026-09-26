/**
 * State of data a screen shows. Page shells start as `ready` with empty data until the BFF
 * is wired; views must handle every variant.
 */
export type Loadable<T> =
  { status: "loading" } | { status: "error" } | { status: "ready"; data: T };

export const loading = { status: "loading" } as const;
export const loadError = { status: "error" } as const;

export function ready<T>(data: T): Loadable<T> {
  return { status: "ready", data };
}
