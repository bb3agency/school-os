import "server-only";
import type { components } from "@schoolos/api-client";
import { logEvent } from "@/server/log";
import { normaliseCode } from "@/lib/verification-code";
import { callPublicApi, type PublicRuntime } from "./client";

/**
 * `/v/{code}`: certificate verification through the BFF (ADR-0045; FR-CERT-021, FR-CERT-022;
 * FR-PUB-001). The code is normalised here (case, spaces, dashes, O/0 and I/L/1) and sent in
 * the path of `GET /api/v1/public/certificates/{code}` with the service token and no user token;
 * it is never logged. Every answer that is not a certificate is the same "not found"; a 429 or an
 * unreachable API is "busy".
 */

export type PublicCertificate = components["schemas"]["PublicCertificateOut"];

export type VerifyView =
  | { state: "ok"; certificate: PublicCertificate; code: string }
  | { state: "not_found" }
  | { state: "busy" };

function isCertificate(value: unknown): value is PublicCertificate {
  const v = value as Partial<PublicCertificate> | null;
  return (
    (v?.status === "valid" || v?.status === "cancelled") &&
    typeof v.serial === "string" &&
    typeof v.school_name === "string"
  );
}

export async function loadVerification(
  runtime: PublicRuntime,
  headers: Headers,
  typed: string,
): Promise<VerifyView> {
  const code = normaliseCode(typed);
  if (code === null) return { state: "not_found" };
  let upstream: Response;
  try {
    upstream = await callPublicApi(runtime, {
      method: "GET",
      path: `/api/v1/public/certificates/${code}`,
      incomingHeaders: headers,
    });
  } catch {
    logEvent("public_api_unreachable_verify", {}, "error");
    return { state: "busy" };
  }
  if (upstream.status === 429 || upstream.status >= 500) {
    await upstream.body?.cancel();
    return { state: "busy" };
  }
  if (upstream.status !== 200) {
    await upstream.body?.cancel();
    return { state: "not_found" };
  }
  const body: unknown = await upstream.json().catch(() => null);
  if (!isCertificate(body)) return { state: "not_found" };
  return { state: "ok", certificate: body, code };
}
