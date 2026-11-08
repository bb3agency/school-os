/**
 * Opening a link the API handed out (presigned file downloads, the payment provider's sign-in
 * page): audit 2026-11-08 WS-03. The browser only ever navigates to an absolute `https:` URL.
 * Plain `http:` is accepted only between loopback hosts, i.e. when this page itself is served
 * over http on localhost (local runs and the e2e stand-in, whose presigned links point at
 * `http://localhost:<port>`). A real deployment is always https (the BFF config refuses http
 * outside loopback), so it never accepts http. `javascript:`, `data:`, relative and other
 * links are refused with {@link UnsafeFileUrlError}, which the callers show through their
 * usual error state.
 */

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export class UnsafeFileUrlError extends Error {
  constructor() {
    super("refused a link that is not https");
    this.name = "UnsafeFileUrlError";
  }
}

function currentPage(): URL | null {
  if (typeof window === "undefined") return null;
  try {
    return new URL(window.location.href);
  } catch {
    return null;
  }
}

/** The URL when the browser may navigate to it, otherwise null. */
export function safeFileUrl(value: unknown, page: URL | null = currentPage()): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  let url: URL;
  try {
    // No base: a relative link is not a file link from the API.
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.username || url.password) return null;
  if (url.protocol === "https:") return url.href;
  if (
    url.protocol === "http:" &&
    LOOPBACK_HOSTS.has(url.hostname) &&
    page !== null &&
    page.protocol === "http:" &&
    LOOPBACK_HOSTS.has(page.hostname)
  ) {
    return url.href;
  }
  return null;
}

/** The URL when it is safe; throws {@link UnsafeFileUrlError} otherwise. */
export function checkedFileUrl(value: unknown): string {
  const safe = safeFileUrl(value);
  if (safe === null) throw new UnsafeFileUrlError();
  return safe;
}

type Navigate = (url: string) => void;
const defaultNavigate: Navigate = (url) => window.location.assign(url);
let navigate: Navigate = defaultNavigate;

/** Tests replace the navigation (jsdom cannot navigate); null restores it. */
export function setFileUrlNavigatorForTesting(next: Navigate | null): void {
  navigate = next ?? defaultNavigate;
}

/** Navigate to a link the API handed out, or throw {@link UnsafeFileUrlError}. */
export function openFileUrl(value: unknown): void {
  navigate(checkedFileUrl(value));
}
