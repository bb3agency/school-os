"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { z } from "zod";
import { Alert } from "@/components/ui/Alert";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, Pill } from "@/components/ui/Badge";
import { Button, ButtonLink, type ButtonSize, type ButtonVariant } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import {
  DataTable,
  Table,
  TableScroll,
  TBody,
  THead,
  Th,
  Tr,
  type Column,
} from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { Value } from "@/components/ui/Value";
import { StudentInsights } from "@/features/insights/StudentInsights";
import { Link } from "@/i18n/navigation";
import { asList, unwrap, useApiQuery, useBffClient } from "@/lib/bff/query";
import { useDateInput } from "@/lib/date-format";
import { formatDate, formatList } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";
import type { Locale } from "@/i18n/routing";
import { containsFullAadhaar } from "./aadhaar";
import { toIsoDate } from "./dates";
import { GuardedTextField } from "./fields";
import { FormDialog } from "./FormDialog";
import { PERM, useStaffPermissions, type Permissions } from "./me";
import {
  LoadGate,
  SourceChip,
  StudentStatusBadge,
  useAttributeIndex,
  useAttributes,
  useValueFormatter,
} from "./parts";
import { ProblemAlert, problemCode } from "./ProblemAlert";
import { SensitiveValue } from "./SensitiveValue";
import { ValuesBySource, ValuesHistory } from "./SourceCompare";
import { EnrolmentsCard } from "./Enrolments";
import {
  GuardianDialog,
  RemoveGuardianDialog,
  StatusDialog,
  reloadOnConflict,
} from "./StudentEdit";
import {
  VALUE_SOURCES,
  isValueSource,
  type Attribute,
  type CanonicalValue,
  type Guardian,
  type SourceValue,
  type Student,
  type ValueSource,
} from "./types";

export const studentKey = (id: string) => ["staff", "students", id] as const;

/** Findings and change requests are other screens: link to them by URL (filtered). */
function RelatedLinks({ studentId, permissions }: { studentId: string; permissions: Permissions }) {
  const t = useTranslations("students.detail");
  const q = `student_id=${encodeURIComponent(studentId)}`;
  const links = [
    ...(permissions.has(PERM.findingsRead)
      ? [{ href: `/findings?${q}`, label: t("findingsLink") }]
      : []),
    ...(permissions.has(PERM.requestChange) || permissions.has(PERM.approveChange)
      ? [{ href: `/change-requests?${q}`, label: t("changeRequestsLink") }]
      : []),
    // US-1101: certificates are issued from the checked record, starting here.
    ...(permissions.has(PERM.certificateIssue)
      ? [
          {
            href: `/students/${encodeURIComponent(studentId)}/certificates/new`,
            label: t("issueCertificateLink"),
          },
        ]
      : []),
    ...(permissions.has(PERM.certificateRead) ||
    permissions.has(PERM.certificateIssue) ||
    permissions.has(PERM.certificateApprove)
      ? [{ href: `/certificates?${q}`, label: t("certificatesLink") }]
      : []),
  ];
  if (links.length === 0) return null;
  return (
    <nav aria-label={t("relatedLabel")} data-print="hide">
      <ul className="flex flex-wrap gap-2">
        {links.map((link) => (
          <li key={link.href}>
            <ButtonLink href={link.href} variant="secondary" size="sm">
              {link.label}
            </ButtonLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function canonicalState(canonical: CanonicalValue): "verified" | "provisional" | "unverified" {
  if (canonical.verified) return "verified";
  return canonical.provisional ? "provisional" : "unverified";
}

interface RowProps {
  student: Student;
  attributeKey: string;
  attribute: Attribute | undefined;
  label: string;
  permissions: Permissions;
  onVerify: (value: SourceValue, status: "verified" | "rejected") => void;
  verifying: string | null;
  /** "Change" (non-identity) or "Request a change" (identity field), when allowed. */
  action?: ReactNode;
}

/** One attribute: the value SchoolOS uses, and every source's current value beside it. */
function AttributeRow({
  student,
  attributeKey,
  attribute,
  label,
  permissions,
  onVerify,
  verifying,
  action,
}: RowProps) {
  const t = useTranslations("students.detail");
  const ts = useTranslations("students");
  const locale = useLocale() as Locale;
  const format = useValueFormatter();
  const canonical = student.canonical[attributeKey];
  const values = (student.values[attributeKey] ?? []).filter((value) => value.current);
  const canReveal = permissions.has(PERM.readSensitive) && student.sensitive_revealable;
  const canVerify = permissions.has(PERM.updateNonIdentity) && attribute?.is_identity === false;

  const shown = (value: string | null, masked: boolean, valueId?: string) =>
    masked ? (
      <SensitiveValue
        studentId={student.id}
        attributeKey={attributeKey}
        fieldLabel={label}
        valueId={valueId}
        canReveal={canReveal}
      />
    ) : (
      <span className="font-mono font-medium">
        <Value>{format(value, attribute)}</Value>
      </span>
    );

  const conflicts = canonical?.conflicts.filter(isValueSource) ?? [];

  return (
    <tr className="align-top">
      <th
        scope="row"
        className="min-w-40 px-4 py-4 text-left font-medium whitespace-normal text-ink"
      >
        {label}
        {attribute?.is_identity ? (
          <span className="block text-xs font-normal text-ink-muted">{t("identityField")}</span>
        ) : null}
        {attribute?.classification === "C3" ? (
          <span className="block text-xs font-normal text-ink-muted">{t("restrictedField")}</span>
        ) : null}
        {action ? (
          <span className="mt-1 block font-normal" data-print="hide">
            {action}
          </span>
        ) : null}
      </th>
      <td className="px-4 py-4">
        {canonical ? (
          <div className="space-y-1">
            {shown(canonical.value, canonical.masked)}
            <div className="flex flex-wrap items-center gap-2">
              {canonical.source ? (
                <SourceChip source={canonical.source} verification={canonicalState(canonical)} />
              ) : null}
              {canonical.provisional ? <Badge tone="warning">{t("provisional")}</Badge> : null}
              {conflicts.length > 0 ? (
                <Pill variant="negative">
                  {t("conflicts", {
                    sources: formatList(
                      conflicts.map((source) => ts(`sourceShort.${source}`)),
                      locale,
                    ),
                  })}
                </Pill>
              ) : null}
            </div>
          </div>
        ) : (
          <Value>{null}</Value>
        )}
      </td>
      <td className="px-4 py-4">
        {values.length === 0 ? (
          <Value>{null}</Value>
        ) : (
          <ul className="space-y-3">
            {values.map((value) => (
              <li key={value.id} className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <SourceChip source={value.source} verification={value.verification_status} />
                  {shown(value.value, value.masked, value.id)}
                </div>
                <p className="text-xs text-ink-muted">
                  {t("recordedOn", { date: formatDate(value.recorded_at) ?? "" })}
                  {value.evidence_document_id ? ` · ${t("hasEvidence")}` : ""}
                  {value.import_batch_id ? ` · ${t("fromImport")}` : ""}
                </p>
                {canVerify && value.verification_status === "unverified" ? (
                  <div className="flex flex-wrap gap-2" data-print="hide">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={verifying === value.id}
                      onClick={() => onVerify(value, "verified")}
                    >
                      {t("markVerified")}
                      <span className="sr-only">
                        : {label},{" "}
                        {ts(
                          `sources.${isValueSource(value.source) ? value.source : "manual_entry"}`,
                        )}
                      </span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={verifying === value.id}
                      onClick={() => onVerify(value, "rejected")}
                    >
                      {t("markRejected")}
                      <span className="sr-only">: {label}</span>
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ record a value */

function valueSchema(attribute: Attribute | undefined) {
  const base = z
    .string()
    .trim()
    .min(1, { error: "required" })
    .max(1000, { error: "tooLong" })
    .refine((value) => !containsFullAadhaar(value), { error: "invalid" });
  if (attribute?.data_type === "date") {
    return base
      .refine((value) => toIsoDate(value) !== null, { error: "invalidDate" })
      .transform((value) => toIsoDate(value) ?? value);
  }
  if (attribute?.data_type === "digits4") {
    return base.refine((value) => /^\d{4}$/.test(value), { error: "invalid" });
  }
  return base;
}

function recordSchema(attribute: Attribute | undefined) {
  return z.object({
    attribute_key: z.string().trim().min(1, { error: "chooseOption" }),
    source: z.enum(VALUE_SOURCES, { error: "chooseOption" }),
    value: valueSchema(attribute),
  });
}

function RecordValueDialog({
  student,
  attributes,
  initialKey = "",
  triggerLabel,
  triggerVariant,
  triggerSize,
}: {
  student: Student;
  attributes: readonly Attribute[];
  /** Field chosen when the dialog opens (the row's "Change" button). */
  initialKey?: string;
  triggerLabel?: ReactNode;
  triggerVariant?: ButtonVariant;
  triggerSize?: ButtonSize;
}) {
  const t = useTranslations("students.record");
  const ts = useTranslations("students");
  const dates = useDateInput();
  const tc = useTranslations("common");
  const locale = useLocale();
  const api = useBffClient("staff");
  const [key, setKey] = useState(initialKey);
  const [raw, setRaw] = useState("");
  const format = useValueFormatter();
  const attribute = attributes.find((item) => item.key === key);
  const sources: readonly ValueSource[] = attribute?.allowed_sources
    ? VALUE_SOURCES.filter((source) => attribute.allowed_sources?.includes(source))
    : VALUE_SOURCES;

  const valueError = (error: string | undefined) => {
    if (!error) return undefined;
    if (containsFullAadhaar(raw)) return ts("aadhaarNotAllowed");
    if (attribute?.data_type === "date") return ts("dateInvalid", dates.hint("2012-03-14"));
    if (attribute?.data_type === "digits4") return t("digits4Invalid");
    return error;
  };

  return (
    <FormDialog
      triggerLabel={triggerLabel ?? t("open")}
      {...(triggerVariant ? { triggerVariant } : {})}
      {...(triggerSize ? { triggerSize } : {})}
      title={t("title")}
      description={t("description")}
      confirmLabel={t("submit")}
      problems="students.errors"
      problemAction={(error) =>
        problemCode(error) === "identity_change_required" ? (
          <Link
            href={`/change-requests?student_id=${encodeURIComponent(student.id)}`}
            className="font-semibold underline"
          >
            {t("goToChangeRequests")}
          </Link>
        ) : (
          reloadOnConflict([studentKey(student.id)])(error)
        )
      }
      schema={recordSchema(attribute)}
      onOpen={() => {
        setKey(initialKey);
        setRaw("");
      }}
      invalidate={[studentKey(student.id)]}
      submit={(data, idempotencyKey) =>
        unwrap(
          api.POST("/api/v1/students/{student_id}/values", {
            params: { path: { student_id: student.id } },
            // If-Match: refuse to add a value on top of changes made since this page loaded.
            headers: { "Idempotency-Key": idempotencyKey, "If-Match": `"${student.version}"` },
            body: { attribute_key: data.attribute_key, source: data.source, value: data.value },
          }),
        )
      }
    >
      {(errors) => (
        <>
          <SelectField
            name="attribute_key"
            label={t("field")}
            placeholder={tc("chooseOne")}
            error={errors.attribute_key}
            value={key}
            onChange={(event) => {
              setKey(event.currentTarget.value);
              setRaw("");
            }}
            options={attributes.map((item) => ({
              value: item.key,
              label: locale === "te" && item.label_te ? item.label_te : item.label_en,
            }))}
          />
          <SelectField
            name="source"
            label={t("source")}
            hint={t("sourceHint")}
            error={errors.source}
            defaultValue=""
            placeholder={tc("chooseOne")}
            options={sources.map((value) => ({ value, label: ts(`sources.${value}`) }))}
            key={`source-${key}`}
          />
          {attribute?.data_type === "enum" && attribute.allowed_values ? (
            <SelectField
              name="value"
              label={t("value")}
              placeholder={tc("chooseOne")}
              error={errors.value}
              options={attribute.allowed_values.map((value) => ({
                value,
                label: format(value, attribute) ?? value,
              }))}
            />
          ) : (
            <GuardedTextField
              name="value"
              label={t("value")}
              hint={
                attribute?.data_type === "date"
                  ? ts("dateHint", dates.hint("2012-03-14"))
                  : attribute?.data_type === "digits4"
                    ? t("digits4Hint")
                    : undefined
              }
              error={valueError(errors.value)}
              value={raw}
              onChange={(event) => setRaw(event.currentTarget.value)}
              autoComplete="off"
              maxLength={attribute?.data_type === "digits4" ? 4 : 1000}
              inputMode={
                attribute?.data_type === "digits4" || attribute?.data_type === "date"
                  ? "numeric"
                  : undefined
              }
            />
          )}
          {attribute?.is_identity ? <Alert tone="info">{t("identityNote")}</Alert> : null}
        </>
      )}
    </FormDialog>
  );
}

/* ------------------------------------------------------------------ guardians */

function GuardiansCard({
  student,
  guardians,
  permissions,
}: {
  student: Student;
  guardians: Loadable<readonly Guardian[]>;
  permissions: Permissions;
}) {
  const t = useTranslations("students.guardians");
  const tc = useTranslations("common");
  const canReveal = permissions.has(PERM.readSensitive) && student.sensitive_revealable;
  const canEdit = permissions.has(PERM.updateNonIdentity);
  const relationship = (value: string) =>
    value === "father" || value === "mother" || value === "guardian"
      ? t(`relationship.${value}`)
      : value;

  const contact = (guardian: Guardian, kind: "phone" | "address") => {
    const present = kind === "phone" ? guardian.has_phone : guardian.has_address;
    const value = kind === "phone" ? guardian.phone : guardian.address;
    if (!present) return <Value>{null}</Value>;
    if (!guardian.masked) return <Value>{value}</Value>;
    return (
      <SensitiveValue
        studentId={student.id}
        attributeKey={kind === "phone" ? "guardian_phone" : "guardian_address"}
        guardianId={guardian.id}
        fieldLabel={t(kind === "phone" ? "phoneOf" : "addressOf", { name: guardian.full_name })}
        canReveal={canReveal}
      />
    );
  };

  const columns: Column<Guardian>[] = [
    {
      key: "name",
      header: t("colName"),
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          <Avatar name={row.full_name} size="sm" decorative />
          {row.full_name}
          {row.is_primary ? <Badge tone="info">{t("primary")}</Badge> : null}
        </span>
      ),
    },
    {
      key: "relationship",
      header: t("colRelationship"),
      cell: (row) => relationship(row.relationship),
    },
    { key: "phone", header: t("colPhone"), cell: (row) => contact(row, "phone") },
    { key: "address", header: t("colAddress"), cell: (row) => contact(row, "address") },
    ...(canEdit
      ? [
          {
            key: "actions",
            header: <span className="sr-only">{tc("actions")}</span>,
            cell: (row: Guardian) => (
              <span className="flex flex-wrap gap-1" data-print="hide">
                <GuardianDialog studentId={student.id} guardian={row} />
                <RemoveGuardianDialog studentId={student.id} guardian={row} />
              </span>
            ),
          },
        ]
      : []),
  ];

  return (
    <Card
      title={t("title")}
      description={t("description")}
      actions={canEdit ? <GuardianDialog studentId={student.id} /> : null}
    >
      <DataTable
        caption={t("title")}
        captionHidden
        columns={columns}
        state={guardians}
        rowKey={(row) => row.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
      />
    </Card>
  );
}

/* ------------------------------------------------------------------ the page */

export interface StudentDetailViewProps {
  student: Loadable<Student>;
  attributes: Loadable<readonly Attribute[]>;
  guardians: Loadable<readonly Guardian[]>;
  permissions: Permissions;
}

/** US-301 / FR-STU-001..008: canonical profile with every source's value, guardians, class. */
export function StudentDetailView({
  student,
  attributes,
  guardians,
  permissions,
}: StudentDetailViewProps) {
  const t = useTranslations("students.detail");
  const tl = useTranslations("students.list");
  const index = useAttributeIndex(attributes);
  const api = useBffClient("staff");
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyError, setVerifyError] = useState<unknown>(undefined);
  const [verified, setVerified] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const te = useTranslations("students.edit");
  const canEdit = permissions.has(PERM.updateNonIdentity);
  const canRequest = permissions.has(PERM.requestChange);

  if (student.status !== "ready") {
    return (
      <div className="space-y-6">
        <PageHeader
          breadcrumb={[{ label: tl("title"), href: "/students" }, { label: t("loadingTitle") }]}
          title={t("loadingTitle")}
        />
        <LoadGate state={student} />
      </div>
    );
  }
  const data = student.data;
  const name = data.canonical.full_name?.value ?? t("unnamed");
  const keys = [
    ...index.sorted
      .map((item) => item.key)
      .filter((key) => key in data.canonical || key in data.values),
    ...Object.keys(data.canonical).filter((key) => !index.byKey.has(key)),
  ];

  /** Identity fields: a correction request with evidence (invariant 6); others: record a value. */
  function rowAction(key: string): ReactNode {
    const attribute = index.byKey.get(key);
    if (!attribute) return null;
    const label = index.label(key);
    if (attribute.is_identity) {
      if (!canRequest) return null;
      const query = new URLSearchParams({ student_id: data.id, attribute_key: key });
      return (
        <Link
          href={`/change-requests/new?${query.toString()}`}
          className="text-sm text-primary underline"
        >
          {te("requestChange")}
          <span className="sr-only">: {label}</span>
        </Link>
      );
    }
    if (!canEdit) return null;
    return (
      <RecordValueDialog
        student={data}
        attributes={index.sorted}
        initialKey={key}
        triggerVariant="ghost"
        triggerSize="sm"
        triggerLabel={
          <>
            {te("change")}
            <span className="sr-only">: {label}</span>
          </>
        }
      />
    );
  }

  async function verify(value: SourceValue, status: "verified" | "rejected") {
    setVerifying(value.id);
    setVerifyError(undefined);
    setVerified(null);
    try {
      await unwrap(
        api.POST("/api/v1/students/{student_id}/values/{value_id}/verify", {
          params: { path: { student_id: data.id, value_id: value.id } },
          body: { status },
        }),
      );
      setVerified(status);
      await queryClient.invalidateQueries({ queryKey: studentKey(data.id) });
    } catch (failure) {
      setVerifyError(failure);
    } finally {
      setVerifying(null);
    }
  }

  const canReveal = permissions.has(PERM.readSensitive) && data.sensitive_revealable;
  const fullName = data.canonical.full_name;
  const avatarName = fullName && !fullName.masked && fullName.value ? fullName.value : null;

  const detailsPanel = (
    <Card
      title={t("valuesTitle")}
      description={t("valuesDescription")}
      actions={
        permissions.has(PERM.updateNonIdentity) && attributes.status === "ready" ? (
          <RecordValueDialog student={data} attributes={index.sorted} />
        ) : null
      }
    >
      <div className="space-y-3">
        <ProblemAlert error={verifyError} namespace="students.errors" />
        {verified ? (
          <Alert tone="success" live>
            {verified === "verified" ? t("verifiedDone") : t("rejectedDone")}
          </Alert>
        ) : null}
        {keys.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("noValues")}</p>
        ) : (
          <TableScroll label={t("valuesTable")}>
            <Table stickyFirstColumn>
              <caption className="sr-only">{t("valuesTable")}</caption>
              <THead>
                <Tr>
                  <Th>{t("colField")}</Th>
                  <Th>{t("colUsed")}</Th>
                  <Th>{t("colSources")}</Th>
                </Tr>
              </THead>
              <TBody>
                {keys.map((key) => (
                  <AttributeRow
                    key={key}
                    student={data}
                    attributeKey={key}
                    attribute={index.byKey.get(key)}
                    label={index.label(key)}
                    permissions={permissions}
                    onVerify={verify}
                    verifying={verifying}
                    action={rowAction(key)}
                  />
                ))}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </div>
    </Card>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        breadcrumb={[{ label: tl("title"), href: "/students" }, { label: name }]}
        title={
          <span className="inline-flex items-center gap-3">
            {avatarName ? <Avatar name={avatarName} size="lg" decorative /> : null}
            <span>{name}</span>
          </span>
        }
        badge={<StudentStatusBadge status={data.status} />}
        actions={canEdit ? <StatusDialog student={data} /> : null}
        description={
          <span className="mt-1 flex flex-wrap items-center gap-2">
            <Pill variant="tag" size="md">
              {t("admissionShort")}{" "}
              <span className="font-mono text-ink">
                <Value>{data.admission_no}</Value>
              </span>
            </Pill>
            <Pill variant="tag" size="md">
              {data.enrollment?.label ?? t("noClass")}
            </Pill>
            {data.enrollment?.roll_no ? (
              <Pill variant="tag" size="md">
                {t("rollNo")} <span className="font-mono text-ink">{data.enrollment.roll_no}</span>
              </Pill>
            ) : null}
          </span>
        }
      />
      <RelatedLinks studentId={data.id} permissions={permissions} />
      <Tabs
        label={t("tabsLabel")}
        items={[
          { id: "details", label: t("tabDetails"), panel: detailsPanel },
          {
            id: "by-source",
            label: t("tabBySource"),
            panel: <ValuesBySource studentId={data.id} index={index} canReveal={canReveal} />,
          },
          {
            id: "guardians",
            label: t("tabGuardians"),
            panel: <GuardiansCard student={data} guardians={guardians} permissions={permissions} />,
          },
          {
            id: "enrolments",
            label: t("tabEnrolments"),
            panel: <EnrolmentsCard student={data} permissions={permissions} />,
          },
          {
            id: "history",
            label: t("tabHistory"),
            panel: <ValuesHistory studentId={data.id} index={index} canReveal={canReveal} />,
          },
          // M5 (US-1707): restricted, for the class teacher and the principal (FR-EW-011).
          ...(permissions.has(PERM.insightsRead) && permissions.has(PERM.readSensitive)
            ? [
                {
                  id: "timeline",
                  label: t("tabTimeline"),
                  panel: <StudentInsights studentId={data.id} />,
                },
              ]
            : []),
        ]}
      />
    </div>
  );
}

/** GET /students/{id} (404 outside the caller's scope or school), /attributes, guardians. */
export function StudentDetailScreen({ studentId }: { studentId: string }) {
  const api = useBffClient("staff");
  const permissions = useStaffPermissions();
  const student = useApiQuery(studentKey(studentId), async () => {
    const body = await unwrap(
      api.GET("/api/v1/students/{student_id}", { params: { path: { student_id: studentId } } }),
    );
    // Missing maps read as "nothing recorded" rather than crashing the whole page.
    return { ...body, canonical: body.canonical ?? {}, values: body.values ?? {} };
  });
  const attributes = useAttributes();
  const guardians = useApiQuery([...studentKey(studentId), "guardians"], async () =>
    asList(
      await unwrap(
        api.GET("/api/v1/students/{student_id}/guardians", {
          params: { path: { student_id: studentId } },
        }),
      ),
    ),
  );
  return (
    <StudentDetailView
      student={student}
      attributes={attributes}
      guardians={guardians}
      permissions={permissions}
    />
  );
}
