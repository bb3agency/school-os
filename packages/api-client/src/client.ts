import createClient, { type Client } from "openapi-fetch";
import type { paths } from "./paths";

/** Anything shaped like `fetch` for a `Request` (global fetch, a BFF wrapper, a test double). */
export type FetchLike = (input: Request) => Promise<Response>;

export type ApiClient = Client<paths>;

/**
 * Typed client for the SchoolOS API.
 *
 * - In the browser: `createApiClient("/bff", fetch)` — requests go to the same-origin BFF
 *   (`/bff/api/v1/...`), which holds the session; the browser never sees tokens.
 * - In BFF route handlers: `createApiClient(API_INTERNAL_URL, fetchWithServiceToken)`.
 */
export function createApiClient(baseUrl: string, fetchImpl: FetchLike): ApiClient {
  const trimmed = baseUrl.replace(/\/+$/, "");
  return createClient<paths>({
    baseUrl: trimmed,
    fetch: fetchImpl,
    headers: { Accept: "application/json" },
  });
}
