"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { DataTableV2, type ColumnV2 } from "@/components/console";
import { ActionDialog } from "@/components/ui/ActionDialog";
import { Alert } from "@/components/ui/Alert";
import { Pill } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Link } from "@/i18n/navigation";
import { ApiError, toLoadable, useBffClient } from "@/lib/bff/query";
import { useStaffCan, useStaffMe } from "@/lib/bff/staff-me";
import { formatDate } from "@/lib/format";
import * as z from "@/lib/zod";
import { AckCount, ReviewPill } from "./PoliciesScreen";
import {
  COMPLIANCE_MANAGE,
  COMPLIANCE_READ,
  SAFETY_KEYS,
  askAgain,
  fetchPolicy,
  setPolicyStatus,
  type PolicyMember,
} from "./safety-data";

/**
 * One school policy (FC-10; FR-COMP-064, FR-COMP-066): its review date, the roles asked to read
 * it and every asked staff member with whether they read the current version, not yet read
 * first. The office reminds those who have not read it, or retires the policy.
 */
export function PolicyDetailScreen({ policyId }: { policyId: string }) {
  const t = useTranslations("compliancePolicies");
  const td = useTranslations("compliancePolicies.detail");
  const tc = useTranslations("common");
  const tn = useTranslations("school.nav");
  const tco = useTranslations("compliance");
  const api = useBffClient("staff");
  const can = useStaffCan();
  const meLoaded = useStaffMe() !== undefined;
  const allowed = can(COMPLIANCE_READ);
  const manage = can(COMPLIANCE_MANAGE);
  const detail = useQuery({
    queryKey: SAFETY_KEYS.policy(policyId),
    queryFn: () => fetchPolicy(api, policyId),
    enabled: allowed,
    retry: false,
  });
  const policy = detail.data;
  const missing = detail.error instanceof ApiError && detail.error.status === 404;

  const header = (
    <PageHeader
      title={policy?.title ?? t("title")}
      breadcrumb={[
        { label: tn("home"), href: "/" },
        { label: tco("title"), href: "/compliance" },
        { label: td("back"), href: "/compliance/policies" },
        { label: policy?.title ?? t("title") },
      ]}
    />
  );
  if (!meLoaded || (allowed && detail.isPending)) {
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
  if (missing || !policy) {
    return (
      <div className="space-y-5">
        {header}
        <Alert tone="warning" title={td("notFoundTitle")}>
          {td("notFoundBody")}
        </Alert>
      </div>
    );
  }

  const columns: ColumnV2<PolicyMember>[] = [
    {
      key: "name",
      stack: "title",
      header: td("colName"),
      cell: (m) => <span className="font-semibold break-words text-ink">{m.display_name}</span>,
    },
    {
      key: "roles",
      header: td("colRoles"),
      cell: (m) => (
        <span className="text-sm break-words">
          {m.roles.map((r) => policy.ack_roles.find((a) => a.role === r)?.name ?? r).join(", ")}
        </span>
      ),
    },
    {
      key: "status",
      header: td("colStatus"),
      cell: (m) =>
        m.acknowledged_at ? (
          <span className="num text-sm whitespace-nowrap">
            {td("readOn", { date: formatDate(m.acknowledged_at) ?? "" })}
          </span>
        ) : (
          <span className="flex flex-col items-start gap-1">
            <Pill variant="review">{td("notYet")}</Pill>
            {m.acknowledged_earlier ? (
              <span className="text-xs text-ink-muted">{td("earlier")}</span>
            ) : null}
          </span>
        ),
    },
  ];
  const active = policy.status === "active";

  return (
    <div className="space-y-5">
      {header}
      <Card>
        <dl className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-sm text-ink-muted">{td("reviewDue")}</dt>
            <dd className="mt-1 flex flex-wrap items-center gap-2">
              <span className="num">{formatDate(policy.review_due)}</span>
              {active ? <ReviewPill state={policy.review_state} /> : null}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-muted">{td("lastReviewed")}</dt>
            <dd className="num mt-1">
              {policy.last_reviewed_on ? formatDate(policy.last_reviewed_on) : td("notReviewed")}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-muted">{td("document")}</dt>
            <dd className="mt-1">
              {policy.document ? (
                <Link
                  href={`/documents/${policy.document.id}`}
                  className="break-anywhere text-primary underline-offset-4 hover:underline"
                >
                  {policy.document.title ?? td("openDocument")}
                </Link>
              ) : (
                t("notReady")
              )}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-muted">{td("askedRoles")}</dt>
            <dd className="mt-1 text-sm">
              {policy.ack_roles.map((r) => r.name).join(", ") || t("noTargets")}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-muted">{t("colAcknowledged")}</dt>
            <dd className="mt-1">
              <AckCount policy={policy} />
            </dd>
          </div>
        </dl>
      </Card>
      {manage ? (
        <div className="flex flex-wrap gap-2">
          {active && policy.current_version_id && policy.acknowledged < policy.targeted ? (
            <ActionDialog
              triggerLabel={td("askAgain")}
              triggerVariant="primary"
              title={td("askAgain")}
              description={policy.title}
              confirmLabel={td("askAgain")}
              schema={z.object({})}
              invalidate={[SAFETY_KEYS.policy(policy.id), SAFETY_KEYS.policies]}
              errorNamespace="compliancePolicies"
              submit={() => askAgain(api, policy)}
              renderResult={(result, close) => (
                <div className="space-y-4">
                  <p>{td("askedResult", { count: result.notified })}</p>
                  <Button variant="secondary" onClick={close}>
                    {tc("close")}
                  </Button>
                </div>
              )}
            />
          ) : null}
          <ActionDialog
            triggerLabel={active ? td("retire") : td("reactivate")}
            triggerVariant={active ? "ghost" : "secondary"}
            title={active ? td("retire") : td("reactivate")}
            description={policy.title}
            confirmLabel={active ? td("retire") : td("reactivate")}
            confirmVariant={active ? "danger" : "primary"}
            consequence={active ? td("retireConsequence") : undefined}
            schema={z.object({})}
            invalidate={[SAFETY_KEYS.policy(policy.id), SAFETY_KEYS.policies]}
            errorNamespace="compliancePolicies"
            submit={() => setPolicyStatus(api, policy, active ? "retired" : "active")}
          />
        </div>
      ) : null}
      <h2 className="text-lg font-semibold text-ink">{td("membersTitle")}</h2>
      <DataTableV2
        id="compliance-policy-members"
        stacked
        caption={td("membersTitle")}
        captionHidden
        columns={columns}
        state={toLoadable({ ...detail, data: policy.members })}
        rowKey={(m) => m.membership_id}
        emptyTitle={td("membersEmpty")}
        emptyBody=""
        onRetry={() => void detail.refetch()}
      />
    </div>
  );
}
