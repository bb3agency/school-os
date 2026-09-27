import { useTranslations } from "next-intl";
import { SessionControls } from "@/components/session/SessionControls";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { CheckInvitationsButton } from "@/features/auth/CheckInvitationsButton";
import { pageMetadata } from "@/lib/metadata";
import { getSession } from "@/server/session/rsc";

export const generateMetadata = pageMetadata((t) => t("noAccess.title"));

const SIGN_IN = "/bff/auth/login";

function NoAccessBody({ signedIn }: { signedIn: boolean }) {
  const t = useTranslations("noAccess");
  return (
    <div className="space-y-6">
      <PageHeader title={t("title")} description={t("description")} />
      <Alert tone="info" title={t("expiredTitle")}>
        {t("expiredBody")}
      </Alert>
      {signedIn ? (
        <CheckInvitationsButton />
      ) : (
        <p>
          {/* Plain link: the BFF route starts the OIDC redirect (not a client navigation). */}
          <a href={SIGN_IN} className="font-semibold text-primary underline">
            {t("signIn")}
          </a>
        </p>
      )}
    </div>
  );
}

/**
 * Signed in, but the user belongs to no school yet (ADR-0019): usually an invitation that
 * expired or was never accepted. Friendly next step: ask the office to send it again.
 */
export default async function NoAccessPage() {
  const session = await getSession("staff");
  return (
    <MinimalShell
      headerActions={
        session ? <SessionControls kind="staff" displayName={session.displayName} /> : undefined
      }
    >
      <NoAccessBody signedIn={session !== null} />
    </MinimalShell>
  );
}
