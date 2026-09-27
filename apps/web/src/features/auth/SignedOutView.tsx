import { useLocale, useTranslations } from "next-intl";
import { Alert } from "@/components/ui/Alert";
import { buttonClasses } from "@/components/ui/Button";
import { LanguageSwitcher } from "@/components/ui/LanguageSwitcher";

export const SIGN_IN_ERRORS = [
  "signin_expired",
  "signin_failed",
  "signin_unavailable",
  "mfa_required",
  "step_up_failed",
] as const;
export type SignInError = (typeof SIGN_IN_ERRORS)[number];

/** Signed-out landing page body (sign-out, "Lock now", idle timeout, failed sign-in). */
export function SignedOutView({
  operator,
  idle,
  error,
  devSignIn = false,
}: {
  operator: boolean;
  idle: boolean;
  error: SignInError | undefined;
  /** Local development only (`isDevSignInEnabled()`): link to the dev sign-in helper. */
  devSignIn?: boolean;
}) {
  const t = useTranslations("auth");
  const td = useTranslations("devSignIn");
  const locale = useLocale();
  const tc = useTranslations("common");
  const signIn = operator ? "/bff/auth/platform/login" : "/bff/auth/login";
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3">
        <p className="text-lg font-bold text-primary">{tc("appName")}</p>
        <LanguageSwitcher />
      </header>
      <main id="main" className="mx-auto w-full max-w-xl space-y-4 p-6 md:p-10">
        <h1 className="text-2xl font-bold">{t("signedOut.title")}</h1>
        {error ? (
          <Alert tone="danger" live>
            {t(`errors.${error}`)}
          </Alert>
        ) : null}
        {idle ? <Alert tone="info">{t("signedOut.idle")}</Alert> : null}
        <p className="text-ink-muted">{t("signedOut.body")}</p>
        {/* Plain link: the BFF route starts the OIDC redirect (not a client navigation). */}
        <a href={signIn} className={buttonClasses("primary")}>
          {operator ? t("signedOut.platformSignIn") : t("signedOut.signIn")}
        </a>
        {devSignIn ? (
          <p className="text-sm">
            <a href={`/${locale}/dev/sign-in`} className="text-primary underline">
              {td("link")}
            </a>
          </p>
        ) : null}
      </main>
    </div>
  );
}
