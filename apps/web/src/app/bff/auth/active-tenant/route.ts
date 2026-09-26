import { handleActiveTenant } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** POST {tenant_id} (CSRF): switch the school this session works in. */
export const dynamic = "force-dynamic";

export const POST = withRuntime(handleActiveTenant);
