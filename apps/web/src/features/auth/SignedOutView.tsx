import { useTranslations } from "next-intl";
import { MinimalShell } from "@/components/shell/MinimalShell";
import { Alert } from "@/components/ui/Alert";
import { buttonClasses } from "@/components/ui/Button";
import { cardClasses } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

export const SIGN_IN_ERRORS = [
  "signin_expired",
  "signin_failed",
  "signin_unavailable",
  "mfa_required",
  "step_up_failed",
  "support_not_allowed",
  "support_ended",
] as const;
export type SignInError = (typeof SIGN_IN_ERRORS)[number];

/** Signed-out landing page body (sign-out, "Lock now", idle timeout, failed sign-in). */
export function SignedOutView({
  operator,
  support = false,
  idle,
  error,
  devSignIn = false,
}: {
  operator: boolean;
  /** A SchoolOS support session (break-glass, ADR-0023) ended: back to the admin panel. */
  support?: boolean;
  idle: boolean;
  error: SignInError | undefined;
  /** Local development only (`isDevSignInEnabled()`): link to the dev sign-in helper. */
  devSignIn?: boolean;
}) {
  const t = useTranslations("auth");
  const td = useTranslations("devSignIn");
  const signIn = operator ? "/bff/auth/platform/login" : "/bff/auth/login";
  return (
    <MinimalShell
      headerActions={
        <Link
          href="/welcome"
          className="inline-flex min-h-10 items-center rounded-full px-3 text-sm font-medium text-primary underline underline-offset-4 hover:bg-primary-soft"
        >
          {t("signedOut.homeLink")}
        </Link>
      }
    >
      <div className={cn(cardClasses({ padding: "lg" }), "mx-auto max-w-xl space-y-5")}>
        <span className="flex size-12 items-center justify-center rounded-full bg-primary-soft text-primary">
          <Icon name="lock" className="size-6" />
        </span>
        <h1 className="text-2xl font-semibold text-ink">{t("signedOut.title")}</h1>
        {error ? (
          <Alert tone="danger" live>
            {t(`errors.${error}`)}
          </Alert>
        ) : null}
        {idle ? <Alert tone="info">{t("signedOut.idle")}</Alert> : null}
        <p className="text-ink-muted">
          {support ? t("signedOut.supportBody") : t("signedOut.body")}
        </p>
        {support ? (
          <Link href="/platform/break-glass" className={buttonClasses("primary")}>
            {t("signedOut.supportBack")}
          </Link>
        ) : (
          /* Plain link: the BFF route starts the OIDC redirect (not a client navigation). */
          <a href={signIn} className={buttonClasses("primary")}>
            {operator ? t("signedOut.platformSignIn") : t("signedOut.signIn")}
          </a>
        )}
        {devSignIn && !support ? (
          <p className="text-sm">
            <Link href="/dev/sign-in" className="text-primary underline">
              {td("link")}
            </Link>
          </p>
        ) : null}
      </div>
    </MinimalShell>
  );
}
