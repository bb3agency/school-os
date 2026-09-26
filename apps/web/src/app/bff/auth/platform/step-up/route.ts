import { handleStepUp } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** Step-up re-authentication for operators (docs/16 §2). */
export const dynamic = "force-dynamic";

export const GET = withRuntime((request, runtime) => handleStepUp(request, runtime, "operator"));
