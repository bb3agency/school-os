"use client";

import { useTranslations } from "next-intl";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import type { ConsentStatus } from "./types";

const VARIANTS: Record<ConsentStatus, PillVariant> = {
  given: "positive",
  refused: "negative",
  pending: "progress",
  withdrawn: "tag",
};

/** A consent state in words (colour only repeats the text, WCAG 1.4.1). */
export function ConsentStatusPill({ status }: { status: ConsentStatus }) {
  const t = useTranslations("apaar.status");
  return <Pill variant={VARIANTS[status]}>{t(status)}</Pill>;
}
