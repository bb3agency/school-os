import { taxCertificateRedirect } from "@/server/public/actions";
import { getPublicRuntime } from "@/server/public/runtime";

/**
 * GET /p/tax-certificate: the tuition fee certificate PDF of the cookie's receipt or dues link
 * (FM-06; FR-FEE-058). 303 to the presigned file (at most 5 minutes), or back to /p/view saying
 * it is not issued or still being made. It never issues a certificate.
 */
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return taxCertificateRedirect(await getPublicRuntime(), request);
}
