"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import type { FieldErrors } from "@/lib/forms";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { text } from "@/lib/validation";
import { ASK_KEYS, useKnowledgeApi, type VerifiedAnswer } from "./data";

/** Same shape as the API's SourceUri (schemas.py); the API accepts only `sos://doc` here. */
const DOC_SOURCE = /^sos:\/\/doc\/[0-9a-f-]{36}\/v\d+#p\d+$/;
const MAX_CITATIONS = 20;

const noAadhaar = (max: number) =>
  text(max).refine((value) => !containsAadhaarNumber(value), { error: "noAadhaar" });

export const verifiedAnswerSchema = z.object({
  question: noAadhaar(500),
  language: z.enum(["en", "te", "mixed"], { error: "required" }),
  answer_text: noAadhaar(5000),
  review_due: z
    .string()
    .trim()
    .transform((value) => (value === "" ? null : value))
    .pipe(z.iso.date({ error: "invalid" }).nullable()),
  citations: z
    .array(
      z.object({
        source: z.string().trim().regex(DOC_SOURCE, { error: "invalid" }),
        cited_text: text(2000),
      }),
    )
    .min(1, { error: "required" })
    .max(MAX_CITATIONS, { error: "tooLong" }),
});

export interface CitationDraft {
  source: string;
  cited_text: string;
}

export interface VerifiedDraft {
  question: string;
  language: "en" | "te" | "mixed";
  answer_text: string;
  citations: CitationDraft[];
}

/** `citations[0].source` (service) or `citations.0.source` (validation) → `source_0`. */
export function verifiedFieldMap(field: string): string | undefined {
  const match = /^citations(?:\[(\d+)\]|\.(\d+))\.(source|cited_text)$/.exec(field);
  if (match) return `${match[3]}_${match[1] ?? match[2]}`;
  const last = field.split(".").pop();
  return last === "citations" ? "source_0" : last;
}

function citationsFrom(form: HTMLFormElement): CitationDraft[] {
  const data = new FormData(form);
  const out: CitationDraft[] = [];
  for (let i = 0; i < MAX_CITATIONS + 1; i += 1) {
    const source = data.get(`source_${i}`);
    const quote = data.get(`cited_text_${i}`);
    if (typeof source !== "string" && typeof quote !== "string") continue;
    out.push({
      source: typeof source === "string" ? source : "",
      cited_text: typeof quote === "string" ? quote : "",
    });
  }
  return out;
}

function CitationRows({ initial, errors }: { initial: CitationDraft[]; errors: FieldErrors }) {
  const t = useTranslations("ask.verified");
  const [rows, setRows] = useState<Array<CitationDraft & { key: number }>>(() =>
    (initial.length > 0 ? initial : [{ source: "", cited_text: "" }]).map((row, key) => ({
      ...row,
      key,
    })),
  );
  const error = (i: number, name: "source" | "cited_text") =>
    errors[`citations.${i}.${name}`] ?? errors[`${name}_${i}`];
  return (
    <fieldset className="space-y-4">
      <legend className="text-sm font-semibold">{t("citations")}</legend>
      <p className="text-sm text-ink-muted">{t("citationsHint")}</p>
      {errors.citations ? (
        <p className="text-sm font-semibold text-danger">{errors.citations}</p>
      ) : null}
      {rows.map((row, i) => (
        <div key={row.key} className="space-y-2 rounded-md border border-border p-3">
          <TextField
            name={`source_${i}`}
            label={t("citationSource", { number: i + 1 })}
            hint={t("citationSourceHint")}
            defaultValue={row.source}
            maxLength={300}
            autoComplete="off"
            spellCheck={false}
            error={error(i, "source")}
          />
          <TextAreaField
            name={`cited_text_${i}`}
            label={t("citationText", { number: i + 1 })}
            defaultValue={row.cited_text}
            maxLength={2000}
            rows={3}
            error={error(i, "cited_text")}
          />
          {rows.length > 1 ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRows((list) => list.filter((item) => item.key !== row.key))}
            >
              {t("removeCitation", { number: i + 1 })}
            </Button>
          ) : null}
        </div>
      ))}
      {rows.length < MAX_CITATIONS ? (
        <Button
          variant="secondary"
          size="sm"
          onClick={() =>
            setRows((list) => [
              ...list,
              { source: "", cited_text: "", key: Math.max(-1, ...list.map((r) => r.key)) + 1 },
            ])
          }
        >
          {t("addCitation")}
        </Button>
      ) : null}
    </fieldset>
  );
}

/**
 * Save a checked answer as a verified answer (US-802, FR-KB-030; `kb.verified_answer.manage`).
 * From an Ask answer the fields are prefilled (document sources only, quotes from the
 * snippets); the person checks them before saving. The API checks every quote against the
 * current version of the page (422 citation_* codes, shown on the field).
 */
export function VerifiedAnswerDialog({
  draft,
  triggerLabel,
  triggerVariant = "secondary",
  onSaved,
}: {
  draft: VerifiedDraft | null;
  triggerLabel: string;
  triggerVariant?: "primary" | "secondary";
  onSaved?: (answer: VerifiedAnswer) => void;
}) {
  const t = useTranslations("ask.verified");
  const api = useKnowledgeApi();
  return (
    <ActionDialog
      triggerLabel={triggerLabel}
      triggerVariant={triggerVariant}
      title={t("dialogTitle")}
      description={t("dialogDescription")}
      confirmLabel={t("confirm")}
      schema={verifiedAnswerSchema}
      extra={(form) => ({ citations: citationsFrom(form) })}
      fieldMap={verifiedFieldMap}
      invalidate={[ASK_KEYS.verifiedAll]}
      submit={(data, key) => api.createVerified(data, key)}
      {...(onSaved ? { onSuccess: onSaved } : {})}
      errorNamespace="ask"
    >
      {(errors) => (
        <>
          <TextAreaField
            name="question"
            label={t("question")}
            defaultValue={draft?.question ?? ""}
            maxLength={500}
            rows={2}
            error={errors.question}
          />
          <SelectField
            name="language"
            label={t("language")}
            defaultValue={draft?.language ?? "en"}
            options={(["en", "te", "mixed"] as const).map((value) => ({
              value,
              label: t(`languages.${value}`),
            }))}
            error={errors.language}
          />
          <TextAreaField
            name="answer_text"
            label={t("answer")}
            hint={t("answerHint")}
            defaultValue={draft?.answer_text ?? ""}
            maxLength={5000}
            rows={6}
            error={errors.answer_text}
          />
          <CitationRows initial={draft?.citations ?? []} errors={errors} />
          <TextField
            name="review_due"
            type="date"
            label={t("reviewDue")}
            error={errors.review_due}
          />
        </>
      )}
    </ActionDialog>
  );
}
