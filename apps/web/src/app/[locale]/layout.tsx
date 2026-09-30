// Self-hosted fonts (no external font CDN). Each file declares unicode-range subsets,
// so browsers download only the scripts a page actually uses.
// UI sans: Inter (variable, one file per script covers 400-700).
import "@fontsource-variable/inter/wght.css";
// KPI numbers and display accents (Latin only; :lang(te) uses the sans).
import "@fontsource/instrument-serif/400.css";
// Mono eyebrow labels and codes.
import "@fontsource/jetbrains-mono/500.css";
// Telugu glyphs: the fallback of every stack.
import "@fontsource/noto-sans-telugu/400.css";
import "@fontsource/noto-sans-telugu/600.css";
import "@fontsource/noto-sans-telugu/700.css";
import "../globals.css";

import type { Metadata, Viewport } from "next";
import { hasLocale, NextIntlClientProvider } from "next-intl";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { SIDEBAR_STATE_SCRIPT } from "@/components/shell/sidebar-script";
import { routing } from "@/i18n/routing";

type LayoutProps = {
  children: ReactNode;
  params: Promise<{ locale: string }>;
};

export async function generateMetadata({
  params,
}: Omit<LayoutProps, "children">): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: "metadata" });
  return {
    title: { default: t("schoolTitle"), template: t("pageTitle", { page: "%s" }) },
    description: t("description"),
    robots: { index: false, follow: false },
    referrer: "strict-origin-when-cross-origin",
  };
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Draw under notches and rounded corners; globals.css pads with env(safe-area-inset-*).
  viewportFit: "cover",
  colorScheme: "light",
};

export default async function LocaleLayout({ children, params }: LayoutProps) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  // Nonce-based CSP needs every page rendered per request (SEC-010).
  await connection();
  // The per-request CSP nonce (src/proxy.ts) for the one inline script below.
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    // The inline script may add data-sidebar before React hydrates (a per-viewer preference
    // the server cannot know), so <html> alone may differ from the server markup.
    <html lang={locale} suppressHydrationWarning>
      <head>
        {/* Compact sidebar on the first paint, no layout shift (docs/17 §5.2). Constant
            text; browsers hide the nonce attribute after parsing, hence the suppression. */}
        <script nonce={nonce} suppressHydrationWarning>
          {SIDEBAR_STATE_SCRIPT}
        </script>
      </head>
      <body>
        <NextIntlClientProvider>
          <QueryProvider>{children}</QueryProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
