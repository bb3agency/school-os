import { startStandIns } from "./stand-in";

/** E2E_STAND_IN=1: start the scripted IdP and canned API; stop them after the run. */
export default async function globalSetup(): Promise<() => Promise<void>> {
  return startStandIns();
}
