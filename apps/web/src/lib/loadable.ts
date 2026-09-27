/**
 * State of data a screen shows. Views must handle every variant:
 * - `error` may carry a `reason` the UI explains in plain language (no access, choose a
 *   school first, MFA needed, school suspended); otherwise a generic "couldn't load".
 * - `unavailable`: this deployment does not offer the route (405/501, e.g. the control
 *   plane on a dedicated host); screens say "not available" instead of showing an error.
 */
export type LoadErrorReason =
  "forbidden" | "active_tenant_required" | "mfa_required" | "tenant_suspended" | "not_found";

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
