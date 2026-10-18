import { optinFromForm } from "@/server/public/actions";
import { getAuthRuntime } from "@/server/runtime";

/**
 * POST /p/optin: the parent's message language from the opt-in page (R2-3H; ADR-0044 decision 4;
 * FR-LANG-003). A plain form post (parent cookie, CSRF token, public origin); 303 to /p/view.
 */
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return optinFromForm(await getAuthRuntime(), request);
}
