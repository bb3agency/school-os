import type { SessionKind } from "./session-client";

/**
 * Step-up MFA without losing the page (ADR-0018: "the BFF re-authenticates with prompt=login
 * and retries"; docs/07 §5.2).
 *
 * When the API answers 428 step_up_required, the BFF client asks the registered handler
 * (StepUpHost, mounted by the school layout) to confirm the user's identity. The host opens
 * the BFF step-up route in a small window whose return address is `/<locale>/step-up-complete`;
 * that page announces completion on a BroadcastChannel (same origin only). The client then
 * retries the original request once, with a fresh CSRF token. Nothing is written to browser
 * storage: the pending request lives only in memory in this tab. Without a handler (or when the
 * window cannot be opened) the whole page goes to the step-up route instead, as before.
 */

/** BroadcastChannel name shared by the step-up window and the page that opened it. */
export const STEP_UP_CHANNEL = "sos-step-up";

/** Message the step-up window posts when sign-in is complete. Carries no data. */
export const STEP_UP_COMPLETE = "step-up-complete";

/**
 * Resolves true when the user confirmed their identity (retry the request), false when they
 * cancelled. `fallbackUrl` is the full-page step-up address for "continue in this tab".
 */
export type StepUpHandler = (fallbackUrl: string) => Promise<boolean>;

const handlers = new Map<SessionKind, StepUpHandler>();

/** Register the page's step-up handler; returns the unregister function. */
export function registerStepUpHandler(kind: SessionKind, handler: StepUpHandler): () => void {
  handlers.set(kind, handler);
  return () => {
    if (handlers.get(kind) === handler) handlers.delete(kind);
  };
}

export function stepUpHandler(kind: SessionKind): StepUpHandler | undefined {
  return handlers.get(kind);
}

/** The user closed the "confirm it's you" prompt: the action was not done. */
export class StepUpCancelledError extends Error {
  constructor() {
    super("step-up cancelled");
    this.name = "StepUpCancelledError";
  }
}

/**
 * Staff step-up route that returns to the completion page (opened in its own window). The
 * platform admin panel registers no handler and keeps the full-page step-up.
 */
export function stepUpWindowUrl(locale: string): string {
  return `/bff/auth/step-up?next=${encodeURIComponent(`/${locale}/step-up-complete`)}`;
}
