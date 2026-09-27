"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Link } from "@/i18n/navigation";
import { TENANT_SUSPENDED_EVENT } from "@/lib/bff/fetch";
import { ApiError } from "@/lib/bff/query";
import { STAFF_ME_KEY, useStaffMeQuery } from "@/lib/bff/staff-me";

export type SchoolStatus = "active" | "suspended" | "offboarding";

/** Roles that keep Plan & billing while the school is paused (16 §5.5, BR-08). */
const KEEPS_ACCESS = ["owner", "principal"];

/**
 * Suspended-school banner (FR-PLT-004, BR-08, 16 §5.5), driven by GET /me `tenant_status`.
 * The owner and principal still read /me and see what they can still do (Plan & billing);
 * everyone else gets 403 `tenant_suspended` from /me and every other route, so that code (or
 * the BFF client's TENANT_SUSPENDED_EVENT from any screen) also shows the banner. The server
 * layout passes the status it saw, so the banner is there from the first paint.
 */
export function SuspendedBanner({ initialStatus = "active" }: { initialStatus?: SchoolStatus }) {
  const t = useTranslations("suspended");
  const queryClient = useQueryClient();
  const me = useStaffMeQuery();
  const [announced, setAnnounced] = useState(false);

  useEffect(() => {
    const onSuspended = () => {
      setAnnounced(true);
      // /me tells the owner and principal apart from other staff.
      void queryClient.invalidateQueries({ queryKey: STAFF_ME_KEY });
    };
    window.addEventListener(TENANT_SUSPENDED_EVENT, onSuspended);
    return () => window.removeEventListener(TENANT_SUSPENDED_EVENT, onSuspended);
  }, [queryClient]);

  const refused = me.error instanceof ApiError && me.error.code === "tenant_suspended";
  const status: SchoolStatus =
    me.data?.tenant_status ??
    (refused || announced
      ? initialStatus === "offboarding"
        ? "offboarding"
        : "suspended"
      : initialStatus);
  if (status === "active") return null;

  const keepsAccess = me.data?.roles.some((role) => KEEPS_ACCESS.includes(role)) ?? false;
  const canSeeBilling = me.data?.permissions.includes("tenant.billing.read") ?? false;
  const kind = status === "offboarding" ? "offboarding" : "suspended";

  return (
    <section aria-label={t("label")} className="mb-6" data-print="hide">
      <Alert tone={kind === "offboarding" ? "warning" : "danger"} title={t(`${kind}.title`)}>
        <p>{keepsAccess ? t(`${kind}.leaderBody`) : t(`${kind}.staffBody`)}</p>
        <p className="mt-1">{t("nothingDeleted")}</p>
        {keepsAccess && canSeeBilling ? (
          <p className="mt-2">
            <Link href="/settings/billing" className="font-semibold underline">
              {t("billingLink")}
            </Link>
          </p>
        ) : null}
      </Alert>
    </section>
  );
}
