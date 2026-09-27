import type { Me } from "@schoolos/api-client";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { SessionControls } from "@/components/session/SessionControls";
import { StepUpHost } from "@/components/session/StepUpHost";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { AnnouncementBanner } from "@/features/school/AnnouncementBanner";
import { SuspendedBanner, type SchoolStatus } from "@/features/school-status/SuspendedBanner";
import { apiGetAsSession, PATH_HEADER, requireStaff } from "@/server/session/rsc";

/** Codes that mean "this session has no usable school right now: pick one". */
const CHOOSE_AGAIN = new Set(["active_tenant_required", "no_membership", "invalid_active_tenant"]);

/**
 * School console: needs a staff session (FR-IAM-001) with an active school (FR-IAM-013);
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
  const locale = (await params).locale === "te" ? "te" : "en";
  const path = (await headers()).get(PATH_HEADER) ?? `/${locale}`;
  const picker = `/${locale}/choose-school?next=${encodeURIComponent(path)}`;
  if (!session.activeTenantId) redirect(picker);

  const me = await apiGetAsSession<Me>("staff", "/api/v1/me");
  if (me && me.code && CHOOSE_AGAIN.has(me.code)) redirect(picker);
  const profile = me?.data ?? null;
  // BR-08 / 16 §5.5: while the school is paused, /me works only for the owner and principal.
  const suspended = me?.code === "tenant_suspended";
  const schoolStatus: SchoolStatus = profile?.tenant_status ?? (suspended ? "suspended" : "active");

  return (
    <SchoolShell
      headerActions={
        <SessionControls kind="staff" displayName={profile?.display_name ?? session.displayName} />
      }
      permissions={profile?.permissions ?? (suspended ? [] : null)}
      canSwitchSchool={(profile?.tenant_ids.length ?? 0) > 1}
      banner={
        <>
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
