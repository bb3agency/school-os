import { notFound } from "next/navigation";
import { NoticeDeliveryScreen } from "@/features/privacy/WhatsAppNoticeScreens";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("privacy.whatsapp.delivery.title"));

type Props = { params: Promise<{ locale: string; noticeId: string }> };

/** FP-13, FR-DPDP-065..066: who the privacy notice sent on WhatsApp reached. */
export default async function PrivacyNoticeDeliveryPage({ params }: Props) {
  const { noticeId } = await params;
  if (!UUID_PATTERN.test(noticeId)) notFound();
  return <NoticeDeliveryScreen noticeId={noticeId} />;
}
