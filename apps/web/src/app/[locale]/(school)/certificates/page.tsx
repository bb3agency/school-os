import { CertificatesScreen } from "@/features/certificates/CertificatesScreen";
import { parseCertificateFilters, type SearchParams } from "@/features/certificates/filters";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("certificates.title"));

type Props = { searchParams: Promise<SearchParams> };

/** US-1101..US-1105: certificates and requests (filters in the URL: type, status, student ID). */
export default async function CertificatesPage({ searchParams }: Props) {
  return <CertificatesScreen filters={parseCertificateFilters(await searchParams)} />;
}
