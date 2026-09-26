import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { SessionControls } from "@/components/session/SessionControls";
import { PlatformShell } from "@/components/shell/PlatformShell";
import { platformEnabled, requireOperator } from "@/server/session/rsc";

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
 * Control-plane panel (C14): needs an operator session from the separate platform OIDC
 * client (MFA always; ADR-0018). Switched off on dedicated hosts.
 */
export default async function PlatformLayout({ children }: { children: ReactNode }) {
  if (!platformEnabled()) notFound();
  const session = await requireOperator();
  return (
    <PlatformShell
      headerActions={
        <SessionControls kind="operator" displayName={session.displayName} tone="dark" />
      }
    >
      {children}
    </PlatformShell>
  );
}
