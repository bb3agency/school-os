import "./public.css";

import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

/**
 * Root layout of the parent pages (`(public)` route group; ADR-0046, FR-PUB-001). Its own
 * document: no staff providers, no session, no sidebar, no staff fonts or scripts. The proxy
 * serves it only on PUBLIC_BASE_URL, with its own strict CSP (a nonce Next.js reads from the
 * request, so the pages render per request: `connection()`). Each page sets its own `lang`.
 */
export const metadata: Metadata = {
  title: "SchoolOS",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light",
};

export default async function PublicLayout({ children }: { children: ReactNode }) {
  await connection();
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
