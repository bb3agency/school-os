"use client";

import { useEffect } from "react";
import { clearDeviceDrafts } from "@/lib/device-drafts";

/**
 * The signed-out page forgets every unsent draft kept on this device (FT-11, FR-ATT-032): the
 * session may have ended without this tab's sign-out (idle timeout, "Lock now" or sign-out in
 * another tab, a revoked session), and the next person on a shared PC must not find it.
 */
export function ClearDeviceDrafts(): null {
  useEffect(() => {
    clearDeviceDrafts();
  }, []);
  return null;
}
