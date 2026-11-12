"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill, type PillVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { SelectField } from "@/components/ui/Select";
import { StatCard } from "@/components/ui/StatCard";
import { useDocumentList } from "@/features/documents/data";
import { Link } from "@/i18n/navigation";
import { toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formList } from "@/lib/forms";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import {
  COMPLIANCE_MANAGE,
  COMPLIANCE_READ,
  SAFETY_KEYS,
  createPolicy,
  fetchPolicies,
  policyFormSchema,
  reviewPolicy,
  updatePolicy,
  type Policies,
  type Policy,
  type ReviewState,
} from "./safety-data";

const REVIEW_VARIANTS: Record<ReviewState, PillVariant> = {
  overdue: "negative",
  due_soon: "progress",
  ok: "positive",
};

export function ReviewPill({ state }: { state: ReviewState }) {
  const t = useTranslations("compliancePolicies.reviewState");
  return (
    <Pill variant={REVIEW_VARIANTS[state]} icon={state === "overdue" ? "alert" : null}>
      {t(state)}
    </Pill>
  );
}

/** The add / edit dialog (FR-COMP-056, FR-COMP-057): a policy PDF and who must read it. */
export function PolicyDialog({
  library,
  policy,
  onClose,
}: {
  library: Pick<Policies, "roles" | "review_months">;
  policy: Policy | null;
  onClose: () => void;
}) {
  const t = useTranslations("compliancePolicies.form");
  const api = useBffClient("staff");
  const docs = useDocumentList(
    { purpose: "policy", docType: null, status: "active" },
    undefined,
    true,
  );
  const docOptions =
    docs.status === "ready" ? docs.data.data.map((d) => ({ value: d.id, label: d.title })) : [];
  const chosen = new Set(policy?.ack_roles.map((r) => r.role) ?? []);
  return (
    <ActionDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={policy ? t("editTitle", { name: policy.title }) : t("addTitle")}
      description={t("description")}
      confirmLabel={policy ? t("save") : t("add")}
      schema={policyFormSchema}
      extra={(form) => ({ ack_roles: formList(form, "ack_roles") })}
      invalidate={[SAFETY_KEYS.policies, ...(policy ? [SAFETY_KEYS.policy(policy.id)] : [])]}
      errorNamespace="compliancePolicies"
      errorSummary
      submit={(data, key) =>
        policy ? updatePolicy(api, policy, data, policy.status) : createPolicy(api, data, key)
      }
      onSuccess={onClose}
    >
      {(errors) => (
        <div className="space-y-4">
          <TextField
            label={t("title")}
            name="title"
            maxLength={120}
            minLength={3}
            defaultValue={policy?.title ?? ""}
            error={errors.title}
            required
          />
          <SelectField
            label={t("document")}
            name="document_id"
            defaultValue={policy?.document?.id ?? ""}
            placeholder={docs.status === "ready" ? t("choose") : t("loading")}
            options={docOptions}
            error={errors.document_id}
            required
          />
          <TextField
            label={t("reviewDue")}
            hint={policy ? undefined : t("reviewDueHint", { months: library.review_months })}
            name="review_due"
            type="date"
            defaultValue={policy?.review_due ?? ""}
            error={errors.review_due}
            required={policy !== null}
          />
          <fieldset className="space-y-2" aria-describedby="policy-roles-hint">
            <legend className="text-sm font-semibold text-ink">{t("roles")}</legend>
            <p id="policy-roles-hint" className="text-sm text-ink-muted">
              {t("rolesHint")}
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {library.roles.map((r) => (
                <label key={r.role} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="ack_roles"
                    value={r.role}
                    defaultChecked={chosen.has(r.role)}
                    className="mt-1 size-4 accent-primary"
                  />
                  <span>{r.name}</span>
                </label>
              ))}
            </div>
            {errors.ack_roles ? (
              <p className="text-sm text-danger-ink" role="alert">
                {errors.ack_roles}
              </p>
            ) : null}
          </fieldset>
        </div>
      )}
    </ActionDialog>
  );
}

/** "Mark reviewed" (FR-COMP-060): today, and the next review date (default 12 months). */
export function ReviewDialog({
  policy,
  months,
  onClose,
}: {
  policy: Policy;
  months: number;
  onClose: () => void;
}) {
  const t = useTranslations("compliancePolicies.reviewDialog");
  const api = useBffClient("staff");
  return (
    <ActionDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("title")}
      description={policy.title}
      confirmLabel={t("confirm")}
      schema={z.object({ next_review_due: z.string().optional() })}
      invalidate={[SAFETY_KEYS.policies, SAFETY_KEYS.policy(policy.id)]}
      errorNamespace="compliancePolicies"
      submit={(data) => reviewPolicy(api, policy, data.next_review_due)}
      onSuccess={onClose}
    >
      {(errors) => (
        <TextField
          label={t("nextReview")}
          hint={t("nextReviewHint", { months })}
          name="next_review_due"
          type="date"
          error={errors.next_review_due}
        />
      )}
    </ActionDialog>
  );
}

export function AckCount({ policy }: { policy: Policy }) {
  const t = useTranslations("compliancePolicies");
  if (policy.status === "retired") return <Pill variant="tag">{t("retired")}</Pill>;
  if (!policy.current_version_id) {
    return <span className="text-sm text-ink-muted">{t("notReady")}</span>;
  }
  if (policy.targeted === 0)
    return <span className="text-sm text-ink-muted">{t("noTargets")}</span>;
  return (
    <span className="num text-sm whitespace-nowrap">
      {t("acknowledged", { done: policy.acknowledged, total: policy.targeted })}
    </span>
  );
}

type Editing = { mode: "add" } | { mode: "edit" | "review"; policy: Policy };

/**
 * The school's policy library (FC-10; FR-COMP-056..FR-COMP-070): each policy with its next
 * review date and how many of the staff asked have read the current version. The office adds
 * policies (a PDF in Documents) and chooses which roles must read them.
 */
export function PoliciesScreen() {
  const t = useTranslations("compliancePolicies");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const tco = useTranslations("compliance");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COMPLIANCE_READ);
  const manage = can(COMPLIANCE_MANAGE);
  const [editing, setEditing] = useState<Editing | null>(null);
  const library = useQuery({
    queryKey: SAFETY_KEYS.policies,
    queryFn: () => fetchPolicies(api),
    enabled: allowed,
    retry: false,
  });
  const data = library.data;

  const header = (
    <PageHeader
      title={t("title")}
      description={t("description")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tco("title"), href: "/compliance" },
        { label: t("title") },
      ]}
      actions={
        manage && data ? (
          <Button variant="primary" onClick={() => setEditing({ mode: "add" })}>
            {t("addButton")}
          </Button>
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

  const columns: ColumnV2<Policy>[] = [
    {
      key: "policy",
      stack: "title",
      header: t("colPolicy"),
      cell: (p) => (
        <Link
          href={`/compliance/policies/${p.id}`}
          className="font-semibold break-words text-primary underline-offset-4 hover:underline"
        >
          {p.title}
        </Link>
      ),
    },
    {
      key: "review",
      header: t("colReview"),
      cell: (p) => (
        <span className="flex flex-col items-start gap-1">
          {p.status === "active" ? <ReviewPill state={p.review_state} /> : null}
          <span className="num text-sm whitespace-nowrap">{formatDate(p.review_due)}</span>
        </span>
      ),
    },
    { key: "acks", header: t("colAcknowledged"), cell: (p) => <AckCount policy={p} /> },
  ];
  if (manage) {
    columns.push({
      key: "actions",
      header: t("colActions"),
      cell: (p) => (
        <span className="flex flex-wrap gap-2">
          {p.status === "active" ? (
            <Button
              size="sm"
              variant={p.review_state === "ok" ? "secondary" : "primary"}
              onClick={() => setEditing({ mode: "review", policy: p })}
              aria-label={t("reviewAria", { name: p.title })}
            >
              {t("review")}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setEditing({ mode: "edit", policy: p })}
            aria-label={t("editAria", { name: p.title })}
          >
            {t("edit")}
          </Button>
        </span>
      ),
    });
  }

  return (
    <div className="space-y-5">
      {header}
      {data ? (
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard
            label={t("counts.overdue")}
            value={String(data.counts.overdue)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.dueSoon")}
            value={String(data.counts.due_soon)}
            unavailableLabel=""
          />
          <StatCard
            label={t("counts.pending")}
            value={String(data.counts.pending_acks)}
            unavailableLabel=""
          />
        </dl>
      ) : null}
      {data && !data.verified ? (
        <Alert tone="info" title={t("unverifiedTitle")}>
          {t("unverifiedBody", { months: data.review_months })}
        </Alert>
      ) : null}
      <DataTableV2
        id="compliance-policies"
        stacked
        caption={t("title")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...library, data: data?.items })}
        rowKey={(p) => p.id}
        emptyTitle={t("emptyTitle")}
        emptyBody={t("emptyBody")}
        onRetry={() => void library.refetch()}
      />
      {editing && data && editing.mode !== "review" ? (
        <PolicyDialog
          library={data}
          policy={editing.mode === "edit" ? editing.policy : null}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {editing?.mode === "review" && data ? (
        <ReviewDialog
          policy={editing.policy}
          months={data.review_months}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </div>
  );
}
