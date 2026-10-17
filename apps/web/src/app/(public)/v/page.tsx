import { VerifyForm } from "@/features/public/VerifyPages";

/**
 * `/v` on VERIFY_BASE_URL: the code box (ADR-0045 decision 6; FR-CERT-022). No lookup by
 * serial, name or date of birth. `?error=invalid` (set by `/v/check`) shows the field error.
 */
export const dynamic = "force-dynamic";

export default async function VerifyPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  return <VerifyForm invalid={params.error === "invalid"} />;
}
