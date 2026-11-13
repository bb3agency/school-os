import type { ReactNode } from "react";
import { ClientMessages } from "@/i18n/ClientMessages";

/** WP-04 (PERF-014): the browser gets only the message paths of this area's pages. */
export default function SettingsFeesLayout({ children }: { children: ReactNode }) {
  return <ClientMessages scope="settings/fees">{children}</ClientMessages>;
}
