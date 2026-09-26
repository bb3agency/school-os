import { problemResponse, requestIdFrom } from "@/lib/problem";

/**
 * BFF proxy stub (docs/09 §1): browser → /bff/api/v1/* → API /api/v1/*.
 *
 * Not wired yet. The later auth task adds: session lookup (__Host-sos_session), CSRF
 * check on state-changing methods, user access token + short-lived X-Service-Token
 * (SEC-004), and forwarding to API_INTERNAL_URL. Until then every call gets 501.
 */
export const dynamic = "force-dynamic";

function notImplemented(request: Request): Response {
  const requestId = requestIdFrom(request.headers);
  return problemResponse(
    {
      type: "https://docs.schoolos.example/errors/not-implemented",
      title: "Not implemented",
      status: 501,
      code: "not_implemented",
      detail: "The web BFF does not forward API requests yet.",
      instance: new URL(request.url).pathname,
    },
    requestId,
  );
}

export const GET = notImplemented;
export const POST = notImplemented;
export const PUT = notImplemented;
export const PATCH = notImplemented;
export const DELETE = notImplemented;
