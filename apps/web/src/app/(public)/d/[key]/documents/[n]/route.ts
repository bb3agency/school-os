import { disclosureDocumentRedirect } from "@/server/public/disclosure";
import { getAuthRuntime } from "@/server/runtime";

/**
 * GET /d/{key}/documents/{n}: a document of the published disclosure (ADR-0051; FR-DISC-009).
 * 303 to the presigned PDF (at most 5 minutes), or back to the page for anything else.
 */
export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ key: string; n: string }> },
): Promise<Response> {
  const { key, n } = await params;
  return disclosureDocumentRedirect(await getAuthRuntime(), request, key, n);
}
