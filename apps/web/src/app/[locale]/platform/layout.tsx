import type { OperatorMe } from "@schoolos/api-client";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { PlatformShell } from "@/components/shell/PlatformShell";
import { apiGetAsSession, platformEnabled, requireOperator } from "@/server/session/rsc";

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
 * client (MFA always; ADR-0018). Switched off on dedicated hosts. The menu shows what the
 * operator's roles allow (GET /platform/me); the API checks every call regardless.
 */
export default async function PlatformLayout({ children }: { children: ReactNode }) {
  if (!platformEnabled()) notFound();
  const session = await requireOperator();
  const me = await apiGetAsSession<OperatorMe>("operator", "/api/v1/platform/me");
  return (
    <PlatformShell
      account={{ displayName: session.displayName, roles: me?.data?.roles ?? null }}
      permissions={me?.data?.permissions ?? null}
    >
      {children}
    </PlatformShell>
  );
}
