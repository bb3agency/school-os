#!/usr/bin/env node
// Generate TypeScript types from the API's OpenAPI document (openapi-typescript).
// Input:  apps/api/openapi.json (override with OPENAPI_SPEC=/path/to/openapi.json)
// Output: packages/api-client/src/generated/schema.ts
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(here, "..");
const specPath = resolve(
  process.env.OPENAPI_SPEC ?? resolve(packageRoot, "../../apps/api/openapi.json"),
);
const outFile = resolve(packageRoot, "src/generated/schema.ts");

if (!existsSync(specPath)) {
  console.error(
    [
      `[api-client] OpenAPI document not found: ${specPath}`,
      "Export it from the API first so apps/api/openapi.json exists,",
      "or point OPENAPI_SPEC at an existing file, then run `npm run generate -w @schoolos/api-client` again.",
    ].join("\n"),
  );
  process.exit(1);
}

const { default: openapiTS, astToString } = await import("openapi-typescript");

try {
  const ast = await openapiTS(pathToFileURL(specPath), {
    alphabetize: true,
    exportType: false,
    immutable: false,
  });
  const header = [
    "/* eslint-disable */",
    "// GENERATED FILE: do not edit by hand.",
    `// Source: ${relative(packageRoot, specPath).split(sep).join("/")} via openapi-typescript.`,
    "",
  ].join("\n");
  mkdirSync(dirname(outFile), { recursive: true });
  writeFileSync(outFile, header + astToString(ast));
  console.log(`[api-client] Wrote ${relative(process.cwd(), outFile)}`);
} catch (error) {
  console.error(`[api-client] Could not generate types from ${specPath}:`);
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
