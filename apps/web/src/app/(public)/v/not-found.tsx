import { VerifyNotFound } from "@/features/public/VerifyPages";

/** The one "not found" for every code that does not open a certificate (ADR-0045 decision 5). */
export default function VerifyCodeNotFound() {
  return <VerifyNotFound />;
}
