import { TaxCertificatesScreen } from "@/features/fees/TaxCertificates";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("fees.taxCertificate.title"));

/** FM-06 (FR-FEE-050, FR-FEE-055): tuition fee certificates for income tax, newest first. */
export default function FeeTaxCertificatesPage() {
  return <TaxCertificatesScreen />;
}
