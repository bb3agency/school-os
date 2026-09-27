import "server-only";
import { isLocalDevHost } from "@/server/config";

type Env = Readonly<Record<string, string | undefined>>;

/**
 * The dev sign-in helper exists only when BOTH hold:
 * 1. the app runs under `next dev` (NODE_ENV=development; `next build`/`next start` use
 *    production), and
 * 2. the staff OIDC issuer is the local dev stub: an http(s) URL on a loopback or `*.localhost`
 *    host, the same rule `server/config.ts` uses to accept a plain-http dev issuer.
 * Otherwise the page is a 404 and nothing links to it. It changes nothing about sign-in: the
 * helper only links to the normal `/bff/auth/login` flow (MFA and step-up unchanged, ADR-0018).
 */
export function isDevSignInEnabled(env: Env = process.env): boolean {
  if (env.NODE_ENV !== "development") return false;
  const raw = env.OIDC_ISSUER?.trim();
  if (!raw) return false;
  try {
    const issuer = new URL(raw);
    return /^https?:$/.test(issuer.protocol) && isLocalDevHost(issuer.hostname);
  } catch {
    return false;
  }
}
