import { handleCallback } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** OIDC redirect URI for school staff (state, nonce and PKCE checked). */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleCallback(request, runtime, "staff"));
