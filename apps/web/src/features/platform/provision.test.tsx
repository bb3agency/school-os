import { screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { messages, renderWithIntl } from "@/test/render";
import {
  CSRF,
  installBffStub,
  page,
  problem,
  uninstallBffStub,
  type BffStub,
} from "@/test/bff-stub";
import { provisionSchema } from "./provision-schema";
import { ProvisionSchoolForm } from "./ProvisionSchoolForm";

// Synthetic example values only.
const PLAN_SHARED = {
  id: "0192f3a4-0000-7000-8000-00000000a001",
  code: "standard",
  name: "Standard",
  version: 1,
  tier: "shared",
  status: "published",
  billing_period: "monthly",
  pricing_model: "flat",
  base_price_inr: "4999.00",
  per_student_price_inr: null,
  included_students: null,
  gst_rate: "18.00",
  sac_code: "998314",
  trial_days: 30,
  limits: {},
  features: {},
  published_at: "2026-06-01T00:00:00Z",
  created_at: "2026-06-01T00:00:00Z",
};
const PLAN_DEDICATED = {
  ...PLAN_SHARED,
  id: "0192f3a4-0000-7000-8000-00000000a002",
  code: "dedicated",
  name: "Dedicated",
  tier: "dedicated",
};

const valid: Record<string, string> = {
  school_name: "Sample Public School",
  code: "sample-public",
  boards: "cbse, state_ap",
  tier: "shared",
  custom_domain: "",
  "owner.display_name": "Test Owner",
  "owner.email": "owner@example.org",
  "owner.idp_subject": "owner-sub-1",
  "owner.language": "te",
  plan_id: PLAN_SHARED.id,
  start_as: "trial",
  price_override_inr: "",
  override_reason: "",
  "billing_account.legal_name": "Sample Education Society",
  "billing_account.gstin": "37abcde1234f1z5",
  "billing_account.pan": "",
  "billing_account.billing_email": "Accounts@Example.org",
  "billing_account.billing_contact_name": "",
  "billing_account.billing_phone": "",
  "billing_account.address_line1": "1 Main Road",
  "billing_account.address_line2": "",
  "billing_account.city": "Vijayawada",
  "billing_account.district": "",
  "billing_account.postal_code": "520001",
  "billing_account.state_code": "37",
  "billing_account.po_reference": "",
};

describe("FR-PLT-001 provision schema mirrors the API's ProvisionIn", () => {
  it("builds the API body: nested owner and billing account, normalised GSTIN and boards", () => {
    const result = provisionSchema.safeParse(valid);
    expect(result.success).toBe(true);
    if (!result.success) return;
    expect(result.data).toMatchObject({
      code: "sample-public",
      boards: ["CBSE", "STATE_AP"],
      tier: "shared",
      custom_domain: null,
      plan_id: PLAN_SHARED.id,
      owner: { display_name: "Test Owner", idp_subject: "owner-sub-1", language: "te" },
      billing_account: {
        gstin: "37ABCDE1234F1Z5",
        billing_email: "accounts@example.org",
        state_code: "37",
        pan: null,
      },
    });
  });

  it("reports GST, domain, owner and price rules with message keys", () => {
    const result = provisionSchema.safeParse({
      ...valid,
      custom_domain: "office.school.edu.in",
      "owner.idp_subject": "",
      price_override_inr: "3999",
      "billing_account.gstin": "36ABCDE1234F1Z5",
      "billing_account.pan": "ZZZZZ9999Z",
      code: "Bad Code",
    });
    expect(result.success).toBe(false);
    if (result.success) return;
    const byField = Object.fromEntries(
      result.error.issues.map((issue) => [issue.path.join("."), issue.message]),
    );
    expect(byField).toMatchObject({
      code: "invalidCode",
      custom_domain: "domainNeedsDedicated",
      "owner.idp_subject": "required",
      override_reason: "priceNeedsReason",
      "billing_account.gstin": "gstinStateMismatch",
      "billing_account.pan": "gstinPanMismatch",
    });
  });

  it("allows a dedicated school without an owner", () => {
    const result = provisionSchema.safeParse({
      ...valid,
      tier: "dedicated",
      custom_domain: "Office.School.edu.in",
      "owner.display_name": "",
      "owner.email": "",
      "owner.idp_subject": "",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.owner).toBeNull();
      expect(result.data.custom_domain).toBe("office.school.edu.in");
    }
  });
});

let stub: BffStub;
beforeEach(() => {
  stub = installBffStub("operator");
  stub.routes["GET /bff/api/v1/platform/plans"] = () => page([PLAN_SHARED, PLAN_DEDICATED]);
});
afterEach(uninstallBffStub);

const m = messages.en.platform.provision;
const ba = messages.en.platform.billingAccount;
const next = () => screen.getByRole("button", { name: messages.en.common.next });

async function fillToReview(user: UserEvent, tier: "shared" | "dedicated") {
  await user.type(screen.getByLabelText(m.fields.schoolName), valid.school_name as string);
  await user.type(screen.getByLabelText(m.fields.code), valid.code as string);
  await user.click(next());
  if (tier === "dedicated") {
    await user.click(screen.getByLabelText(messages.en.deploymentMode.dedicated));
    await user.type(screen.getByLabelText(m.fields.customDomain), "office.school.edu.in");
  }
  await user.click(next());
  if (tier === "shared") {
    await user.type(screen.getByLabelText(m.fields.ownerName), "Test Owner");
    await user.type(screen.getByLabelText(m.fields.ownerSubject), "owner-sub-1");
  }
  await user.click(next());
  await waitFor(() =>
    expect(within(screen.getByLabelText(m.fields.plan)).getAllByRole("option")).toHaveLength(2),
  );
  await user.selectOptions(
    screen.getByLabelText(m.fields.plan),
    tier === "shared" ? PLAN_SHARED.id : PLAN_DEDICATED.id,
  );
  await user.click(next());
  await user.type(screen.getByLabelText(ba.legalName), "Sample Education Society");
  await user.type(screen.getByLabelText(ba.gstin), "37ABCDE1234F1Z5");
  await user.type(screen.getByLabelText(ba.billingEmail), "accounts@example.org");
  await user.type(screen.getByLabelText(ba.addressLine1), "1 Main Road");
  await user.type(screen.getByLabelText(ba.city), "Vijayawada");
  await user.type(screen.getByLabelText(ba.postalCode), "520001");
  await user.click(next());
  expect(screen.getByRole("heading", { level: 2, name: m.steps.review })).toHaveFocus();
}

describe("provision wizard (FR-PLT-001..003)", () => {
  it("validates the current step before moving on and summarises errors", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");

    expect(screen.getByRole("heading", { level: 2, name: m.steps.school })).toBeInTheDocument();
    await user.click(next());

    expect(screen.getByRole("alert")).toHaveTextContent("2 fields need attention");
    const name = screen.getByLabelText(m.fields.schoolName);
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription(
      `${m.fields.schoolNameHint} ${messages.en.validation.required}`,
    );

    await user.type(name, "Sample Public School");
    await user.type(screen.getByLabelText(m.fields.code), "sample-public");
    await user.click(next());
    expect(screen.getByRole("heading", { level: 2, name: m.steps.deployment })).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(m.steps.deployment);
  });

  it("provisions a shared school with one Idempotency-Key, CSRF, and shows the result", async () => {
    stub.routes["POST /bff/api/v1/platform/tenants"] = () =>
      Response.json(
        {
          tenant_id: "0192f3a4-0000-7000-8000-000000000001",
          deployment_id: "0192f3a4-0000-7000-8000-000000000002",
          subscription_id: "0192f3a4-0000-7000-8000-000000000003",
          billing_account_id: "0192f3a4-0000-7000-8000-000000000004",
          tier: "shared",
          tenant_status: "provisioning",
          owner_invite: "created",
          heartbeat_key: null,
          heartbeat_key_id: null,
        },
        { status: 201 },
      );
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");
    await fillToReview(user, "shared");
    await user.click(screen.getByRole("button", { name: m.submit }));

    expect(await screen.findByText(m.doneTitle)).toBeInTheDocument();
    expect(screen.getByText(m.ownerInvite.created)).toBeInTheDocument();
    const [call] = stub.callsTo("POST /bff/api/v1/platform/tenants");
    expect(call?.headers.get("x-csrf-token")).toBe(CSRF);
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    const body = JSON.parse(call?.body ?? "{}") as Record<string, unknown>;
    expect(body).toMatchObject({
      code: "sample-public",
      tier: "shared",
      plan_id: PLAN_SHARED.id,
      owner: { display_name: "Test Owner", idp_subject: "owner-sub-1" },
      billing_account: { gstin: "37ABCDE1234F1Z5", state_code: "37", postal_code: "520001" },
    });
    expect(screen.getByRole("link", { name: m.openSchool })).toHaveAttribute(
      "href",
      "/en/platform/schools/0192f3a4-0000-7000-8000-000000000001",
    );
  });

  it("shows a dedicated host's heartbeat key once, then drops it after confirmation", async () => {
    stub.routes["POST /bff/api/v1/platform/tenants"] = () =>
      Response.json(
        {
          tenant_id: "0192f3a4-0000-7000-8000-000000000011",
          deployment_id: "0192f3a4-0000-7000-8000-000000000012",
          subscription_id: "0192f3a4-0000-7000-8000-000000000013",
          billing_account_id: "0192f3a4-0000-7000-8000-000000000014",
          tier: "dedicated",
          tenant_status: "provisioning",
          owner_invite: "not_applicable",
          heartbeat_key: "hb-secret-synthetic-0001",
          heartbeat_key_id: "hk_1",
        },
        { status: 201 },
      );
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");
    await fillToReview(user, "dedicated");
    await user.click(screen.getByRole("button", { name: m.submit }));

    const key = await screen.findByLabelText(m.heartbeatKey);
    expect(key).toHaveValue("hb-secret-synthetic-0001");
    expect(key).toHaveAttribute("readonly");
    const done = screen.getByRole("button", { name: messages.en.common.continue });
    expect(done).toBeDisabled();
    await user.click(screen.getByLabelText(messages.en.common.secret.stored));
    await user.click(done);

    expect(await screen.findByText(m.doneDedicated)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("hb-secret-synthetic-0001");
  });

  it("puts a server field error on its input and goes back to that step", async () => {
    stub.routes["POST /bff/api/v1/platform/tenants"] = () =>
      problem(422, "validation_error", {
        errors: [
          {
            field: "billing_account.gstin",
            code: "string_pattern_mismatch",
            message_key: "errors.string_pattern_mismatch",
          },
        ],
      });
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");
    await fillToReview(user, "shared");
    await user.click(screen.getByRole("button", { name: m.submit }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: m.steps.billing })).toBeVisible(),
    );
    expect(screen.getByLabelText(ba.gstin)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(ba.gstin)).toHaveAccessibleDescription(
      expect.stringContaining(messages.en.errors.field.string_pattern_mismatch),
    );
  });

  it("step-up required (428) sends the operator to re-authenticate", async () => {
    stub.routes["POST /bff/api/v1/platform/tenants"] = () =>
      problem(428, "step_up_required", {
        step_up_url: "/bff/auth/platform/step-up?next=%2Fen%2Fplatform%2Fprovision",
      });
    const user = userEvent.setup({ delay: null });
    renderWithIntl(<ProvisionSchoolForm />, "en");
    await fillToReview(user, "shared");
    await user.click(screen.getByRole("button", { name: m.submit }));

    await waitFor(() =>
      expect(stub.navigate).toHaveBeenCalledWith(
        "/bff/auth/platform/step-up?next=%2Fen%2Fplatform%2Fprovision",
      ),
    );
    expect(await screen.findByText(messages.en.errors.api.redirecting)).toBeInTheDocument();
  });
});
