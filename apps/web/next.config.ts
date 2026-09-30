import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/**
 * Headers for responses the proxy does not see (hashed static assets under /_next/static).
 * Page, route-handler and BFF responses get the full set, including the per-request CSP,
 * from src/proxy.ts (SEC-010, docs/07 §11).
 */
const staticAssetHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  // Monorepo: trace dependencies from the repository root so the standalone bundle
  // includes hoisted node_modules and the workspace api-client package.
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
  transpilePackages: ["@schoolos/api-client"],
  poweredByHeader: false,
  reactStrictMode: true,
  productionBrowserSourceMaps: false,
  // No next/image optimisation: avoids shipping sharp/libvips (LGPL) in the image and an
  // image-proxy endpoint we do not need. Files are served via presigned S3 URLs.
  images: { unoptimized: true },
  // ADR-0036: the Telugu font files, read by the /fonts/telugu route only while Telugu is on.
  outputFileTracingIncludes: {
    // The key is a glob over route paths ("[file]" would be a character class).
    "/fonts/telugu/**": [
      "../../node_modules/@fontsource/noto-sans-telugu/files/noto-sans-telugu-telugu-{400,600,700}-normal.woff2",
    ],
  },
  outputFileTracingExcludes: {
    "*": ["../../node_modules/sharp/**", "../../node_modules/@img/**"],
  },
  async headers() {
    return [{ source: "/:path*", headers: staticAssetHeaders }];
  },
};

export default withNextIntl(nextConfig);
