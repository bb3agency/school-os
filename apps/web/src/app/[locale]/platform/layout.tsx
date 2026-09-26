import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";
import { PlatformShell } from "@/components/shell/PlatformShell";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale: locale === "te" ? "te" : "en", namespace: "metadata" });
  return { title: { default: t("platformTitle"), template: t("pageTitle", { page: "%s" }) } };
}

/**
 * Control-plane panel (C14). Auth (separate platform OIDC client, MFA) and the
 * SOS_DEPLOYMENT_MODE=dedicated switch-off are wired in later tasks.
 */
export default function PlatformLayout({ children }: { children: ReactNode }) {
  return <PlatformShell>{children}</PlatformShell>;
}
