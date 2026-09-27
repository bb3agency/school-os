// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { DEV_ROLES, DEV_SCHOOL_CODES, devSignInSchools, devSubject } from "./staff";

/**
 * The dev sign-in list must match what `make seed-synthetic` creates (apps/api/app/devtools/
 * plan.py, dataset v1 defaults). The web app never reads the database, so this reads the API's
 * versioned files as text and checks the same rule.
 */
const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
const read = (path: string) => readFileSync(resolve(repo, path), "utf8").replace(/\r\n/g, "\n");
const plan = read("apps/api/app/devtools/plan.py");
const rolesYaml = read("apps/api/app/authz/roles.yaml");
const academic = read("apps/api/app/tenancy/academic_defaults.yaml");

function constant(name: string): string {
  const match = new RegExp(`^${name}: Final = (.+)$`, "m").exec(plan);
  if (!match?.[1]) throw new Error(`${name} not found in plan.py`);
  return match[1].trim();
}

describe("dev sign-in subjects follow the synthetic seed plan", () => {
  it("uses the seed's default schools and subject pattern", () => {
    const tenants = Number(constant("DEFAULT_TENANTS"));
    const prefix = JSON.parse(constant("DEFAULT_CODE_PREFIX")) as string;
    const codes = Array.from({ length: tenants }, (_, i) => `${prefix}-${"abcdefgh"[i]}`);
    expect([...DEV_SCHOOL_CODES]).toEqual(codes);
    expect(plan).toContain('return f"synthetic|{code}|{role}|{ordinal}"');
    expect(devSubject("synth-a", "owner", 1)).toBe("synthetic|synth-a|owner|1");
  });

  it("lists every system role in roles.yaml order with its MFA requirement", () => {
    const block = rolesYaml.slice(
      rolesYaml.indexOf("\nroles:\n"),
      rolesYaml.indexOf("\nbreakglass_role:"),
    );
    const roles = [...block.matchAll(/^ {2}([a-z_]+):\n((?: {4}.*\n)+)/gm)].map((m) => ({
      role: m[1],
      mfaRequired: /^ {4}mfa_required: true$/m.test(m[2] ?? ""),
    }));
    expect(roles.length).toBeGreaterThan(5);
    expect(DEV_ROLES.map(({ role, mfaRequired }) => ({ role, mfaRequired }))).toEqual(roles);
  });

  it("uses the seed's staff counts (one class teacher per current-year section)", () => {
    const counts = Object.fromEntries(
      [...plan.matchAll(/^ {4}"([a-z_]+)": (\d+),$/gm)].map((m) => [m[1], Number(m[2])]),
    );
    const classes = [...academic.matchAll(/\{code: ([A-Z]+),/g)].map((m) => m[1]);
    const early = ["NUR", "LKG", "UKG"];
    expect(plan).toContain('EARLY_YEARS_CLASSES: Final = frozenset({"NUR", "LKG", "UKG"})');
    expect(plan).toContain('EARLY_YEARS_SECTIONS: Final = ("A", "B")');
    expect(plan).toContain('SCHOOL_SECTIONS: Final = ("A", "B", "C", "D")');
    const sections = classes.reduce((n, code) => n + (early.includes(code ?? "") ? 2 : 4), 0);
    for (const { role, count } of DEV_ROLES) {
      expect(count, role).toBe(role === "class_teacher" ? sections : (counts[role] ?? 1));
    }
  });

  it("shows the first person of each role per school", () => {
    const schools = devSignInSchools();
    expect(schools.map((s) => s.code)).toEqual(["synth-a", "synth-b"]);
    expect(schools[0]?.staff[0]).toEqual({
      role: "owner",
      subject: "synthetic|synth-a|owner|1",
      count: 1,
      mfaRequired: true,
    });
  });
});
