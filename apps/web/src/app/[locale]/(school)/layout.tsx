import type { ReactNode } from "react";
import { SessionControls } from "@/components/session/SessionControls";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { requireStaff } from "@/server/session/rsc";

/**
 * School console: needs a staff session (FR-IAM-001); otherwise the visitor is sent to
 * staff sign-in and brought back here. Data is loaded through the BFF, which checks the
 * session again on every call.
 */
export default async function SchoolLayout({ children }: { children: ReactNode }) {
  const session = await requireStaff();
  return (
    <SchoolShell headerActions={<SessionControls kind="staff" displayName={session.displayName} />}>
      {children}
    </SchoolShell>
  );
}
