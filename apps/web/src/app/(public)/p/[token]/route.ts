import { logEvent } from "@/server/log";
import { exchangeParentLink, UNAVAILABLE_TEXT } from "@/server/public/exchange";
import { getAuthRuntime } from "@/server/runtime";

/**
 * GET /p/{token}: a parent link from a WhatsApp message (ADR-0046 decision 6, FR-PUB-010).
 * Exchanges the token for the parent cookie and redirects (303) to /p/view, or to the uniform
 * expired page; see src/server/public/exchange.ts. The token is in the path (never a query
 * string) and is in no log line.
 */
export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ token: string }> },
): Promise<Response> {
  const { token } = await params;
  try {
    return await exchangeParentLink(request, await getAuthRuntime(), token);
  } catch {
    logEvent("public_exchange_unavailable", {}, "error");
    return new Response(UNAVAILABLE_TEXT, {
      status: 503,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "Retry-After": "60",
      },
    });
  }
}
