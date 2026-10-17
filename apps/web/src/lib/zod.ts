/**
 * The zod builders the web app uses, re-exported by name (audit 2026-10-17 WEB-10).
 *
 * `import { z } from "zod"` hands the bundler zod's whole namespace object, which Turbopack does
 * not tree-shake, so every page that validated a form shipped all of zod's 45+ locale bundles
 * and its JSON-schema converters to the browser. Named re-exports let the bundler keep only
 * these. Use it as `import * as z from "@/lib/zod"`; the API is zod's own, unchanged (English
 * messages are still registered by zod on the first schema it builds). Add a name here when a
 * screen needs another builder; never import "zod" directly in app code (lint enforces it).
 */
export {
  array,
  boolean,
  coerce,
  custom,
  enum,
  iso,
  literal,
  number,
  object,
  record,
  string,
  undefined,
  union,
  ZodError,
} from "zod";
export type { infer, output, RefinementCtx, ZodType } from "zod";
