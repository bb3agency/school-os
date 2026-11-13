import type { ReactNode } from "react";
import { ClientMessages } from "@/i18n/ClientMessages";

/** WP-04 (PERF-014): the browser gets only the message paths of this area's pages. */
export default function RegisterPhotosLayout({ children }: { children: ReactNode }) {
  return <ClientMessages scope="register-photos">{children}</ClientMessages>;
}
