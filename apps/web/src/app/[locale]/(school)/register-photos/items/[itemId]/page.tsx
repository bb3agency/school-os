import { notFound } from "next/navigation";
import { ItemReviewScreen } from "@/features/extraction/ItemReview";
import { afterActionOf } from "@/features/extraction/after";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

// The page title never carries a student's name (browser history on shared office PCs).
export const generateMetadata = pageMetadata((t) => t("extraction.review.loadingTitle"));

type Props = {
  params: Promise<{ locale: string; itemId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/** US-402 AC1/AC2: one extracted row beside its page photo (404 for another school's id). */
export default async function RegisterRowPage({ params, searchParams }: Props) {
  const { itemId } = await params;
  if (!UUID_PATTERN.test(itemId)) notFound();
  const after = afterActionOf((await searchParams).after);
  return <ItemReviewScreen itemId={itemId} {...(after ? { after } : {})} />;
}
