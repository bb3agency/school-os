import { notFound } from "next/navigation";
import { ImportSheetScreen } from "@/features/imports/ImportSheet";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("sheets.import.title"));

type Props = { params: Promise<{ locale: string; importId: string }> };

/** US-401 AC5/AC6, FR-IMP-008/009: the uploaded file as a sheet (view, correct, download). */
export default async function ImportSheetPage({ params }: Props) {
  const { importId } = await params;
  if (!UUID_PATTERN.test(importId)) notFound();
  return <ImportSheetScreen importId={importId} />;
}
