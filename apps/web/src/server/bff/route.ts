import "server-only";
import { requestIdFrom } from "@/lib/problem";
import { ConfigError } from "@/server/config";
import { logEvent } from "@/server/log";
import { getAuthRuntime, type AuthRuntime } from "@/server/runtime";
import { problem } from "./http";

type Handler = (request: Request, runtime: AuthRuntime) => Promise<Response>;

/**
 * Wrap a BFF handler for a Next.js route file: resolve the process-wide runtime and turn
 * configuration or Valkey outages into a plain 503 problem (no internals leaked).
 */
export function withRuntime(handler: Handler): (request: Request) => Promise<Response> {
  return async (request) => {
    const requestId = requestIdFrom(request.headers);
    let runtime: AuthRuntime;
    try {
      runtime = await getAuthRuntime();
    } catch (error) {
      logEvent(
        error instanceof ConfigError ? "bff_config_invalid" : "bff_runtime_unavailable",
        { request_id: requestId },
        "error",
      );
      return problem(requestId, 503, "service_unavailable", "Sign-in is not available right now", {
        detail: "Try again in a few minutes. If it keeps happening, contact support.",
      });
    }
    try {
      return await handler(request, runtime);
    } catch {
      logEvent("bff_handler_error", { request_id: requestId, method: request.method }, "error");
      return problem(requestId, 500, "internal_error", "Something went wrong", {
        detail: "Try again. If it keeps happening, contact support.",
      });
    }
  };
}
