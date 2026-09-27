import { SIGN_IN_ERRORS, SignedOutView } from "@/features/auth/SignedOutView";
import { isDevSignInEnabled } from "@/features/dev-sign-in/enabled";
import { pageMetadata } from "@/lib/metadata";

export const generateMetadata = pageMetadata((t) => t("auth.signedOut.title"));

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

/**
 * Landing page after sign-out, "Lock now", the idle timeout or a failed sign-in.
 * Public (no session); only allowlisted error codes are shown, never raw query text.
 */
export default async function SignedOutPage({ searchParams }: Props) {
  const params = await searchParams;
  const error = SIGN_IN_ERRORS.find((code) => code === params.error);
  return (
    <SignedOutView
      operator={params.kind === "operator"}
      idle={params.reason === "idle"}
      error={error}
      devSignIn={isDevSignInEnabled()}
    />
  );
}
