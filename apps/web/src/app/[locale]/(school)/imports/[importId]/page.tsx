import { notFound } from "next/navigation";
import { ImportDetailScreen } from "@/features/imports/ImportDetail";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("imports.detail.loadingTitle"));

type Props = { params: Promise<{ locale: string; importId: string }> };

/** US-401 / FR-IMP-002..005: one import (404 for another school's or an unknown id). */
export default async function ImportPage({ params }: Props) {
  const { importId } = await params;
  if (!UUID_PATTERN.test(importId)) notFound();
  return <ImportDetailScreen importId={importId} />;
}
