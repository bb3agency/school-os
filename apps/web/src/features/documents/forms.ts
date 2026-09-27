import { z } from "zod";
import { ApiError } from "@/lib/bff/query";
import { optionalText, text } from "@/lib/validation";
import { aclField, refineAcl, visibilityField } from "./parts";
import {
  DOC_LANGUAGES,
  GENERAL_DOC_TYPES,
  MAX_ISSUER,
  MAX_TITLE,
  SENSITIVITIES,
  type Purpose,
} from "./types";
import { checkFile, StorageUploadError } from "./upload";

/**
 * zod schemas of the document forms. Messages are keys under `validation.*`; the server checks
 * everything again (file content, ACL references, scoped uploads).
 */

/** The chosen file (from `extra`), checked for the purpose's types and the 25 MB limit. */
export function fileField(purpose: Purpose) {
  return z.custom<File>().superRefine((value, context) => {
    const problem = checkFile(value instanceof File ? value : null, purpose);
    if (problem) context.addIssue({ code: "custom", message: problem });
  });
}

/** The file input's first file, for `useApiForm`'s `extra`. */
export function chosenFile(form: HTMLFormElement): { file: File | undefined } {
  const input = form.elements.namedItem("file");
  return { file: input instanceof HTMLInputElement ? (input.files?.[0] ?? undefined) : undefined };
}

export const newDocumentSchema = z
  .object({
    file: fileField("other"),
    title: text(MAX_TITLE),
    doc_type: z.enum(GENERAL_DOC_TYPES, { error: "chooseOption" }),
    sensitivity: z.enum(SENSITIVITIES, { error: "chooseOption" }),
    language: z
      .union([z.enum(DOC_LANGUAGES), z.literal("")], { error: "chooseOption" })
      .transform((value) => (value === "" ? null : value)),
    issuer: optionalText(MAX_ISSUER),
    issued_on: z
      .string()
      .trim()
      .refine((value) => value === "" || /^\d{4}-\d{2}-\d{2}$/.test(value), {
        error: "invalidIssueDate",
      })
      .transform((value) => (value === "" ? null : value)),
    visibility: visibilityField,
    acl: aclField,
  })
  .superRefine(refineAcl);

export const aclSchema = z
  .object({ visibility: visibilityField, acl: aclField })
  .superRefine(refineAcl);

export function newVersionSchema(purpose: Purpose) {
  return z.object({ file: fileField(purpose) });
}

/**
 * Storage refused the file (for example the presigned form expired on a slow link): shown on
 * the file field ("upload it again") like a server validation error, so the form keeps the
 * other answers and the next try asks for a fresh upload form.
 */
export function storageFailure(failure: unknown): unknown {
  if (!(failure instanceof StorageUploadError)) return failure;
  return new ApiError(422, "validation_error", {
    errors: [{ field: "file", code: "upload_failed", message_key: "errors.upload_failed" }],
  });
}
