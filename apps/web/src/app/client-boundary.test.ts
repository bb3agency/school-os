// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Server Components (pages, layouts) may import only components from "use client" modules:
 * calling a plain function exported by a client module from the server fails at run time
 * ("Attempted to call X() from the server"). Found once by the live e2e run; kept as a guard.
 */

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "..");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

function resolveModule(specifier: string, from: string): string | null {
  const base = specifier.startsWith("@/")
    ? join(src, specifier.slice(2))
    : specifier.startsWith(".")
      ? resolve(dirname(from), specifier)
      : null;
  if (!base) return null;
  for (const candidate of [`${base}.tsx`, `${base}.ts`, join(base, "index.ts")]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // try the next one
    }
  }
  return null;
}

const IMPORT = /import\s+(type\s+)?\{([^}]*)\}\s+from\s+"([^"]+)"/g;

describe("server/client boundary", () => {
  it("pages and layouts import only components (or types) from client modules", () => {
    const offenders: string[] = [];
    const serverFiles = files(join(src, "app")).filter((file) =>
      /[\\/](page|layout|not-found)\.tsx$/.test(file),
    );
    expect(serverFiles.length).toBeGreaterThan(20);
    for (const file of serverFiles) {
      const text = readFileSync(file, "utf8");
      if (text.startsWith('"use client"')) continue;
      for (const match of text.matchAll(IMPORT)) {
        const [, typeOnly, names = "", specifier = ""] = match;
        if (typeOnly) continue;
        const target = resolveModule(specifier, file);
        if (!target || !readFileSync(target, "utf8").startsWith('"use client"')) continue;
        for (const raw of names.split(",")) {
          const name = raw.trim().replace(/^type\s+/, "");
          if (!name || raw.trim().startsWith("type ")) continue;
          if (!/^[A-Z]/.test(name)) offenders.push(`${file.slice(src.length)}: ${name}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("only server code reads the Telugu switch; client code asks LanguagesProvider (ADR-0036)", () => {
    const readers =
      /from\s+"(@\/i18n\/languages|\.\/languages|@\/i18n\/messages|@\/i18n\/telugu-font(?:-files)?)"/;
    const offenders = files(src)
      .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file))
      .filter((file) => {
        const text = readFileSync(file, "utf8");
        return text.startsWith('"use client"') && readers.test(text);
      })
      .map((file) => file.slice(src.length));
    expect(offenders).toEqual([]);
    const direct = files(src)
      .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file))
      .filter((file) => !file.endsWith(join("i18n", "languages.ts")))
      .filter((file) => readFileSync(file, "utf8").includes("SOS_TELUGU_ENABLED"))
      .map((file) => file.slice(src.length));
    // Comments may name the variable; only languages.ts reads it.
    for (const file of direct) {
      expect(readFileSync(join(src, file), "utf8"), file).not.toMatch(
        /process\.env(\.SOS_TELUGU_ENABLED|\[["']SOS_TELUGU_ENABLED)/,
      );
    }
  });
});
