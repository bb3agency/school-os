import { handleCallback } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** OIDC redirect URI for platform operators. */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleCallback(request, runtime, "operator"));
