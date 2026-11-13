import fs from "node:fs";
import path from "node:path";

/**
 * WP-04: which message paths the code reachable from some entry files can ask for. Walks the
 * static and dynamic imports (relative and `@/`) from the entries and, in every file it
 * reaches, collects (see `keysIn`):
 *
 * - `useTranslations("ns.sub")` (and `getTranslations`, harmless on the server),
 * - `namespace="ns"`, `errorNamespace="ns"`, `namespace: "ns"` (components that build keys
 *   from a namespace prop, e.g. `ApiErrorAlert`),
 * - string or template literals that start with `ns.` (full keys for `useTranslations()`).
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

type Catalogue = Record<string, unknown>;

function hasPath(catalogue: Catalogue, dotted: string): boolean {
  let node: unknown = catalogue;
  for (const part of dotted.split(".")) {
    if (!node || typeof node !== "object" || !(part in node)) return false;
    node = (node as Catalogue)[part];
  }
  return true;
}

/** The longest leading part of `dotted` (whole segments) that exists in the catalogue. */
function existingPrefix(catalogue: Catalogue, dotted: string): string | null {
  const parts = dotted.split(".").filter(Boolean);
  for (let n = parts.length; n > 0; n--) {
    const candidate = parts.slice(0, n).join(".");
    if (hasPath(catalogue, candidate)) return candidate;
  }
  return null;
}

/**
 * WP-04 follow-up (PERF-013): the message paths (`ns` or `ns.sub.key`) a source file can ask
 * for, so an area may send part of a namespace (the shell needs `fees.nav`, not all of `fees`).
 *
 * - `useTranslations("a.b")`, `getTranslations("a.b")`, `namespace="a.b"`: the subtree `a.b`;
 *   a dynamic part (`a.${x}`) widens it to the fixed leading segments.
 * - A full-key literal `"a.b.c"` that exists in the catalogue: that key. A template or
 *   concatenated prefix (`"a.b.${x}"`, `"a.b." + x`) or an unknown key under a known subtree
 *   (`"a.b.typo"`): the deepest known subtree, the whole namespace at most for a template.
 *   A literal whose only known part is the namespace (`"attendance.read"`, a permission) is
 *   not a message key and is skipped.
 *
 * Over-counting is safe; missing one is not, and `app/english-only.test.tsx` checks the same at
 * render time.
 */
export function keysIn(source: string, catalogue: Catalogue): Set<string> {
  const text = codeOnly(source);
  const found = new Set<string>();
  const top = (path: string) => path.split(".")[0] as string;
  const namespaced = (raw: string, dynamic: boolean) => {
    if (!(top(raw) in catalogue)) return;
    const cut = dynamic ? raw.replace(/\.?[^.]*$/, "") || top(raw) : raw.replace(/\.$/, "");
    if (hasPath(catalogue, cut)) found.add(cut);
    else found.add(existingPrefix(catalogue, cut) ?? top(raw));
  };
  const call =
    /(?:[uU]seTranslations|getTranslations)\(\s*(["'`])([A-Za-z][\w.]*)(\$\{)?|[nN]amespace\s*[=:]\s*\{?\s*(["'`])([A-Za-z][\w.]*)(\$\{)?/g;
  for (const m of text.matchAll(call)) {
    const raw = (m[2] ?? m[5]) as string;
    namespaced(raw, Boolean(m[3] ?? m[6]));
  }
  for (const m of text.matchAll(/(["'`])([A-Za-z]+\.(?:[A-Za-z][\w.]*)?)(\$\{|\1)?/g)) {
    const raw = m[2] as string;
    if (!(top(raw) in catalogue)) continue;
    const open = m[3] === "${" || raw.endsWith(".");
    if (!open && hasPath(catalogue, raw)) {
      found.add(raw);
      continue;
    }
    const known = existingPrefix(catalogue, open ? raw.replace(/\.?[^.]*$/, "") : raw);
    if (known && known !== top(raw)) found.add(known);
    else if (open) found.add(top(raw));
  }
  return found;
}

/** The lists of what each area sends: they name message paths but ask for none. */
const MESSAGE_LISTS = new Set(
  ["client-messages.ts", "school-areas.ts"].map((name) => path.join(SRC, "i18n", name)),
);

export function keysFrom(entries: readonly string[], catalogue: Catalogue): Set<string> {
  const out = new Set<string>();
  for (const file of reachableFiles(entries)) {
    if (MESSAGE_LISTS.has(file)) continue;
    for (const key of keysIn(fs.readFileSync(file, "utf8"), catalogue)) out.add(key);
  }
  return out;
}

/** The paths in `used` that no entry of `declared` covers (equal to it or below it). */
export function uncovered(used: Iterable<string>, declared: readonly string[]): string[] {
  return [...used]
    .filter((key) => !declared.some((d) => key === d || key.startsWith(`${d}.`)))
    .sort();
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
