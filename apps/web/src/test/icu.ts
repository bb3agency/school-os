/**
 * Minimal ICU MessageFormat argument extractor for the i18n parity test.
 * Returns "name:type" signatures, e.g. ["count:plural", "page:string"], including
 * arguments nested inside plural/select branches, plus the branch selectors used.
 */
export interface IcuSignature {
  args: string[];
  selectors: string[];
}

const BRANCHING = new Set(["plural", "select", "selectordinal"]);

export function icuSignature(message: string): IcuSignature {
  const args = new Set<string>();
  const selectors = new Set<string>();
  parseMessage(message, 0, false, args, selectors);
  return { args: [...args].sort(), selectors: [...selectors].sort() };
}

/** Parse message text until an unmatched "}" (nested) or the end. Returns the index after it. */
function parseMessage(
  source: string,
  start: number,
  nested: boolean,
  args: Set<string>,
  selectors: Set<string>,
): number {
  let i = start;
  while (i < source.length) {
    const char = source[i];
    if (char === "'") {
      const next = source[i + 1];
      if (next === "'") {
        i += 2;
        continue;
      }
      if (next === "{" || next === "}" || next === "#" || next === "|") {
        const end = source.indexOf("'", i + 1);
        if (end === -1) throw new Error(`Unterminated quote in: ${source}`);
        i = end + 1;
        continue;
      }
      i += 1;
      continue;
    }
    if (char === "{") {
      i = parseArgument(source, i + 1, args, selectors);
      continue;
    }
    if (char === "}") {
      if (!nested) throw new Error(`Unbalanced "}" in: ${source}`);
      return i + 1;
    }
    i += 1;
  }
  if (nested) throw new Error(`Unterminated branch in: ${source}`);
  return i;
}

function parseArgument(
  source: string,
  start: number,
  args: Set<string>,
  selectors: Set<string>,
): number {
  let i = start;
  const readUntil = (stops: string) => {
    const from = i;
    while (i < source.length && !stops.includes(source[i] ?? "")) i += 1;
    if (i >= source.length) throw new Error(`Unterminated argument in: ${source}`);
    return source.slice(from, i).trim();
  };

  const name = readUntil(",}");
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) throw new Error(`Bad argument name "${name}"`);
  if (source[i] === "}") {
    args.add(`${name}:string`);
    return i + 1;
  }
  i += 1; // skip ","
  const type = readUntil(",}");
  args.add(`${name}:${type}`);
  if (source[i] === "}") return i + 1;
  i += 1; // skip ","

  if (!BRANCHING.has(type)) {
    // number/date/time style: skip to the matching "}".
    let depth = 1;
    while (i < source.length && depth > 0) {
      if (source[i] === "{") depth += 1;
      if (source[i] === "}") depth -= 1;
      i += 1;
    }
    return i;
  }

  // Branches: `selector {message}` pairs, optional `offset:n`.
  while (i < source.length) {
    while (/\s/.test(source[i] ?? "")) i += 1;
    if (source[i] === "}") return i + 1;
    const selector = readUntil("{").trim();
    if (!selector.startsWith("offset:")) selectors.add(`${name}:${selector}`);
    i = parseMessage(source, i + 1, true, args, selectors);
  }
  throw new Error(`Unterminated ${type} in: ${source}`);
}

/** Flatten nested message objects to dotted keys. */
export function flattenMessages(tree: unknown, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  if (typeof tree !== "object" || tree === null) return out;
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out.set(path, value);
    else for (const [k, v] of flattenMessages(value, path)) out.set(k, v);
  }
  return out;
}
