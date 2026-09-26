import { handleSessions } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** GET: list your own sessions. DELETE ?id=<session id> (CSRF): sign one out. */
export const dynamic = "force-dynamic";

export const GET = withRuntime(handleSessions);
export const DELETE = withRuntime(handleSessions);
