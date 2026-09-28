import { handleSupportLogin } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/**
 * Break-glass support sign-in to the school app (ADR-0023 option C, SEC-021):
 * `?request=<control-plane request id>&tenant=<school id>`. 404 when the support client is off.
 */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleSupportLogin(request, runtime));
