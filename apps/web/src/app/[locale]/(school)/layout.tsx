import type { Me, SchoolChoices } from "@schoolos/api-client";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { StepUpHost } from "@/components/session/StepUpHost";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { NotificationBell } from "@/features/notifications/NotificationBell";
import { AnnouncementBanner } from "@/features/school/AnnouncementBanner";
import { SuspendedBanner, type SchoolStatus } from "@/features/school-status/SuspendedBanner";
import { SupportAccessBanner } from "@/features/support-access/SupportAccessBanner";
import { uiLocale } from "@/i18n/languages";
import { apiGetAsSession, PATH_HEADER, requireStaff } from "@/server/session/rsc";

/** Permissions that use the Tally connector screens (M6); nobody else needs its status. */
const TALLY_PERMISSIONS = ["finance.read", "tally.device.manage", "tally.configure"];

/** Codes that mean "this session has no usable school right now: pick one". */
const CHOOSE_AGAIN = new Set(["active_tenant_required", "no_membership", "invalid_active_tenant"]);

/**
 * School console: needs a staff session (FR-IAM-001) with an active school (FR-IAM-013), or a
 * SchoolOS support session pinned to the school of its break-glass grant (ADR-0023: read-only
 * banner; when the grant is over the operator lands on the support signed-out page);
 * otherwise the visitor goes to sign-in or the school picker and comes back here. The shell
 * hides menu items using the effective permissions from GET /me (UX only; the BFF and API
 * check every call).
 */
export default async function SchoolLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const session = await requireStaff();
  const support = session.kind === "support";
  const locale = uiLocale((await params).locale);
  const path = (await headers()).get(PATH_HEADER) ?? `/${locale}`;
  const picker = `/${locale}/choose-school?next=${encodeURIComponent(path)}`;
  const supportEnded = `/${locale}/signed-out?kind=support&error=support_ended`;
  if (!session.activeTenantId) redirect(support ? supportEnded : picker);

  // The school's name for the sidebar comes from the picker's list (no personal data); both
  // calls run at once. Without it the sidebar simply shows no name.
  const [me, schools] = await Promise.all([
    apiGetAsSession<Me>("staff", "/api/v1/me"),
    apiGetAsSession<SchoolChoices>("staff", "/api/v1/me/schools"),
  ]);
  // The grant ended or was revoked: the API refuses the support session everywhere.
  if (support && me && (me.status === 401 || me.status === 403)) redirect(supportEnded);
  if (me && me.code && CHOOSE_AGAIN.has(me.code)) redirect(picker);
  const profile = me?.data ?? null;
  // School settings (FR-TEN-012); an older API answer may not carry them.
  const settings: Partial<Me["settings"]> | undefined = profile?.settings;
  // BR-08 / 16 §5.5: while the school is paused, /me works only for the owner and principal.
  const suspended = me?.code === "tenant_suspended";
  const schoolStatus: SchoolStatus = profile?.tenant_status ?? (suspended ? "suspended" : "active");
  // M6 (ADR-0032 Proposed): the Tally menu items show only while the school's connector flag is
  // on; its status route answers 404 while it is off. Asked only for people who could use it.
  const tallyUser =
    !suspended && (profile?.permissions ?? []).some((key) => TALLY_PERMISSIONS.includes(key));
  const tally = tallyUser
    ? (await apiGetAsSession<unknown>("staff", "/api/v1/tally/status"))?.status === 200
    : false;

  const activeTenantId = profile?.tenant_id ?? session.activeTenantId;
  const schoolName =
    schools?.data?.data?.find((school) => school.tenant_id === activeTenantId)?.name ?? null;

  return (
    <SchoolShell
      topbarActions={suspended ? null : <NotificationBell />}
      account={{
        kind: support ? "support" : "staff",
        displayName: profile?.display_name ?? session.displayName,
        roles: profile?.roles ?? null,
      }}
      schoolName={schoolName}
      permissions={profile?.permissions ?? (suspended ? [] : null)}
      canSwitchSchool={(profile?.tenant_ids.length ?? 0) > 1}
      languages={settings?.languages ?? null}
      features={{ tally }}
      banner={
        <>
          {support ? <SupportAccessBanner /> : null}
          <SuspendedBanner initialStatus={schoolStatus} />
          <AnnouncementBanner />
          <StepUpHost />
        </>
      }
    >
      {children}
    </SchoolShell>
  );
}
