"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonClasses } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { AttributeDef } from "@/features/findings/types";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { formList, useApiForm } from "@/lib/forms";
import { EXPORT_KEYS, useExportAttributes } from "./data";
import {
  exportFieldMap,
  LanguageField,
  refineScope,
  ScopePicker,
  scopeBody,
  scopeFields,
  StepUpNotice,
} from "./parts";
import {
  EXPORT_LANGUAGES,
  EXPORT_PERM,
  LIST_FORMATS,
  MAX_COLUMNS,
  NEVER_EXPORTED,
  RESTRICTED_CLASSIFICATION,
  STRUCTURE_COLUMNS,
  type Export,
} from "./types";

const COLUMN = /^[a-z][a-z0-9_]{0,63}$/;

/** Client checks mirroring `StudentListCreate` (apps/api/app/exports/schemas.py). */
export const studentListSchema = z
  .object({
    columns: z
      .array(z.string().regex(COLUMN, { error: "chooseColumns" }))
      .min(1, { error: "chooseColumns" })
      .max(MAX_COLUMNS, { error: "tooManyColumns" }),
    format: z.enum(LIST_FORMATS, { error: "chooseOption" }),
    language: z.enum(EXPORT_LANGUAGES, { error: "chooseOption" }),
    ...scopeFields,
  })
  .superRefine(refineScope);

export interface ColumnChoice {
  key: string;
  restricted: boolean;
  attribute?: AttributeDef;
}

/**
 * Columns this member may choose: structure columns, then attributes in catalogue order.
 * Aadhaar-as-printed fields are never offered; restricted (C3) ones only to
 * `student.read_sensitive` holders (the API refuses them otherwise).
 */
export function columnChoices(
  attributes: readonly AttributeDef[] | undefined,
  canSensitive: boolean,
): ColumnChoice[] {
  const catalogue = [...(attributes ?? [])]
    .filter((item) => !NEVER_EXPORTED.includes(item.key))
    .filter((item) => canSensitive || item.classification !== RESTRICTED_CLASSIFICATION)
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((item) => ({
      key: item.key,
      restricted: item.classification === RESTRICTED_CLASSIFICATION,
      attribute: item,
    }));
  return [...STRUCTURE_COLUMNS.map((key) => ({ key, restricted: false })), ...catalogue];
}

/** Checked at first: where the student sits and their name (the least needed to be useful). */
const DEFAULT_COLUMNS = new Set<string>([...STRUCTURE_COLUMNS, "admission_no", "full_name"]);

/**
 * New student list (US-901, FR-EXP-003..004): choose the columns, students, file type and
 * language. `student.export` with a fresh MFA sign-in (the global step-up prompt handles the
 * 428 and retries); restricted columns are recorded in the audit log.
 */
export function NewStudentListScreen() {
  const t = useTranslations("exports.new");
  const tl = useTranslations("exports.studentList");
  const tcol = useTranslations("exports.columns");
  const tf = useTranslations("exports.format");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const te = useTranslations("exports");
  const locale = useLocale();
  const router = useRouter();
  const api = useBffClient("staff");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const mayExport = can(EXPORT_PERM.studentList);
  const canSensitive = can(EXPORT_PERM.readSensitive);
  const attributes = useExportAttributes(mayExport && can(EXPORT_PERM.readBasic));
  const columnsErrorId = useId();
  const formatErrorId = useId();

  const form = useApiForm({
    schema: studentListSchema,
    extra: (element) => ({
      columns: formList(element, "columns"),
      class_ids: formList(element, "class_ids"),
      section_ids: formList(element, "section_ids"),
    }),
    fieldMap: exportFieldMap,
    invalidate: [EXPORT_KEYS.all],
    submit: (data, key): Promise<Export> =>
      unwrap(
        api.POST("/api/v1/exports/student-list", {
          headers: { "Idempotency-Key": key },
          body: {
            columns: data.columns,
            scope: scopeBody(data),
            format: data.format,
            language: data.language,
          },
        }),
      ),
    onSuccess: (created) => router.push(`/exports/${created.id}`),
  });

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!mayExport) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={tl("title")}
          breadcrumb={[
            { label: tn("home"), href: "/" },
            { label: te("title"), href: "/exports" },
            { label: tl("title") },
          ]}
        />
        <Alert tone="warning" title={tl("noPermissionTitle")}>
          {tl("noPermissionBody")}
        </Alert>
      </div>
    );
  }
  if (attributes.isPending && attributes.fetchStatus !== "idle") {
    return <LoadingState label={tc("loading")} />;
  }

  const { errors } = form;
  const choices = columnChoices(attributes.data, canSensitive);
  const label = (choice: ColumnChoice) =>
    choice.attribute
      ? locale === "te" && choice.attribute.label_te
        ? choice.attribute.label_te
        : choice.attribute.label_en
      : tcol(choice.key as (typeof STRUCTURE_COLUMNS)[number]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={tl("title")}
        description={tl("description")}
        breadcrumb={[
          { label: tn("home"), href: "/" },
          { label: te("title"), href: "/exports" },
          { label: tl("title") },
        ]}
      />
      <StepUpNotice />
      <form noValidate onSubmit={form.onSubmit} className="space-y-6">
        <Card title={tl("columnsTitle")}>
          <fieldset
            className="space-y-2"
            aria-describedby={errors.columns ? columnsErrorId : undefined}
          >
            <legend className="text-sm font-semibold text-ink">{tl("columnsLegend")}</legend>
            <p className="text-sm text-ink-muted">{tl("columnsHint", { max: MAX_COLUMNS })}</p>
            {attributes.isError ? (
              <p className="text-sm text-ink-muted">{tl("columnsPartial")}</p>
            ) : null}
            <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2 xl:grid-cols-3">
              {choices.map((choice, index) => (
                <label key={choice.key} className="inline-flex min-h-8 items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="columns"
                    value={choice.key}
                    defaultChecked={DEFAULT_COLUMNS.has(choice.key)}
                    aria-invalid={(index === 0 && Boolean(errors.columns)) || undefined}
                    className="size-4"
                  />
                  <span>{label(choice)}</span>
                  {choice.restricted ? <Badge tone="warning">{tl("restricted")}</Badge> : null}
                </label>
              ))}
            </div>
            {errors.columns ? (
              <p id={columnsErrorId} className="text-sm font-semibold text-danger">
                {errors.columns}
              </p>
            ) : null}
            <p className="text-sm text-ink-muted">{tl("aadhaarNote")}</p>
            {choices.some((choice) => choice.restricted) ? (
              <Alert tone="warning">{tl("restrictedWarning")}</Alert>
            ) : null}
          </fieldset>
        </Card>

        <Card title={t("studentsTitle")}>
          <ScopePicker errors={errors} />
        </Card>

        <Card title={t("fileTitle")}>
          <div className="space-y-4">
            <fieldset
              className="space-y-1"
              aria-describedby={errors.format ? formatErrorId : undefined}
            >
              <legend className="text-sm font-semibold text-ink">{tl("format")}</legend>
              <p className="text-sm text-ink-muted">{tl("formatHint")}</p>
              <div className="flex flex-wrap gap-x-6">
                {LIST_FORMATS.map((format) => (
                  <label key={format} className="inline-flex min-h-8 items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name="format"
                      value={format}
                      defaultChecked={format === "xlsx"}
                      className="size-4"
                    />
                    {tf(format)}
                  </label>
                ))}
              </div>
              {errors.format ? (
                <p id={formatErrorId} className="text-sm font-semibold text-danger">
                  {errors.format}
                </p>
              ) : null}
            </fieldset>
            <LanguageField errors={errors} />
          </div>
        </Card>

        <ApiErrorAlert error={form.error} namespace="exports" />
        <div className="flex flex-wrap justify-end gap-3">
          <Link href="/exports" className={buttonClasses("ghost", "md")}>
            {tc("cancel")}
          </Link>
          <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
            {form.pending ? tc("working") : tl("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}
