import { handleLogout } from "@/server/auth/handlers";
import { withRuntime } from "@/server/bff/route";

/** Sign out and "Lock now" (POST + CSRF). ?kind=operator for the platform session. */
export const dynamic = "force-dynamic";

export const POST = withRuntime(handleLogout);
