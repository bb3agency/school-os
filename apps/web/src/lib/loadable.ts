/**
 * State of data a screen shows. Views must handle every variant:
 * - `unavailable`: the API does not offer this yet (404/501 while the backend is being
 *   built); screens say "not available yet" instead of showing an error.
 */
export type Loadable<T> =
  | { status: "loading" }
  | { status: "error" }
  | { status: "unavailable" }
  | { status: "ready"; data: T };

export const loading = { status: "loading" } as const;
export const loadError = { status: "error" } as const;
export const unavailable = { status: "unavailable" } as const;

export function ready<T>(data: T): Loadable<T> {
  return { status: "ready", data };
}
