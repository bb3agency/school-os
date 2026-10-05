import type { SchoolChoices } from "@schoolos/api-client";
import { useTranslations } from "next-intl";
import { redirect } from "next/navigation";
import { SessionControls } from "@/components/session/SessionControls";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { Alert } from "@/components/ui/Alert";
import { ChooseSchoolView } from "@/features/auth/ChooseSchoolView";
import { PendingInvitations } from "@/features/auth/PendingInvitations";
import { pageMetadata } from "@/lib/metadata";
import { safeNext } from "@/server/auth/redirect";
import { apiGetAsSession, requireStaff } from "@/server/session/rsc";

export const generateMetadata = pageMetadata((t) => t("chooseSchool.title"));

function PickerLoadError({ mfa }: { mfa: boolean }) {
  const t = useTranslations("chooseSchool");
  const te = useTranslations("errors.load");
  return (
    <Alert tone="danger" title={t("loadErrorTitle")}>
      {mfa ? te("mfa_required") : t("loadErrorBody")}
    </Alert>
  );
}

type Props = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

/**
 * FR-IAM-013 / ADR-0019: pick the school to work in (GET /api/v1/me/schools, which works
 * without an active school). Also reached from "Switch school" in the school header.
 */
export default async function ChooseSchoolPage({ searchParams }: Props) {
  const session = await requireStaff();
  const raw = (await searchParams).next;
  let next = safeNext(typeof raw === "string" ? raw : null, "staff");
  // No locale in any URL (ADR-0036 note); safeNext has already dropped an old prefix.
  if (/^\/(choose-school|no-access)(\/|\?|$)/.test(next)) next = "/";

  const result = await apiGetAsSession<SchoolChoices>("staff", "/api/v1/me/schools");
  const schools = result?.data?.data ?? null;
  if (schools && schools.length === 0) redirect("/no-access");

  return (
    <MinimalShell
      headerActions={<SessionControls kind="staff" displayName={session.displayName} />}
    >
      <PendingInvitations />
      {schools ? (
        <ChooseSchoolView schools={schools} next={next} currentTenantId={session.activeTenantId} />
      ) : (
        <PickerLoadError mfa={result?.code === "mfa_required"} />
      )}
    </MinimalShell>
  );
}
