import { handleCallback } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** OIDC callback of the break-glass support client (ADR-0023); starts the support session. */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleCallback(request, runtime, "support"));
