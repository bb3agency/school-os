import { notFound } from "next/navigation";
import { UdiseRequestScreen } from "@/features/udise/UdiseScreens";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("udise.detail.title"));

type Props = { params: Promise<{ locale: string; requestId: string }> };

/** FO-02, FR-UDI-004..008: one UDISE+ correction request, its letter and its follow-up. */
export default async function UdiseRequestPage({ params }: Props) {
  const { requestId } = await params;
  if (!UUID_PATTERN.test(requestId)) notFound();
  return <UdiseRequestScreen requestId={requestId} />;
}
