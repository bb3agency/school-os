"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button, ButtonLink } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { useDocumentList } from "@/features/documents/data";
import { Link } from "@/i18n/navigation";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  COMPLIANCE_KEYS,
  COMPLIANCE_MANAGE,
  COMPLIANCE_READ,
  certificateFormSchema,
  fetchRegister,
  recordCertificate,
  removeCertificate,
  updateCertificate,
  type Certificate,
  type CertificateState,
  type Register,
  type RegisterRow,
} from "./data";

const STATE_VARIANTS: Record<CertificateState, PillVariant> = {
  expired: "negative",
  missing: "review",
  expiring: "progress",
  valid: "positive",
  no_expiry: "tag",
};

export function StatePill({ state }: { state: CertificateState }) {
  const t = useTranslations("compliance.state");
  return (
    <Pill variant={STATE_VARIANTS[state]} icon={state === "expired" ? "alert" : null}>
      {t(state)}
    </Pill>
  );
}

type Mode = "record" | "renew" | "edit";
interface Editing {
  mode: Mode;
  kind: string;
  certificate: Certificate | null;
}

/** The record / renew / correct dialog (FR-COMP-004, FR-COMP-005, FR-COMP-008). */
function CertificateDialog({
  register,
  editing,
  onClose,
}: {
  register: Register;
  editing: Editing;
  onClose: () => void;
}) {
  const t = useTranslations("compliance.form");
  const api = useBffClient("staff");
  const [kind, setKind] = useState(editing.kind);
  const docs = useDocumentList({ purpose: null, docType: null, status: "active" }, undefined, true);
  const cert = editing.mode === "edit" ? editing.certificate : null;
  const kindInfo = register.kinds.find((k) => k.kind === kind);
  const docOptions =
    docs.status === "ready" ? docs.data.data.map((d) => ({ value: d.id, label: d.title })) : [];
  const ownerOptions = (register.owners ?? []).map((o) => ({
    value: o.membership_id,
    label: o.display_name ?? o.membership_id,
  }));
  const title =
    editing.mode === "record"
      ? t("recordTitle")
      : t(editing.mode === "renew" ? "renewTitle" : "editTitle", {
          name: editing.certificate?.kind_label ?? kindInfo?.label ?? kind,
        });
  return (
    <ActionDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={title}
      description={editing.mode === "renew" ? t("renewDescription") : t("description")}
      confirmLabel={t(editing.mode === "edit" ? "save" : "record")}
      schema={certificateFormSchema}
      invalidate={[COMPLIANCE_KEYS.all]}
      errorNamespace="compliance"
      errorSummary
      submit={(data, key) =>
        cert
          ? updateCertificate(api, cert, data)
          : recordCertificate(api, data, editing.certificate, key)
      }
      onSuccess={onClose}
    >
      {(errors) => (
        <div className="space-y-4">
          {editing.mode === "record" ? (
            <SelectField
              label={t("kind")}
              name="kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              placeholder={t("choose")}
              options={register.kinds.map((k) => ({ value: k.kind, label: k.label }))}
              error={errors.kind}
              required
            />
          ) : (
            <input type="hidden" name="kind" value={kind} />
          )}
          {kindInfo ? (
            <p className="text-sm text-ink-muted">
              {t("issuedByHint", { authority: kindInfo.authority })}{" "}
              {kindInfo.typical_validity_months
                ? t("typicalValidity", { months: kindInfo.typical_validity_months })
                : t("typicalNoExpiry")}{" "}
              {kindInfo.note}
            </p>
          ) : null}
          {kind === "other" ? (
            <TextField
              label={t("label")}
              name="label"
              maxLength={120}
              minLength={3}
              defaultValue={cert?.label ?? editing.certificate?.label ?? ""}
              error={errors.label}
              required
            />
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label={t("referenceNo")}
              name="reference_no"
              maxLength={80}
              autoComplete="off"
              defaultValue={cert?.reference_no ?? ""}
              error={errors.reference_no}
            />
            <TextField
              label={t("issuedBy")}
              name="issued_by"
              maxLength={200}
              defaultValue={cert?.issued_by ?? ""}
              error={errors.issued_by}
            />
            <TextField
              label={t("validFrom")}
              name="valid_from"
              type="date"
              defaultValue={cert?.valid_from ?? ""}
              error={errors.valid_from}
            />
            <TextField
              label={t("validUntil")}
              hint={t("validUntilHint")}
              name="valid_until"
              type="date"
              defaultValue={cert?.valid_until ?? ""}
              error={errors.valid_until}
            />
          </div>
          <SelectField
            label={t("document")}
            hint={t("documentHint")}
            name="document_id"
            defaultValue={cert?.document?.id ?? ""}
            placeholder={docs.status === "ready" ? t("noDocument") : t("loading")}
            options={docOptions}
            error={errors.document_id}
          />
          <SelectField
            label={t("owner")}
            hint={t("ownerHint")}
            name="owner_membership_id"
            defaultValue={cert?.owner?.membership_id ?? ""}
            placeholder={t("noOwner")}
            options={ownerOptions}
            error={errors.owner_membership_id}
          />
          <TextAreaField
            label={t("notes")}
            name="notes"
            maxLength={500}
            rows={3}
            defaultValue={cert?.notes ?? ""}
            error={errors.notes}
          />
        </div>
      )}
    </ActionDialog>
  );
}

function ValidUntil({ row }: { row: RegisterRow }) {
  const t = useTranslations("compliance");
  const cert = row.certificate;
  return (
    <span className="flex flex-col items-start gap-1">
      <StatePill state={row.state} />
      {cert?.valid_until ? (
        <span className="num text-sm whitespace-nowrap">
          {formatDate(cert.valid_until)}
          {cert.days_left !== null && cert.days_left >= 0 && row.state === "expiring"
            ? ` · ${t("daysLeft", { days: cert.days_left })}`
            : null}
          {cert.days_left !== null && cert.days_left < 0
            ? ` · ${t("daysAgo", { days: -cert.days_left })}`
            : null}
        </span>
      ) : null}
    </span>
  );
}

/**
 * The school's compliance certificate register (FC-01; FR-COMP-001..FR-COMP-018): every
 * certificate the school is expected to hold for its boards, with its validity and who renews
 * it, most urgent first. The office records, renews and corrects them; reminders go out before
 * a certificate lapses. School-level facts only, no personal data.
 */
export function ComplianceScreen() {
  const t = useTranslations("compliance");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COMPLIANCE_READ);
  const manage = can(COMPLIANCE_MANAGE);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [removing, setRemoving] = useState<Certificate | null>(null);
  const register = useQuery({
    queryKey: COMPLIANCE_KEYS.register,
    queryFn: () => fetchRegister(api),
    enabled: allowed,
    retry: false,
  });

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[{ label: tn("home"), href: "/" }, { label: t("title") }]}
      actions={
        allowed ? (
          <span className="flex flex-wrap gap-2">
            {/* FC-07 / FO-12: the inspection packs build on this register. */}
            <ButtonLink href="/compliance/inspection" variant="secondary">
              {t("inspectionLink")}
            </ButtonLink>
            {manage && register.data ? (
              <Button
                variant="primary"
                onClick={() => setEditing({ mode: "record", kind: "", certificate: null })}
              >
                {t("recordButton")}
              </Button>
            ) : null}
          </span>
        ) : undefined
      }
    />
  );
  if (!meLoaded) {
    return (
      <div className="space-y-5">
        {header}
        <LoadingState label={tc("loading")} />
      </div>
    );
  }
  if (!allowed) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="info" title={t("noPermissionTitle")}>
          {t("noPermissionBody")}
        </Alert>
      </div>
    );
  }

  const data = register.data;
  const columns: ColumnV2<RegisterRow>[] = [
    {
      key: "certificate",
      stack: "title",
      header: t("colCertificate"),
      cell: (row) => (
        <span className="flex flex-col gap-0.5">
          <span className="font-semibold break-words text-ink">{row.kind_label}</span>
          {row.certificate?.reference_no ? (
            <span className="text-xs break-anywhere text-ink-muted">
              {t("reference", { number: row.certificate.reference_no })}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "validity",
      header: t("colValidity"),
      cell: (row) => <ValidUntil row={row} />,
    },
    {
      key: "owner",
      header: t("colOwner"),
      cell: (row) =>
        row.certificate?.owner ? (
          <span className="text-sm break-words">
            {row.certificate.owner.display_name ?? t("ownerLeft")}
            {row.certificate.owner.active ? null : (
              <span className="block text-xs text-ink-muted">{t("ownerInactive")}</span>
            )}
          </span>
        ) : (
          <span className="text-sm text-ink-muted">{t("noOwner")}</span>
        ),
    },
    {
      key: "copy",
      header: t("colCopy"),
      cell: (row) =>
        row.certificate?.document ? (
          <Link
            href={`/documents/${row.certificate.document.id}`}
            className="text-sm break-anywhere text-primary underline-offset-4 hover:underline"
          >
            {row.certificate.document.title ?? t("copyHidden")}
          </Link>
        ) : row.certificate ? (
          <span className="text-sm text-ink-muted">{t("noCopy")}</span>
        ) : null,
    },
  ];
  if (manage) {
    columns.push({
      key: "actions",
      header: t("colActions"),
      cell: (row) => {
        const cert = row.certificate;
        if (!cert) {
          return (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setEditing({ mode: "record", kind: row.kind, certificate: null })}
              aria-label={t("actions.recordAria", { name: row.kind_label })}
            >
              {t("actions.record")}
            </Button>
          );
        }
        return (
          <span className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={
                row.state === "expired" || row.state === "expiring" ? "primary" : "secondary"
              }
              onClick={() => setEditing({ mode: "renew", kind: cert.kind, certificate: cert })}
              aria-label={t("actions.renewAria", { name: row.kind_label })}
            >
              {t("actions.renew")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setEditing({ mode: "edit", kind: cert.kind, certificate: cert })}
              aria-label={t("actions.editAria", { name: row.kind_label })}
            >
              {t("actions.edit")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setRemoving(cert)}
              aria-label={t("actions.removeAria", { name: row.kind_label })}
            >
              {t("actions.remove")}
            </Button>
          </span>
        );
      },
    });
  }

  return (
    <div className="space-y-5">
      {header}
      {data ? (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard
            label={t("counts.expired")}
            value={String(data.counts.expired)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.expiring", { days: data.expiring_days })}
            value={String(data.counts.expiring)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.missing")}
            value={String(data.counts.missing)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.valid")}
            value={String(data.counts.valid + data.counts.no_expiry)}
            unavailableLabel=""
          />
        </dl>
      ) : null}
      {data && !data.verified ? (
        <Alert tone="info" title={t("unverifiedTitle")}>
          {t("unverifiedBody")}
        </Alert>
      ) : null}
      <DataTableV2
        id="compliance-register"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...register, data: data?.rows })}
        rowKey={(row) => row.certificate?.id ?? `missing-${row.kind}`}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void register.refetch()}
      />
      {editing && data ? (
        <CertificateDialog register={data} editing={editing} onClose={() => setEditing(null)} />
      ) : null}
      <ActionDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={t("removeDialog.title")}
        description={removing?.kind_label}
        confirmLabel={t("removeDialog.confirm")}
        confirmVariant="danger"
        consequence={t("removeDialog.consequence")}
        schema={z.object({})}
        invalidate={[COMPLIANCE_KEYS.all]}
        errorNamespace="compliance"
        submit={() => {
          if (!removing) return Promise.reject(new Error("no certificate"));
          return removeCertificate(api, removing);
        }}
      />
    </div>
  );
}
