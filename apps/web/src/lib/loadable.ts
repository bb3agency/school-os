/**
 * State of data a screen shows. Views must handle every variant:
 * - `error` may carry a `reason` the UI explains in plain language (no access, choose a
 *   school first, MFA needed, school suspended); otherwise a generic "couldn't load".
 * - `unavailable`: the API does not offer this yet (404/501 while the backend is being
 *   built); screens say "not available yet" instead of showing an error.
 */
export type LoadErrorReason =
  "forbidden" | "active_tenant_required" | "mfa_required" | "tenant_suspended";

export type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; reason?: LoadErrorReason }
  | { status: "unavailable" }
  | { status: "ready"; data: T };

export const loading = { status: "loading" } as const;
export const loadError = { status: "error" } as const;
export const unavailable = { status: "unavailable" } as const;

export function ready<T>(data: T): Loadable<T> {
  return { status: "ready", data };
}
