"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { ApiErrorAlert } from "@/components/ui/ApiErrorAlert";
import { Pill } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/Select";
import { Table, TableScroll, TBody, Td, Th, THead, Tr } from "@/components/ui/Table";
import { Value } from "@/components/ui/Value";
import { SourceChip } from "@/features/findings/parts";
import { Link, useRouter } from "@/i18n/navigation";
import { unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useApiForm } from "@/lib/forms";
import { localLabel, useCertificateTypes } from "./parts";
import {
  CERT_KEYS,
  CERTIFICATE_TYPES,
  type Blocker,
  type CertificateInput,
  type CertificatePreview,
  type CertificateType,
  type CertificateTypeInfo,
} from "./types";

/** The type's inputs as a zod schema (quick feedback only; the API checks every value). */
export function buildInputSchema(info: CertificateTypeInfo) {
  const shape: Record<string, z.ZodType<string | undefined>> = {};
  for (const input of info.inputs) {
    let field = z.string().trim();
    if (input.kind === "date") {
      field = field.regex(/^(\d{4}-\d{2}-\d{2})?$/, { error: "invalidDate" });
    }
    if (input.max_length) field = field.max(input.max_length, { error: "tooLong" });
    if (input.kind === "choice") {
      const values = input.choices.map((choice) => choice.value);
      field = field.refine((value) => value === "" || values.includes(value), {
        error: "chooseOption",
      });
    }
    shape[input.key] = input.required ? field.min(1, { error: "required" }) : field.optional();
  }
  return z
    .object(shape)
    .transform((values) =>
      Object.fromEntries(
        Object.entries(values).filter(
          (entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== "",
        ),
      ),
    );
}

function BlockerItem({
  blocker,
  studentId,
  fieldLabel,
}: {
  blocker: Blocker;
  studentId: string;
  fieldLabel: string;
}) {
  const t = useTranslations("certificates.issue.blockers");
  const values = { rule: blocker.rule_id ?? "—", field: fieldLabel };
  const correction = `/change-requests/new?student_id=${studentId}${
    blocker.attribute_key ? `&attribute_key=${blocker.attribute_key}` : ""
  }`;
  if (blocker.code === "dq_blocker") {
    return (
      <li>
        {t("dq_blocker", values)}{" "}
        {blocker.finding_id ? (
          <Link href={`/findings/${blocker.finding_id}`} className="text-primary underline">
            {t("openFinding")}
          </Link>
        ) : null}{" "}
        {blocker.attribute_key ? (
          <Link href={correction} className="text-primary underline">
            {t("requestCorrection")}
          </Link>
        ) : null}
      </li>
    );
  }
  if (blocker.code === "missing_value") {
    return (
      <li>
        {t("missing_value", values)}{" "}
        <Link href={correction} className="text-primary underline">
          {t("requestCorrection")}
        </Link>
      </li>
    );
  }
  if (blocker.code === "no_current_year") {
    return (
      <li>
        {t("no_current_year")}{" "}
        <Link href="/settings/structure" className="text-primary underline">
          {t("openStructure")}
        </Link>
      </li>
    );
  }
  if (blocker.code === "transfer_certificate_exists") {
    return (
      <li>
        {t("transfer_certificate_exists")}{" "}
        <Link href={`/certificates?student_id=${studentId}`} className="text-primary underline">
          {t("openCertificates")}
        </Link>
      </li>
    );
  }
  return <li>{t(blocker.code)}</li>;
}

function Preview({
  preview,
  studentId,
  onRecheck,
  rechecking,
}: {
  preview: CertificatePreview;
  studentId: string;
  onRecheck: () => void;
  rechecking: boolean;
}) {
  const t = useTranslations("certificates.issue");
  const locale = useLocale();
  const labelOf = (key: string) => {
    const field = preview.fields.find((item) => item.key === key);
    return field ? localLabel(field, locale) : key;
  };
  return (
    <Card
      title={t("previewTitle")}
      description={t("previewBody")}
      actions={
        <Button variant="secondary" size="sm" onClick={onRecheck} disabled={rechecking}>
          {rechecking ? t("working") : t("recheck")}
        </Button>
      }
    >
      <div className="space-y-4">
        {preview.blockers.length > 0 ? (
          <Alert tone="danger" title={t("blockedTitle")}>
            <p>{t("blockedBody")}</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {preview.blockers.map((blocker, i) => (
                <BlockerItem
                  key={`${blocker.code}-${blocker.finding_id ?? blocker.attribute_key ?? i}`}
                  blocker={blocker}
                  studentId={studentId}
                  fieldLabel={blocker.attribute_key ? labelOf(blocker.attribute_key) : "—"}
                />
              ))}
            </ul>
          </Alert>
        ) : null}
        {preview.warnings.length > 0 ? (
          <Alert tone="warning" title={t("provisionalTitle")}>
            {t("provisionalBody", {
              fields: preview.warnings.map((warning) => labelOf(warning.attribute_key)).join(", "),
            })}
          </Alert>
        ) : null}
        <dl className="flex flex-wrap gap-2 text-sm">
          <div className="flex gap-1">
            <dt className="text-ink-muted">{t("classLabel")}</dt>
            <dd className="font-medium">
              <Value>{preview.class_label}</Value>
            </dd>
          </div>
          <div className="flex gap-1">
            <dt className="text-ink-muted">{t("yearLabel")}</dt>
            <dd className="font-medium">
              <Value>{preview.academic_year_label}</Value>
            </dd>
          </div>
        </dl>
        <TableScroll label={t("previewTitle")}>
          <Table density="compact">
            <THead>
              <Tr>
                <Th>{t("colField")}</Th>
                <Th>{t("colValue")}</Th>
                <Th>{t("colSource")}</Th>
              </Tr>
            </THead>
            <TBody>
              {preview.fields.map((field) => (
                <Tr key={field.key}>
                  <Td className="font-medium">{localLabel(field, locale)}</Td>
                  <Td>
                    <span className="break-anywhere">
                      <Value>{field.value}</Value>
                    </span>
                  </Td>
                  <Td>
                    <span className="flex flex-wrap items-center gap-1">
                      {field.source ? <SourceChip source={field.source} /> : null}
                      {field.verified ? (
                        <Pill variant="positive">{t("verified")}</Pill>
                      ) : field.provisional ? (
                        <Pill variant="review">{t("provisional")}</Pill>
                      ) : null}
                    </span>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableScroll>
      </div>
    </Card>
  );
}

function InputField({ input, error }: { input: CertificateInput; error: string | undefined }) {
  const t = useTranslations("certificates");
  const locale = useLocale();
  const label = t(`inputs.${input.key}` as "inputs.remarks");
  const hint = input.required ? undefined : t("issue.optional");
  if (input.kind === "choice") {
    return (
      <SelectField
        name={input.key}
        label={label}
        hint={hint}
        error={error}
        placeholder={t("issue.choose")}
        options={input.choices.map((choice) => ({
          value: choice.value,
          label: localLabel(choice, locale),
        }))}
      />
    );
  }
  if (input.kind === "date") {
    return <TextField name={input.key} type="date" label={label} hint={hint} error={error} />;
  }
  return (
    <TextField
      name={input.key}
      label={label}
      hint={hint}
      error={error}
      maxLength={input.max_length ?? undefined}
      autoComplete="off"
    />
  );
}

function IssueForm({
  studentId,
  info,
  preview,
}: {
  studentId: string;
  info: CertificateTypeInfo;
  preview: CertificatePreview | null;
}) {
  const t = useTranslations("certificates.issue");
  const router = useRouter();
  const api = useBffClient("staff");
  const form = useApiForm({
    schema: buildInputSchema(info),
    fieldMap: (serverField) => serverField.replace(/^inputs\./, ""),
    invalidate: [CERT_KEYS.all],
    submit: (inputs, key) =>
      unwrap(
        api.POST("/api/v1/students/{student_id}/certificates", {
          params: { path: { student_id: studentId } },
          headers: { "Idempotency-Key": key },
          body: { certificate_type: info.key, inputs },
        }),
      ),
    onSuccess: (created) => router.push(`/certificates/${created.id}`),
  });
  const blocked = preview !== null && !preview.can_issue;
  return (
    <form noValidate onSubmit={form.onSubmit}>
      <Card
        title={t("inputsTitle")}
        description={info.requires_approval ? t("inputsBodyApproval") : t("inputsBody")}
      >
        <div className="space-y-4">
          {info.inputs.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("noInputs")}</p>
          ) : null}
          <div className="grid gap-4 md:grid-cols-2">
            {info.inputs.map((input) => (
              <InputField key={input.key} input={input} error={form.errors[input.key]} />
            ))}
          </div>
          {info.ends_enrolment ? (
            <Alert tone="warning" title={t("endsEnrolmentTitle")}>
              {t("endsEnrolmentBody")}
            </Alert>
          ) : null}
          <ApiErrorAlert error={form.error} namespace="certificates" />
          <div className="flex flex-wrap gap-3">
            <Button type="submit" disabled={form.pending || blocked || preview === null}>
              {form.pending
                ? t("working")
                : info.requires_approval
                  ? t("submitForApproval")
                  : t("submitIssue")}
            </Button>
            <ButtonLink href={`/students/${studentId}`} variant="ghost">
              {t("back")}
            </ButtonLink>
          </div>
          {blocked ? <p className="text-sm text-ink-muted">{t("blockedHint")}</p> : null}
        </div>
      </Card>
    </form>
  );
}

/**
 * Issue a certificate from the student's page (US-1101, US-1102): choose the type, check what
 * it will print and where each value comes from, see anything that blocks it (with a link to
 * the finding or to a correction request), fill the type's details and issue it (or send a
 * transfer certificate to the principal). Nothing is corrected here (invariant 6).
 */
export function IssueCertificateScreen({
  studentId,
  initialType,
}: {
  studentId: string;
  initialType: CertificateType | null;
}) {
  const t = useTranslations("certificates");
  const ti = useTranslations("certificates.issue");
  const tc = useTranslations("common");
  const te = useTranslations("errors");
  const locale = useLocale();
  const api = useBffClient("staff");
  const [type, setType] = useState<CertificateType>(initialType ?? "bonafide");
  const types = useCertificateTypes();
  const student = useApiQuery(["staff", "students", studentId], () =>
    unwrap(
      api.GET("/api/v1/students/{student_id}", { params: { path: { student_id: studentId } } }),
    ),
  );
  const preview = useQuery({
    queryKey: CERT_KEYS.preview(studentId, type),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/students/{student_id}/certificates/preview", {
          params: { path: { student_id: studentId }, query: { certificate_type: type } },
        }),
      ),
    retry: false,
  });
  const name =
    student.status === "ready" ? (student.data.canonical?.full_name?.value ?? null) : null;
  const info = types.status === "ready" ? types.data.find((item) => item.key === type) : undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumb={[
          { label: t("studentsCrumb"), href: "/students" },
          { label: name ?? t("studentCrumb"), href: `/students/${studentId}` },
          { label: ti("title") },
        ]}
        title={ti("title")}
        description={name ?? undefined}
      />
      {student.status === "error" ? (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {student.reason ? te(`load.${student.reason}`) : tc("loadErrorBody")}
        </Alert>
      ) : null}
      <Card title={ti("typeTitle")} description={ti("typeBody")}>
        <SegmentedControl
          legend={ti("typeLegend")}
          value={type}
          onValueChange={(value) => {
            const next = CERTIFICATE_TYPES.find((item) => item === value);
            if (next) setType(next);
          }}
          options={CERTIFICATE_TYPES.map((item) => ({
            value: item,
            label: info && info.key === item ? localLabel(info, locale) : t(`types.${item}`),
          }))}
        />
      </Card>
      {preview.isPending ? <LoadingState label={tc("loading")} /> : null}
      {preview.isError ? <ApiErrorAlert error={preview.error} namespace="certificates" /> : null}
      {preview.data ? (
        <Preview
          preview={preview.data}
          studentId={studentId}
          onRecheck={() => void preview.refetch()}
          rechecking={preview.isFetching}
        />
      ) : null}
      {info ? (
        <IssueForm key={type} studentId={studentId} info={info} preview={preview.data ?? null} />
      ) : types.status === "loading" ? (
        <LoadingState label={tc("loading")} />
      ) : null}
    </div>
  );
}
