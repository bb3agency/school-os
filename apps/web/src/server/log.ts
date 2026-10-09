import "server-only";

/**
 * Structured server log lines for the BFF (invariant 5: no PII, no tokens).
 * Callers pass a constant event name and only allowlisted fields: codes, kinds, statuses,
 * request ids. Never names, subjects, tokens, cookies or request bodies.
 */
export type LogField =
  "kind" | "code" | "status" | "request_id" | "reason" | "method" | "ip_hash" | "retry_after_s";

export function logEvent(
  event: string,
  fields: Partial<Record<LogField, string | number>> = {},
  level: "info" | "warn" | "error" = "warn",
): void {
  if (process.env.NODE_ENV === "test" && !process.env.SOS_WEB_TEST_LOGS) return;
  const line = JSON.stringify({ level, event, service: "sos-web", ...fields });
  if (level === "error") console.error(line);
  else if (level === "warn") console.warn(line);
  else console.info(line);
}
