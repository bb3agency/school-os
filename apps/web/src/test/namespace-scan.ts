import fs from "node:fs";
import path from "node:path";

/**
 * WP-04: which top-level message namespaces the code reachable from some entry files can ask
 * for. Walks the static and dynamic imports (relative and `@/`) from the entries and, in every
 * file it reaches, collects:
 *
 * - `useTranslations("ns...")` (and `getTranslations`, harmless on the server),
 * - `namespace="ns"`, `errorNamespace="ns"`, `namespace: "ns"` (components that build keys
 *   from a namespace prop, e.g. `ApiErrorAlert`),
 * - any string or template literal that starts with `ns.` (full keys for `useTranslations()`).
 *
 * `ns` must be a top-level key of the catalogue. Over-counting is safe (a few more strings in
 * the page); missing one is not, so the scan errs on the wide side.
 */

const SRC = path.resolve(import.meta.dirname, "..");
const EXTENSIONS = [".tsx", ".ts", "/index.tsx", "/index.ts"];
const IMPORT =
  /(?:import|export)\s+(?!type\b)[^;]*?from\s+["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/g;

function resolve(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = path.join(SRC, spec.slice(2));
  else if (spec.startsWith(".")) base = path.resolve(path.dirname(from), spec);
  else return null;
  if (/\.(tsx?|json|css)$/.test(base))
    return fs.existsSync(base) && /\.tsx?$/.test(base) ? base : null;
  for (const ext of EXTENSIONS) {
    if (fs.existsSync(base + ext)) return base + ext;
  }
  return null;
}

export function reachableFiles(entries: readonly string[]): Set<string> {
  const seen = new Set<string>();
  const queue = [...entries];
  while (queue.length > 0) {
    const file = queue.pop() as string;
    if (seen.has(file)) continue;
    seen.add(file);
    const text = fs.readFileSync(file, "utf8");
    for (const match of text.matchAll(IMPORT)) {
      const target = resolve(file, (match[1] ?? match[2]) as string);
      if (target && !seen.has(target)) queue.push(target);
    }
  }
  return seen;
}

/** The code without comments and without string members of type unions (`| "ask"`). */
function codeOnly(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|\s)\/\/.*$/gm, "$1")
    .replace(/\|\s*"[^"\n]*"/g, "|");
}

export function namespacesIn(source: string, topLevel: ReadonlySet<string>): Set<string> {
  const text = codeOnly(source);
  const found = new Set<string>();
  const add = (name: string | undefined) => {
    if (name && topLevel.has(name)) found.add(name);
  };
  for (const m of text.matchAll(/[uU]seTranslations\(\s*["'`]([A-Za-z]+)/g)) add(m[1]);
  for (const m of text.matchAll(/getTranslations\(\s*["'`]([A-Za-z]+)/g)) add(m[1]);
  for (const m of text.matchAll(/[nN]amespace\s*[=:]\s*\{?\s*["'`]([A-Za-z]+)/g)) add(m[1]);
  for (const m of text.matchAll(/["'`]([A-Za-z]+)\.[A-Za-z$]/g)) add(m[1]);
  return found;
}

export function namespacesFrom(
  entries: readonly string[],
  topLevel: ReadonlySet<string>,
): Set<string> {
  const out = new Set<string>();
  for (const file of reachableFiles(entries)) {
    for (const name of namespacesIn(fs.readFileSync(file, "utf8"), topLevel)) out.add(name);
  }
  return out;
}

/** Every non-test source file under a directory of `src`. */
export function filesUnder(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(full);
    }
  };
  walk(path.join(SRC, dir));
  return out;
}

export const srcPath = (...parts: string[]) => path.join(SRC, ...parts);
