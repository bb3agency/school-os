"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { SelectField } from "@/components/ui/Select";
import { Tabs } from "@/components/ui/Tabs";

const text = (max: number) =>
  z.string().trim().min(1, { error: "bothLanguages" }).max(max, { error: "tooLong" });

const announcementSchema = z.object({
  titleEn: text(120),
  titleTe: text(120),
  bodyEn: text(1000),
  bodyTe: text(1000),
  audience: z.enum(["all", "shared", "dedicated"], { error: "chooseOption" }),
  startsAt: z.string().min(1, { error: "required" }),
  endsAt: z.string(),
});

type Field = keyof z.input<typeof announcementSchema>;
type ErrorKey = "bothLanguages" | "tooLong" | "chooseOption" | "required";
const ERROR_KEYS: readonly ErrorKey[] = ["bothLanguages", "tooLong", "chooseOption", "required"];

/** FR-PLT-026: bilingual banner editor. Both languages are required before saving. */
export function AnnouncementEditor() {
  const t = useTranslations("platform.announcements");
  const tv = useTranslations("platform.validation");
  const tc = useTranslations("common");
  const [errors, setErrors] = useState<Partial<Record<Field, ErrorKey>>>({});
  const [submitted, setSubmitted] = useState(false);

  const err = (field: Field) => {
    const key = errors[field];
    return key ? tv(key) : undefined;
  };

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const read = (name: Field) => {
      const value = data.get(name);
      return typeof value === "string" ? value : "";
    };
    const result = announcementSchema.safeParse({
      titleEn: read("titleEn"),
      titleTe: read("titleTe"),
      bodyEn: read("bodyEn"),
      bodyTe: read("bodyTe"),
      audience: read("audience"),
      startsAt: read("startsAt"),
      endsAt: read("endsAt"),
    });
    if (result.success) {
      setErrors({});
      setSubmitted(true);
      return;
    }
    const next: Partial<Record<Field, ErrorKey>> = {};
    for (const issue of result.error.issues) {
      const field = issue.path[0] as Field;
      if (next[field]) continue;
      next[field] = (ERROR_KEYS as readonly string[]).includes(issue.message)
        ? (issue.message as ErrorKey)
        : "required";
    }
    setErrors(next);
    setSubmitted(false);
  }

  const languagePanel = (lang: "en" | "te") => {
    const suffix = lang === "en" ? "En" : "Te";
    return (
      <div className="space-y-4" lang={lang}>
        <TextField
          name={`title${suffix}`}
          label={t("titleLabel")}
          error={err(`title${suffix}`)}
          maxLength={120}
          autoComplete="off"
        />
        <TextAreaField
          name={`body${suffix}`}
          label={t("bodyLabel")}
          error={err(`body${suffix}`)}
          maxLength={1000}
          rows={4}
        />
      </div>
    );
  };

  const languageErrors = errors.titleEn || errors.titleTe || errors.bodyEn || errors.bodyTe;

  return (
    <form noValidate onSubmit={onSubmit} className="space-y-4">
      <p className="text-sm text-ink-muted">{t("bothLanguagesHint")}</p>
      {languageErrors ? (
        <Alert tone="danger" live>
          {tv("bothLanguages")}
        </Alert>
      ) : null}
      <Tabs
        label={t("languagesLabel")}
        items={[
          { id: "en", label: t("english"), panel: languagePanel("en") },
          { id: "te", label: t("telugu"), panel: languagePanel("te") },
        ]}
      />
      <div className="grid gap-4 md:grid-cols-3">
        <SelectField
          name="audience"
          label={t("audience")}
          error={err("audience")}
          defaultValue="all"
          options={[
            { value: "all", label: t("audienceAll") },
            { value: "shared", label: t("audienceShared") },
            { value: "dedicated", label: t("audienceDedicated") },
          ]}
        />
        <TextField
          name="startsAt"
          type="datetime-local"
          label={t("startsAt")}
          error={err("startsAt")}
        />
        <TextField name="endsAt" type="datetime-local" label={t("endsAt")} />
      </div>
      {submitted ? (
        <Alert tone="warning" live>
          {tc("notConnected")}
        </Alert>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit">{t("save")}</Button>
      </div>
    </form>
  );
}
