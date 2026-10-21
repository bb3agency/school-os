"use client";

import { useTranslations } from "next-intl";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import type { Answer } from "./types";

const VARIANTS: Record<Answer, PillVariant> = {
  given: "positive",
  refused: "negative",
  withdrawn: "tag",
  pending: "progress",
  not_answered: "tag",
};

/** A family's answer on one purpose, in words (colour only repeats the text, WCAG 1.4.1). */
export function AnswerPill({ answer }: { answer: Answer }) {
  const t = useTranslations("consents.answers");
  return (
    <Pill variant={VARIANTS[answer]} {...(answer === "not_answered" ? { icon: null } : {})}>
      {t(answer)}
    </Pill>
  );
}
