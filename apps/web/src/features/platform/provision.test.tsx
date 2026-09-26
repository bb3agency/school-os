import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { messages, renderWithIntl } from "@/test/render";
import { provisionSchoolAction } from "./provision-actions";
import { validateProvision, type ProvisionField } from "./provision-schema";
import { ProvisionSchoolForm } from "./ProvisionSchoolForm";

// Synthetic example values only.
const valid: Record<ProvisionField, string> = {
  schoolName: "Sample Public School",
  legalName: "Sample Education Society",
  stateCode: "37",
  billingEmail: "accounts@example.org",
  gstin: "37abcde1234f1z5",
  planKey: "standard-v1",
  deploymentMode: "shared",
  customDomain: "",
  ownerName: "Test Owner",
  ownerEmail: "owner@example.org",
};

describe("FR-PLT-001 provision validation (zod)", () => {
  it("accepts a complete shared-tier request and normalises GSTIN", () => {
    const result = validateProvision(valid);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data.gstin).toBe("37ABCDE1234F1Z5");
  });

  it("returns message keys for each invalid field", () => {
    const result = validateProvision({
      ...valid,
      schoolName: " ",
      stateCode: "AP",
      billingEmail: "not-an-email",
      gstin: "123",
      planKey: "",
      customDomain: "https://office.school.edu.in",
    });
    expect(result).toEqual({
      ok: false,
      errors: {
        schoolName: "required",
        stateCode: "invalidStateCode",
        billingEmail: "invalidEmail",
        gstin: "invalidGstin",
        planKey: "choosePlan",
        customDomain: "invalidDomain",
      },
    });
  });

  it("allows a custom domain only for dedicated deployments", () => {
    expect(validateProvision({ ...valid, customDomain: "office.school.edu.in" })).toEqual({
      ok: false,
      errors: { customDomain: "domainNeedsDedicated" },
    });
    expect(
      validateProvision({
        ...valid,
        deploymentMode: "dedicated",
        customDomain: "office.school.edu.in",
      }).ok,
    ).toBe(true);
  });

  it("the server action re-validates and does not pretend to provision", async () => {
    const form = new FormData();
    for (const [key, value] of Object.entries(valid)) form.set(key, value);
    await expect(provisionSchoolAction({ status: "idle" }, form)).resolves.toEqual({
      status: "not_connected",
    });
    form.set("ownerEmail", "nope");
    await expect(provisionSchoolAction({ status: "idle" }, form)).resolves.toEqual({
      status: "invalid",
      errors: { ownerEmail: "invalidEmail" },
    });
  });
});

describe("provision wizard", () => {
  it("validates the current step before moving on and summarises errors", async () => {
    const user = userEvent.setup();
    const m = messages.en.platform;
    renderWithIntl(
      <ProvisionSchoolForm plans={[{ key: "standard-v1", name: "Standard" }]} />,
      "en",
    );

    expect(
      screen.getByRole("heading", { level: 2, name: m.provision.steps.school }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: messages.en.common.next }));

    expect(screen.getByRole("alert")).toHaveTextContent("3 fields need attention");
    expect(screen.getByLabelText(m.provision.fields.schoolName)).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText(m.provision.fields.schoolName)).toHaveAccessibleDescription(
      `${m.provision.fields.schoolNameHint} ${m.validation.required}`,
    );

    await user.type(screen.getByLabelText(m.provision.fields.schoolName), valid.schoolName);
    await user.type(screen.getByLabelText(m.provision.fields.legalName), valid.legalName);
    await user.type(screen.getByLabelText(m.provision.fields.billingEmail), valid.billingEmail);
    await user.click(screen.getByRole("button", { name: messages.en.common.next }));

    const stepHeading = screen.getByRole("heading", { level: 2, name: m.provision.steps.plan });
    expect(stepHeading).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(
      m.provision.steps.plan,
    );
  });

  it("shows the review step with the entered values in Telugu", async () => {
    const user = userEvent.setup();
    const m = messages.te.platform;
    renderWithIntl(
      <ProvisionSchoolForm plans={[{ key: "standard-v1", name: "Standard" }]} />,
      "te",
    );
    await user.type(screen.getByLabelText(m.provision.fields.schoolName), valid.schoolName);
    await user.type(screen.getByLabelText(m.provision.fields.legalName), valid.legalName);
    await user.type(screen.getByLabelText(m.provision.fields.billingEmail), valid.billingEmail);
    await user.click(screen.getByRole("button", { name: messages.te.common.next }));
    await user.selectOptions(screen.getByLabelText(m.provision.fields.plan), "standard-v1");
    await user.click(screen.getByRole("button", { name: messages.te.common.next }));
    await user.type(screen.getByLabelText(m.provision.fields.ownerName), valid.ownerName);
    await user.type(screen.getByLabelText(m.provision.fields.ownerEmail), valid.ownerEmail);
    await user.click(screen.getByRole("button", { name: messages.te.common.next }));

    expect(screen.getByText(m.provision.reviewIntro)).toBeInTheDocument();
    const reviewed = screen.getAllByRole("definition").map((element) => element.textContent);
    expect(reviewed).toEqual(
      expect.arrayContaining([
        "Standard",
        messages.te.deploymentMode.shared,
        m.provision.notProvided,
      ]),
    );
    expect(screen.getByRole("button", { name: m.provision.submit })).toHaveAttribute(
      "type",
      "submit",
    );
  });
});
