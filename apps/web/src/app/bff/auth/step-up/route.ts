import { handleStepUp } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** Step-up re-authentication for staff (prompt=login; SEC-005, ADR-0018). */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleStepUp(request, runtime, "staff"));
