/**
 * Which Chromium the e2e run launches (playwright.config.ts, e2e/audit/audit.config.ts).
 *
 * CI installs the build locked in package-lock.json (`playwright install --with-deps chromium`)
 * and never falls back. Locally, PW_CHROMIUM_PATH wins; otherwise, when the locked build is not
 * installed (for example a sandbox where the browser download is blocked but an older set is
 * preinstalled under PLAYWRIGHT_BROWSERS_PATH), the newest preinstalled `chromium_headless_shell`
 * is used. The headless shell rather than full Chrome: in full Chrome, Tab past the last control
 * moves focus into the browser's own UI and the keyboard checks fail for the wrong reason.
 */
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { chromium, type LaunchOptions } from "@playwright/test";

const SHELL_DIR = /^chromium_headless_shell-(\d+)$/;

function lockedBuildInstalled(): boolean {
  try {
    return existsSync(chromium.executablePath());
  } catch {
    return false;
  }
}

function newestPreinstalledShell(root: string): string | undefined {
  if (!existsSync(root)) return undefined;
  const newest = readdirSync(root)
    .map((name) => ({ name, revision: Number(SHELL_DIR.exec(name)?.[1] ?? Number.NaN) }))
    .filter((entry) => Number.isFinite(entry.revision))
    .sort((a, b) => b.revision - a.revision)[0];
  if (!newest) return undefined;
  const binary = path.join(root, newest.name, "chrome-linux", "headless_shell");
  return existsSync(binary) ? binary : undefined;
}

export function chromiumLaunchOptions(): LaunchOptions {
  const explicit = process.env.PW_CHROMIUM_PATH;
  if (explicit) return { executablePath: explicit };
  if (process.env.CI || lockedBuildInstalled()) return {};
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH;
  const fallback = root ? newestPreinstalledShell(root) : undefined;
  return fallback ? { executablePath: fallback } : {};
}
