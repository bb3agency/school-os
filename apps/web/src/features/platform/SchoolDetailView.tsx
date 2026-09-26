import type { TenantDetail } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { LoadingState } from "@/components/ui/LoadingState";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabNav } from "@/components/ui/TabNav";
import { Value } from "@/components/ui/Value";
import { deploymentTone, schoolTone, subscriptionTone } from "@/features/status";
import { formatDate, formatDateTime } from "@/lib/format";
import type { Loadable } from "@/lib/loadable";

export const SCHOOL_TABS = [
  "overview",
  "subscription",
  "invoices",
  "usage",
  "deployment",
  "flags",
  "tickets",
] as const;
export type SchoolTab = (typeof SCHOOL_TABS)[number];

export function parseSchoolTab(value: string | string[] | undefined): SchoolTab {
  const candidate = Array.isArray(value) ? value[0] : value;
  return (SCHOOL_TABS as readonly string[]).includes(candidate ?? "")
    ? (candidate as SchoolTab)
    : "overview";
}

/** FR-PLT-001..005 school detail: tenant metadata, billing and fleet only (no student data). */
export function SchoolDetailView({
  schoolId,
  tab,
  detail,
}: {
  schoolId: string;
  tab: SchoolTab;
  detail: Loadable<TenantDetail | null>;
}) {
  const t = useTranslations("platform.schoolDetail");
  const tc = useTranslations("common");
  const tschool = useTranslations("status.school");
  const tsub = useTranslations("status.subscription");
  const tdep = useTranslations("status.deployment");
  const tmode = useTranslations("deploymentMode");
  const school = detail.status === "ready" ? detail.data : null;

  const tabs = SCHOOL_TABS.map((id) => ({
    id,
    label: t(`tabs.${id}`),
    href:
      id === "overview"
        ? `/platform/schools/${schoolId}`
        : `/platform/schools/${schoolId}?tab=${id}`,
  }));

  const empty = <EmptyState title={t("emptyTitle")} body={t("emptyBody")} />;

  function panel() {
    if (detail.status === "loading") return <LoadingState label={tc("loading")} />;
    if (detail.status === "error") {
      return (
        <Alert tone="danger" title={tc("loadErrorTitle")}>
          {tc("loadErrorBody")}
        </Alert>
      );
    }
    if (!school) return empty;
    switch (tab) {
      case "overview":
        return (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-ink-muted">{t("fields.legalName")}</dt>
            <dd>
              <Value>{school.billing_account?.legal_name}</Value>
            </dd>
            <dt className="text-ink-muted">{t("fields.status")}</dt>
            <dd>
              <Badge tone={schoolTone[school.status]}>{tschool(school.status)}</Badge>
            </dd>
            <dt className="text-ink-muted">{t("fields.plan")}</dt>
            <dd>
              <Value>{school.plan_name}</Value>
            </dd>
            <dt className="text-ink-muted">{t("fields.deployment")}</dt>
            <dd>{tmode(school.deployment_mode)}</dd>
            <dt className="text-ink-muted">{t("fields.createdOn")}</dt>
            <dd>
              <Value>{formatDate(school.created_at)}</Value>
            </dd>
            <dt className="text-ink-muted">{t("fields.billingEmail")}</dt>
            <dd>
              <Value>{school.billing_account?.billing_email}</Value>
            </dd>
            <dt className="text-ink-muted">{t("fields.gstin")}</dt>
            <dd>
              <Value>{school.billing_account?.gstin}</Value>
            </dd>
            <dt className="text-ink-muted">{t("fields.stateCode")}</dt>
            <dd>
              <Value>{school.billing_account?.state_code}</Value>
            </dd>
          </dl>
        );
      case "subscription":
        return school.subscription ? (
          <p className="flex flex-wrap items-center gap-3">
            <span className="font-semibold">{school.subscription.plan_name}</span>
            <Badge tone={subscriptionTone[school.subscription.status]}>
              {tsub(school.subscription.status)}
            </Badge>
            <Value>{formatDate(school.subscription.current_period_end)}</Value>
          </p>
        ) : (
          empty
        );
      case "deployment":
        return school.deployment ? (
          <p className="flex flex-wrap items-center gap-3">
            <Badge tone={deploymentTone[school.deployment.status]}>
              {tdep(school.deployment.status)}
            </Badge>
            <span>{tmode(school.deployment.mode)}</span>
            <Value>{school.deployment.version}</Value>
            <Value>{formatDateTime(school.deployment.last_heartbeat_at)}</Value>
          </p>
        ) : (
          empty
        );
      default:
        return empty;
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={school?.name ?? t("title")}
        badge={
          school ? (
            <Badge tone={schoolTone[school.status]}>{tschool(school.status)}</Badge>
          ) : undefined
        }
        actions={
          <>
            <Dialog
              title={t("suspendDialogTitle")}
              description={t("suspendDialogBody")}
              triggerLabel={t("suspend")}
              triggerVariant="secondary"
              closeLabel={tc("close")}
              footer={
                <form method="dialog" className="flex gap-2">
                  <Button type="submit" variant="secondary">
                    {tc("cancel")}
                  </Button>
                  <Button variant="danger" disabled>
                    {t("suspendConfirm")}
                  </Button>
                </form>
              }
            >
              <Alert tone="info">{tc("notConnected")}</Alert>
            </Dialog>
            <Button variant="secondary" disabled>
              {t("reactivate")}
            </Button>
            <Button variant="danger" disabled aria-describedby="offboard-note">
              {t("offboard")}
            </Button>
          </>
        }
      />
      <p id="offboard-note" className="text-sm text-ink-muted">
        {t("offboardNote")}
      </p>
      <TabNav label={t("tabsLabel")} items={tabs} activeId={tab} />
      <Card title={t(`tabs.${tab}`)}>{panel()}</Card>
    </div>
  );
}
