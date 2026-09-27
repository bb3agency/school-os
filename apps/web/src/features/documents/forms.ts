import { z } from "zod";
import { containsAadhaarNumber } from "@/lib/aadhaar";
import { ApiError } from "@/lib/bff/query";
import { optionalText, text } from "@/lib/validation";
import { aclField, refineAcl, visibilityField } from "./parts";
import {
  DOC_LANGUAGES,
  DOC_TYPES,
  GENERAL_DOC_TYPES,
  MAX_ISSUER,
  MAX_TITLE,
  SENSITIVITIES,
  type DocLanguage,
  type DocType,
  type DocumentRow,
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

/**
 * Edit a document's details (PATCH /documents/{id}; `DocumentUpdate`). Title and type are
 * required; language, issuer and date may be emptied (sent as `null`). The type list offered
 * suits the purpose; the API checks again. A full Aadhaar number is refused in the title and
 * issuer (invariant 4).
 */
export const documentEditSchema = z
  .object({
    title: text(MAX_TITLE),
    doc_type: z.enum(DOC_TYPES, { error: "chooseOption" }),
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
  })
  .superRefine((data, context) => {
    if (containsAadhaarNumber(data.title)) {
      context.addIssue({ code: "custom", path: ["title"], message: "noAadhaar" });
    }
    if (data.issuer !== null && containsAadhaarNumber(data.issuer)) {
      context.addIssue({ code: "custom", path: ["issuer"], message: "noAadhaar" });
    }
  });
export type DocumentEditInput = z.output<typeof documentEditSchema>;

/** PATCH body with only what changed (the API ignores unchanged values anyway). */
export function documentPatchBody(
  current: Pick<DocumentRow, "title" | "doc_type" | "language" | "issuer" | "issued_on">,
  data: DocumentEditInput,
) {
  const body: {
    title?: string;
    doc_type?: DocType;
    language?: DocLanguage | null;
    issuer?: string | null;
    issued_on?: string | null;
  } = {};
  if (data.title !== current.title) body.title = data.title;
  if (data.doc_type !== current.doc_type) body.doc_type = data.doc_type;
  if (data.language !== current.language) body.language = data.language;
  if (data.issuer !== current.issuer) body.issuer = data.issuer;
  if (data.issued_on !== current.issued_on) body.issued_on = data.issued_on;
  return body;
}

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
