/**
 * Runs once per Next.js server process (for the standalone server: before the first
 * request is handled) and checks the BFF configuration (see `instrumentation-node.ts`).
 *
 * Next.js compiles this file for both runtimes and replaces `process.env.NEXT_RUNTIME` at
 * build time, so the Node-only module is imported inside this block (the documented
 * pattern): the Edge build drops it and never sees Node APIs such as `process.exit`.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { checkServerConfig } = await import("./instrumentation-node");
    checkServerConfig();
  }
}
