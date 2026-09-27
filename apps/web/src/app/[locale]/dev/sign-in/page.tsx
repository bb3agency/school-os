import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DevSignInView } from "@/features/dev-sign-in/DevSignInView";
import { isDevSignInEnabled } from "@/features/dev-sign-in/enabled";
import { devSignInSchools } from "@/features/dev-sign-in/staff";
import { pageMetadata } from "@/lib/metadata";

const devMetadata = pageMetadata((t) => t("devSignIn.title"));

export async function generateMetadata(props: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  return isDevSignInEnabled() ? devMetadata(props) : {};
}

/**
 * LOCAL DEVELOPMENT ONLY: synthetic staff subjects to type at the dev OIDC stub.
 * A 404 unless `next dev` runs against the local stub issuer (`isDevSignInEnabled`), so a
 * production build (`next start`, NODE_ENV=production) never renders it. It is a shortcut to
 * the normal sign-in page, not a way around it: MFA and step-up are enforced as always.
 */
export default function DevSignInPage() {
  if (!isDevSignInEnabled()) notFound();
  return <DevSignInView schools={devSignInSchools()} />;
}
