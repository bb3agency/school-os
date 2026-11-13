import { describe, expect, it } from "vitest";
import { documentIdFrom } from "./gaps";

const ID = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";

describe("knowledge gaps (FA-06)", () => {
  it("reads the document id from a pasted page link or a bare id", () => {
    expect(documentIdFrom(`https://school.example.test/en/documents/${ID}?tab=versions`)).toBe(ID);
    expect(documentIdFrom(`  ${ID.toUpperCase()}  `)).toBe(ID);
    expect(documentIdFrom("fee circular")).toBeNull();
  });
});
