import { handleSessionInfo } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** GET: non-secret session facts for the UI (never tokens). POST (CSRF): stay signed in. */
export const dynamic = "force-dynamic";

export const GET = withRuntime(handleSessionInfo);
export const POST = withRuntime(handleSessionInfo);
