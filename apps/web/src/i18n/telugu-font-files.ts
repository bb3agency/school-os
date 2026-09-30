import { readFile } from "node:fs/promises";
import path from "node:path";
import { TELUGU_FONT_FILES } from "./telugu-font";

/** Server only (route handler): reads the Telugu font files (ADR-0036). */

const PACKAGE_FILES = path.join("node_modules", "@fontsource", "noto-sans-telugu", "files");

/**
 * One font file's bytes. The package is hoisted to the repository root (npm workspaces);
 * `next dev` runs in apps/web and the standalone server chdirs to apps/web too, so both find
 * it two levels up. `outputFileTracingIncludes` (next.config.ts) copies the files into the
 * standalone bundle; the paths are not traced (turbopackIgnore), or the whole project would be.
 */
export async function readTeluguFont(file: string): Promise<Uint8Array<ArrayBuffer> | null> {
  if (!TELUGU_FONT_FILES.includes(file)) return null;
  for (const root of [
    path.resolve(/*turbopackIgnore: true*/ "..", ".."),
    path.resolve(/*turbopackIgnore: true*/ "."),
  ]) {
    try {
      return new Uint8Array(await readFile(path.join(root, PACKAGE_FILES, file)));
    } catch {
      // try the next place
    }
  }
  return null;
}
