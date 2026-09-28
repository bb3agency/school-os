import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { isDevSignInEnabled } from "@/features/dev-sign-in/enabled";
import { UiReference } from "@/features/dev-ui/UiReference";
import { pageMetadata } from "@/lib/metadata";

const uiMetadata = pageMetadata((t) => t("devUi.title"));

export async function generateMetadata(props: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  return isDevSignInEnabled() ? uiMetadata(props) : {};
}

/**
 * LOCAL DEVELOPMENT ONLY: the living design-system reference (every UI primitive and
 * variant with synthetic content). Same guard as the dev sign-in helper: a 404 unless
 * `next dev` runs against the local stub issuer, so production builds never render it and
 * nothing links to it.
 */
export default function DevUiPage() {
  if (!isDevSignInEnabled()) notFound();
  return <UiReference />;
}
