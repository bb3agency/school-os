import { describe, expect, it } from "vitest";
import { classLabel } from "./school-class";

// Synthetic class (NFR-I18N-001, US-202): the API stores an English and a Telugu name.
const CLASS_6 = { display_en: "Class 6", display_te: "6వ తరగతి" };

describe("classLabel (NFR-I18N-001)", () => {
  it("uses the Telugu name in Telugu and the English name otherwise", () => {
    expect(classLabel(CLASS_6, "te")).toBe("6వ తరగతి");
    expect(classLabel(CLASS_6, "en")).toBe("Class 6");
  });

  it("falls back to the English name when the Telugu one is empty", () => {
    expect(classLabel({ display_en: "LKG", display_te: "" }, "te")).toBe("LKG");
  });
});
