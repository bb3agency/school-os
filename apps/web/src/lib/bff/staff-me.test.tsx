import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { schoolDateFormat, setSchoolDateFormat } from "@/lib/date-format";
import { installBffStub, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { renderWithIntl } from "@/test/render";
import { me } from "@/test/school-fixtures";
import { useStaffMe } from "./staff-me";

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
});

afterEach(() => {
  uninstallBffStub();
  setSchoolDateFormat(null);
});

function Probe() {
  const current = useStaffMe();
  return current ? <p>loaded</p> : null;
}

describe("GET /me brings the school's display settings (FR-TEN-012)", () => {
  it("sets the school's date format for display dates", async () => {
    stub.routes["GET /bff/api/v1/me"] = () =>
      Response.json(
        me([], {
          settings: { idle_timeout_minutes: 10, date_format: "YYYY-MM-DD", languages: ["en"] },
        }),
      );
    renderWithIntl(<Probe />);
    await screen.findByText("loaded");
    expect(schoolDateFormat()).toBe("YYYY-MM-DD");
  });

  it("keeps the default when /me has no settings", async () => {
    const { settings: _unused, ...withoutSettings } = me([]);
    void _unused;
    stub.routes["GET /bff/api/v1/me"] = () => Response.json(withoutSettings);
    renderWithIntl(<Probe />);
    await screen.findByText("loaded");
    expect(schoolDateFormat()).toBe("DD/MM/YYYY");
  });
});
