import { notFound } from "next/navigation";
import { DraftPrintScreen } from "@/features/breaches/DraftPrintScreen";
import { DRAFT_KINDS } from "@/features/breaches/types";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("breaches.print.crumb"));

type Props = { params: Promise<{ locale: string; breachId: string; draft: string }> };

/** D60 (ADR-0050): one breach draft (family notice or Board outline) as an A4 print page. */
export default async function BreachDraftPrintPage({ params }: Props) {
  const { breachId, draft } = await params;
  const kind = DRAFT_KINDS.find((item) => item === draft);
  if (!UUID_PATTERN.test(breachId) || !kind) notFound();
  return <DraftPrintScreen breachId={breachId} draft={kind} />;
}
