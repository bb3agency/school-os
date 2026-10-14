import { notFound } from "next/navigation";
import { ImportDetailScreen } from "@/features/imports/ImportDetail";
import { pageMetadata } from "@/lib/metadata";
import { UUID_PATTERN } from "@/lib/validation";

export const generateMetadata = pageMetadata((t) => t("imports.detail.loadingTitle"));

type Props = {
  params: Promise<{ locale: string; importId: string }>;
  searchParams: Promise<{ preset?: string | string[] }>;
};

/** A template-library key (US-403); anything else is ignored. */
const PRESET_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** US-401 / FR-IMP-002..005: one import (404 for another school's or an unknown id).
 * `?preset=` offers a template-library preset first in the mapping step (US-403, US-204). */
export default async function ImportPage({ params, searchParams }: Props) {
  const { importId } = await params;
  if (!UUID_PATTERN.test(importId)) notFound();
  const { preset } = await searchParams;
  const initialPreset =
    typeof preset === "string" && PRESET_PATTERN.test(preset) ? preset : undefined;
  return <ImportDetailScreen importId={importId} initialPreset={initialPreset} />;
}
