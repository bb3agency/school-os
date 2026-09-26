import type { SchoolChoices } from "@schoolos/api-client";
import { getTranslations } from "next-intl/server";
import { redirect } from "next/navigation";
import { SessionControls } from "@/components/session/SessionControls";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { Alert } from "@/components/ui/Alert";
import { ChooseSchoolView } from "@/features/auth/ChooseSchoolView";
import { pageMetadata } from "@/lib/metadata";
import { safeNext } from "@/server/auth/redirect";
import { apiGetAsSession, requireStaff } from "@/server/session/rsc";

export const generateMetadata = pageMetadata((t) => t("chooseSchool.title"));

type Props = {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/**
 * FR-IAM-013 / ADR-0019: pick the school to work in (GET /api/v1/me/schools, which works
 * without an active school). Also reached from "Switch school" in the school header.
 */
export default async function ChooseSchoolPage({ params, searchParams }: Props) {
  const session = await requireStaff();
  const locale = (await params).locale === "te" ? "te" : "en";
  const t = await getTranslations({ locale, namespace: "chooseSchool" });
  const te = await getTranslations({ locale, namespace: "errors.load" });
  const raw = (await searchParams).next;
  let next = safeNext(typeof raw === "string" ? raw : null, "staff");
  if (next === "/" || /^\/(en|te)\/(choose-school|no-access)(\/|\?|$)/.test(next)) {
    next = `/${locale}`;
  }

  const result = await apiGetAsSession<SchoolChoices>("staff", "/api/v1/me/schools");
  const schools = result?.data?.data ?? null;
  if (schools && schools.length === 0) redirect(`/${locale}/no-access`);

  return (
    <MinimalShell headerActions={<SessionControls kind="staff" displayName={session.displayName} />}>
      {schools ? (
        <ChooseSchoolView schools={schools} next={next} currentTenantId={session.activeTenantId} />
      ) : (
        <Alert tone="danger" title={t("loadErrorTitle")}>
          {result?.code === "mfa_required" ? te("mfa_required") : t("loadErrorBody")}
        </Alert>
      )}
    </MinimalShell>
  );
}
