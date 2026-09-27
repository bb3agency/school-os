"use client";

import { useTranslations } from "next-intl";
import { useRef } from "react";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { Link, useRouter } from "@/i18n/navigation";
import { newIdempotencyKey, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMeQuery } from "@/lib/bff/staff-me";
import { useApiForm } from "@/lib/forms";
import { DOCUMENT_KEYS } from "./data";
import { chosenFile, newDocumentSchema, storageFailure } from "./forms";
import { aclBody, aclExtra, AclFields, documentFieldMap } from "./parts";
import {
  acceptFor,
  DOC_LANGUAGES,
  DOCUMENT_PERM,
  GENERAL_DOC_TYPES,
  MAX_ISSUER,
  MAX_TITLE,
  purposeFor,
  SENSITIVITIES,
} from "./types";
import { createUploadKeys, StorageUploadError, uploadDocument } from "./upload";

/**
 * Upload a document (US-701 AC1..AC2, FR-DOC-001..005): the file goes straight to storage
 * through a presigned form, then SchoolOS registers it with its details and who can see it.
 * It opens once the virus check has passed; the document screen shows the progress.
 */
export function NewDocumentScreen() {
  const t = useTranslations("documents.new");
  const td = useTranslations("documents");
  const ttype = useTranslations("documents.docType");
  const tsens = useTranslations("documents.sensitivity");
  const tlang = useTranslations("documents.language");
  const tc = useTranslations("common");
  const me = useStaffMeQuery();
  const can = useStaffCan();
  const api = useBffClient("staff");
  const router = useRouter();
  const keys = useRef(createUploadKeys(newIdempotencyKey));

  const form = useApiForm({
    schema: newDocumentSchema,
    extra: (element) => ({ ...chosenFile(element), ...aclExtra(element) }),
    fieldMap: documentFieldMap,
    invalidate: [DOCUMENT_KEYS.all],
    submit: async (data, key) => {
      const purpose = purposeFor(data.doc_type);
      try {
        return await uploadDocument(
          api,
          data.file,
          purpose,
          {
            title: data.title,
            doc_type: data.doc_type,
            sensitivity: data.sensitivity,
            language: data.language,
            issuer: data.issuer,
            issued_on: data.issued_on,
            acl: aclBody(data),
          },
          keys.current.for(key),
        );
      } catch (failure) {
        if (failure instanceof StorageUploadError) keys.current.storageFailed();
        throw storageFailure(failure);
      }
    },
    onSuccess: (document) => router.push(`/documents/${document.id}`),
  });

  if (me.isPending) return <LoadingState label={tc("loading")} />;
  if (!can(DOCUMENT_PERM.upload)) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} />
        <Alert tone="warning" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
        <Link href="/documents" className="text-primary underline">
          {t("back")}
        </Link>
      </div>
    );
  }

  const errors = form.errors;
  return (
    <div className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Link href="/documents" className="text-primary underline">
            {t("back")}
          </Link>
        }
      />
      <form noValidate onSubmit={form.onSubmit} className="space-y-6">
        <Card title={t("fileTitle")}>
          <Field label={t("file")} hint={t("fileHint")} error={errors.file}>
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                name="file"
                type="file"
                accept={acceptFor("other")}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface file:px-3 file:py-2 file:font-semibold"
              />
            )}
          </Field>
        </Card>
        <Card title={t("detailsTitle")}>
          <div className="grid gap-4 md:grid-cols-2">
            <TextField
              name="title"
              label={t("titleField")}
              hint={t("titleHint")}
              maxLength={MAX_TITLE}
              autoComplete="off"
              required
              error={errors.title}
              className="md:col-span-2"
            />
            <SelectField
              name="doc_type"
              label={t("docType")}
              placeholder={tc("chooseOne")}
              options={GENERAL_DOC_TYPES.map((value) => ({ value, label: ttype(value) }))}
              required
              error={errors.doc_type}
            />
            <SelectField
              name="language"
              label={t("language")}
              hint={t("languageHint")}
              placeholder={t("languageUnknown")}
              options={DOC_LANGUAGES.map((value) => ({ value, label: tlang(value) }))}
              error={errors.language}
            />
            <TextField
              name="issuer"
              label={t("issuer")}
              hint={t("issuerHint")}
              maxLength={MAX_ISSUER}
              autoComplete="off"
              error={errors.issuer}
            />
            <TextField
              name="issued_on"
              type="date"
              label={t("issuedOn")}
              hint={t("issuedOnHint")}
              error={errors.issued_on}
            />
          </div>
          <fieldset className="mt-6 space-y-2">
            <legend className="text-sm font-semibold text-ink">{t("sensitivity")}</legend>
            <p className="text-sm text-ink-muted">{t("sensitivityHint")}</p>
            {SENSITIVITIES.map((value) => (
              <label key={value} className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="sensitivity"
                  value={value}
                  defaultChecked={value === "C1"}
                  className="mt-0.5 size-4"
                />
                <span>
                  <span className="font-semibold">{tsens(`${value}.short`)}</span>
                  {" · "}
                  {tsens(`${value}.hint`)}
                </span>
              </label>
            ))}
            {errors.sensitivity ? (
              <p className="text-sm font-semibold text-danger">{errors.sensitivity}</p>
            ) : null}
          </fieldset>
        </Card>
        <Card title={t("aclTitle")}>
          <AclFields errors={errors} />
        </Card>
        <p className="text-sm text-ink-muted">{t("scanNote")}</p>
        <ApiErrorAlert error={form.error} namespace="documents" />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" disabled={form.pending} aria-disabled={form.pending || undefined}>
            {form.pending ? t("uploading") : td("upload")}
          </Button>
        </div>
      </form>
    </div>
  );
}
