import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { schoolDateFormat, setSchoolDateFormat } from "@/lib/date-format";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { useStaffMe } from "./me";

/**
 * The student, import and extraction screens read GET /me through their own hook. It shares
 * the query key with the shell's hook, so whichever fetches first fills the cache for both:
 * the school's date format must be applied either way (FR-TEN-012; WD-05).
 */

function Probe() {
  const staff = useStaffMe();
  return <p>{staff ? `loaded ${schoolDateFormat()}` : "loading"}</p>;
}

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("staff");
  setSchoolDateFormat(null);
});
afterEach(() => {
  uninstallBffStub();
  setSchoolDateFormat(null);
});

describe("students' GET /me (WD-05, FR-TEN-012)", () => {
  it("applies the school's date format when this hook is the first to read /me", async () => {
    const base = me(["student.read_basic"]);
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json({ ...base, settings: { ...base.settings, date_format: "YYYY-MM-DD" } });
    renderWithIntl(<Probe />);
    expect(await screen.findByText("loaded YYYY-MM-DD")).toBeVisible();
    expect(schoolDateFormat()).toBe("YYYY-MM-DD");
  });
});
