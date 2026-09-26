import { handleLogin } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** Operator sign-in with the separate platform OIDC client (FR-PLT-028, ADR-0018). */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleLogin(request, runtime, "operator"));
