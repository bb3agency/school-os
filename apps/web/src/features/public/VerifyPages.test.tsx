import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PublicCertificate } from "@/server/public/verify";
import { VerifyBusy, VerifyForm, VerifyNotFound, VerifyResult } from "./VerifyPages";

/**
 * Certificate verification pages (ADR-0045 decisions 5-7; docs/17 §5.8; FR-CERT-021,
 * FR-CERT-022). Synthetic school and certificate only.
 */

const CODE = "7K4MQ2XP9HD3RT6W";
const base: PublicCertificate = {
  status: "valid",
  certificate_type: "transfer",
  title_en: "Transfer certificate",
  school_name: "Synthetic Model School",
  school_place: "Vijayawada",
  serial: "TC/2026-27/0001",
  issued_on: "2026-10-17",
  duplicate: null,
  cancelled_on: null,
  content: {
    certificate_type: "transfer",
    title_en: "Transfer certificate",
    title_te: "",
    serial: "TC/2026-27/0001",
    academic_year_label: "2026-27",
    issued_on: "2026-10-17",
    school_name_en: "Synthetic Model School",
    school_name_te: "",
    school_address_en: "",
    school_address_te: "",
    school_affiliation: "",
    school_place: "Vijayawada",
    student_name: "Synthetica Asha Tset",
    admission_no: "A-101",
    class_label_en: "Class IX A",
    class_label_te: null,
    fields: [
      {
        key: "full_name",
        label_en: "Name of the pupil",
        label_te: "",
        value: "Synthetica Asha Tset",
      },
      { key: "religion", label_en: "Religion", label_te: "", value: null },
    ],
    details: [],
    blanks: [],
  },
};

describe("verification pages", () => {
  it("the code box posts to /v/check and offers no other search", () => {
    const { container } = render(<VerifyForm />);
    const form = container.querySelector("form");
    expect(form?.getAttribute("method")).toBe("post");
    expect(form?.getAttribute("action")).toBe("/v/check");
    expect(screen.getByLabelText("Verification code")).toHaveAttribute("autocomplete", "off");
    expect(container.querySelectorAll("input")).toHaveLength(1);
    expect(container.textContent).toContain("not by name, date of birth or serial number");
  });

  it("an invalid code is marked on the field", () => {
    render(<VerifyForm invalid />);
    expect(screen.getByLabelText("Verification code")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("This is not a verification code");
  });

  it("a valid certificate shows the banner, the printed lines and blanks, nothing else", () => {
    const { container } = render(<VerifyResult certificate={base} code={CODE} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Valid: issued by Synthetic Model School on 17/10/2026",
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Transfer certificate");
    expect(container.textContent).toContain("7K4M-Q2XP-9HD3-RT6W");
    expect(container.textContent).toContain("Name of the pupil");
    expect(container.textContent).toContain("Left blank on the certificate");
    expect(container.textContent).toContain("This page shows only what the certificate prints.");
    expect(container.querySelector("script")).toBeNull();
  });

  it("a duplicate names its copy and the original serial", () => {
    render(
      <VerifyResult
        certificate={{
          ...base,
          duplicate: { copy_no: 2, original_serial: base.serial, issued_on: "2026-10-18" },
        }}
        code={CODE}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Duplicate copy 2 of serial TC/2026-27/0001, issued on 18/10/2026",
    );
  });

  it("a cancelled certificate shows the date and no printed lines", () => {
    const { container } = render(
      <VerifyResult
        certificate={{ ...base, status: "cancelled", cancelled_on: "2026-10-20", content: null }}
        code={CODE}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Cancelled on 20/10/2026");
    expect(container.textContent).not.toContain("Name of the pupil");
  });

  it("not found and busy name no school", () => {
    const missing = render(<VerifyNotFound />);
    expect(missing.container.textContent).toContain(
      "We could not find a certificate with this code",
    );
    expect(missing.container.textContent).toContain(
      "Check the code or contact the school that issued it.",
    );
    missing.unmount();
    const busy = render(<VerifyBusy />);
    expect(busy.container.textContent).not.toContain("Synthetic");
  });
});
