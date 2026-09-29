import { notFound } from "next/navigation";
import { NoticeDetailScreen } from "@/features/circulars/NoticeDetailScreen";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("notices.detailTitle"));

type Props = { params: Promise<{ locale: string; noticeId: string }> };

/** US-1605, US-1606: edit, approve, copy, print and download one parent notice. */
export default async function NoticePage({ params }: Props) {
  const { noticeId } = await params;
  if (!UUID_PATTERN.test(noticeId)) notFound();
  return <NoticeDetailScreen noticeId={noticeId} />;
}
