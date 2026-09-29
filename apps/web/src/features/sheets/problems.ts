import { ApiError } from "@/lib/bff/query";
import { StepUpCancelledError } from "@/lib/bff/step-up";

/**
 * The code that explains a failed sheet call best: for a 422 the first field error's code
 * (`aadhaar_full_number_rejected`, `column_restricted` …), else the problem's own code. The
 * API never echoes cell values in errors, and neither do the messages (sheets.errors.*).
 */
export function sheetErrorCode(error: unknown): string | undefined {
  if (error instanceof StepUpCancelledError) return "step_up_cancelled";
  if (!(error instanceof ApiError)) return undefined;
  const fields = error.problem.errors;
  if (error.status === 422 && Array.isArray(fields)) {
    const first = fields.find((item) => typeof item?.code === "string");
    if (first) return first.code;
  }
  if (error.status === 412) return "precondition_failed";
  return error.code;
}

/** An error that carries only a code, for <ProblemAlert> (never raw API text). */
export class SheetProblem extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "SheetProblem";
  }
}

/** Normalise any failure into something <ProblemAlert namespace="sheets.errors"> explains. */
export function sheetProblem(error: unknown): unknown {
  const code = sheetErrorCode(error);
  if (code && !(error instanceof ApiError && code === error.code)) return new SheetProblem(code);
  return error;
}
