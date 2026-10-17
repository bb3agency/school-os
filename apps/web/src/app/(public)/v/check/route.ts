import { normaliseCode } from "@/lib/verification-code";

/**
 * POST /v/check: the code box's form (no JavaScript needed). A code that can be one is
 * normalised and redirected (303) to `/v/{code}`; anything else back to `/v` with the field
 * error. Nothing is looked up or logged here.
 */
export const dynamic = "force-dynamic";

const HEADERS = {
  "Cache-Control": "private, no-store",
  "Referrer-Policy": "no-referrer",
};

export async function POST(request: Request): Promise<Response> {
  let typed: unknown = null;
  try {
    typed = (await request.formData()).get("code");
  } catch {
    typed = null;
  }
  const code = normaliseCode(typed);
  const target = code === null ? "/v?error=invalid" : `/v/${code}`;
  return new Response(null, { status: 303, headers: { ...HEADERS, Location: target } });
}
