import { proxyToApi } from "@/server/bff/proxy";
import { withRuntime } from "@/server/bff/route";

/**
 * BFF proxy (docs/09 §1): browser -> /bff/api/v1/* -> API /api/v1/* with the session's
 * access token and a short-lived service token (SEC-004). See src/server/bff/proxy.ts.
 */
export const dynamic = "force-dynamic";

const handler = withRuntime(proxyToApi);

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
