import { headers } from "next/headers";
import { notFound, permanentRedirect } from "next/navigation";
import { VerifyBusy, VerifyResult } from "@/features/public/VerifyPages";
import { normaliseCode } from "@/lib/verification-code";
import { getAuthRuntime } from "@/server/runtime";
import { loadVerification } from "@/server/public/verify";

/**
 * `/v/{code}` on VERIFY_BASE_URL: the page a certificate's QR opens (ADR-0045; FR-CERT-021).
 * A code typed with dashes or in lower case is redirected to its canonical form first; an
 * unknown, mistyped or withdrawn code is the uniform "not found" page with status 404.
 */
export const dynamic = "force-dynamic";

export default async function VerifyCodePage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const canonical = normaliseCode(code);
  if (canonical === null) notFound();
  if (canonical !== code) permanentRedirect(`/v/${canonical}`);
  const runtime = await getAuthRuntime();
  const view = await loadVerification(runtime, new Headers(await headers()), canonical);
  if (view.state === "not_found") notFound();
  if (view.state === "busy") return <VerifyBusy />;
  return <VerifyResult certificate={view.certificate} code={view.code} />;
}
