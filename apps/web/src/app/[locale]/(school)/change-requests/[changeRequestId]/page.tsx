import { notFound } from "next/navigation";
import { ChangeRequestDetailScreen } from "@/features/change-requests/ChangeRequestDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("changeRequests.title"));

type Props = { params: Promise<{ locale: string; changeRequestId: string }> };

/** US-601 AC1–AC4: one request; approve/reject (never by the requester), withdraw, memo. */
export default async function ChangeRequestPage({ params }: Props) {
  const { changeRequestId } = await params;
  if (!UUID_PATTERN.test(changeRequestId)) notFound();
  return <ChangeRequestDetailScreen changeRequestId={changeRequestId} />;
}
