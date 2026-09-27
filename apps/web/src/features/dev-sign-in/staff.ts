import "server-only";

/**
 * Synthetic staff sign-in subjects for LOCAL DEVELOPMENT only (CLAUDE.md invariant 11).
 *
 * Mirrors the deterministic rule of `apps/api/app/devtools/plan.py` (dataset v1, default
 * `make seed-synthetic`): schools `synth-a`, `synth-b`; subject `synthetic|<code>|<role>|<n>`;
 * staff counts per role as `STAFF_COUNTS_V1`; one class teacher per current-year section.
 * Subjects are not secrets and nothing here reads the database. `staff.test.ts` checks this
 * file against plan.py, roles.yaml and academic_defaults.yaml so the two cannot drift.
 */

export const DEV_SCHOOL_CODES = ["synth-a", "synth-b"] as const;

export interface DevRole {
  role: string;
  /** How many seeded people hold the role in each school (ordinals 1..count). */
  count: number;
  /** roles.yaml `mfa_required` (the local stub asserts MFA for everyone). */
  mfaRequired: boolean;
}

/** Early-years classes (NUR, LKG, UKG) have sections A-B; classes I-XII have A-D. */
const CLASS_TEACHERS = 3 * 2 + 12 * 4;

/** In roles.yaml order. */
export const DEV_ROLES: readonly DevRole[] = [
  { role: "owner", count: 1, mfaRequired: true },
  { role: "principal", count: 1, mfaRequired: true },
  { role: "office_admin", count: 1, mfaRequired: true },
  { role: "office_staff", count: 3, mfaRequired: false },
  { role: "accountant", count: 1, mfaRequired: false },
  { role: "exam_coordinator", count: 1, mfaRequired: false },
  { role: "class_teacher", count: CLASS_TEACHERS, mfaRequired: false },
  { role: "teacher", count: 6, mfaRequired: false },
  { role: "auditor_readonly", count: 1, mfaRequired: false },
];

export function devSubject(code: string, role: string, ordinal: number): string {
  return `synthetic|${code}|${role}|${ordinal}`;
}

export interface DevSchool {
  code: string;
  staff: { role: string; subject: string; count: number; mfaRequired: boolean }[];
}

/** One row per role (the first person); the other ordinals follow the same pattern. */
export function devSignInSchools(): DevSchool[] {
  return DEV_SCHOOL_CODES.map((code) => ({
    code,
    staff: DEV_ROLES.map(({ role, count, mfaRequired }) => ({
      role,
      subject: devSubject(code, role, 1),
      count,
      mfaRequired,
    })),
  }));
}
