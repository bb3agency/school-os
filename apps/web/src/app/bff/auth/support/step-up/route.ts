import { handleStepUp } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** Step-up re-authentication of a break-glass support session (ADR-0023, SEC-005). */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleStepUp(request, runtime, "support"));
