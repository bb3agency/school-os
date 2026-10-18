import { documentRedirect } from "@/server/public/actions";
import { getAuthRuntime } from "@/server/runtime";

/**
 * GET /p/document: the PDF of the cookie's notice or receipt link (R2-3H; FR-PUB-009). 303 to
 * the presigned file (at most 5 minutes), or back to /p/view saying it is not ready.
 */
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return documentRedirect(await getAuthRuntime(), request);
}
