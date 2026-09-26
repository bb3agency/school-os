/**
 * Runs once when the Next.js server starts. Refuses to start with an invalid BFF
 * configuration (e.g. SESSION_SECRET shorter than 32 bytes, plain http outside localhost,
 * dev-only placeholder secrets on https) instead of failing on the first sign-in.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { loadAuthConfig } = await import("@/server/config");
  loadAuthConfig();
}
