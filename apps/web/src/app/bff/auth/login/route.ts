import { handleLogin } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** Staff sign-in: Authorization Code + PKCE with the school OIDC client (FR-IAM-001). */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleLogin(request, runtime, "staff"));
