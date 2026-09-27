import { notFound } from "next/navigation";
import { NextItemScreen } from "@/features/extraction/ItemReview";
import { afterActionOf } from "@/features/extraction/after";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("extraction.review.openingNext"));

type Props = {
  params: Promise<{ locale: string; batchId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** Opens the next row waiting for review in the batch (US-402). */
export default async function NextRegisterRowPage({ params, searchParams }: Props) {
  const { batchId } = await params;
  if (!UUID_PATTERN.test(batchId)) notFound();
  const after = afterActionOf((await searchParams).after);
  return <NextItemScreen batchId={batchId} {...(after ? { after } : {})} />;
}
