import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { registerStepUpHandler } from "@/lib/bff/step-up";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { fakeAadhaar } from "@/test/records-fixtures";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, SECTION, structureRoutes } from "@/test/school-fixtures";
import { InviteUserScreen } from "./InviteUserScreen";
import { scopesBody } from "./parts";
import { profileBody, profileSchema } from "./UserDetailScreen";
import { UserDetailScreen } from "./UserDetailScreen";
import { UsersScreen } from "./UsersScreen";

type Schemas = components["schemas"];
const detailCopy = messages.en.school.users.detail;

const push = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/settings/users",
    useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

// Synthetic data only.
const MANAGE = "user.manage";
const ASSIGN = "role.assign";
const READ_BASIC = "student.read_basic";
const ME_USER = "0192f3a4-0000-7000-8000-0000000000a1";
const USER = "0192f3a4-0000-7000-8000-0000000000d1";
const CREATED = "0192f3a4-0000-7000-8000-0000000000d9";
const CLASS_9 = "0192f3a4-0000-7000-8000-0000000000c9";
const OLD_SECTION = "0192f3a4-0000-7000-8000-00000000e0ff";

function role(
  key: string,
  permissions: string[],
  overrides: Partial<Schemas["RoleOut"]> = {},
): Schemas["RoleOut"] {
  return {
    id: `0192f3a4-0000-7000-8000-${key.length.toString(16).padStart(12, "0")}`,
    key,
    name_en: key,
    name_te: key,
    is_system: true,
    permissions,
    grantable: true,
    scoped: false,
    needs_mfa: false,
    ...overrides,
  };
}

const ROLES: Schemas["RoleOut"][] = [
  role("owner", [MANAGE, ASSIGN, READ_BASIC, "audit.read"], {
    name_en: "Owner",
    name_te: "యజమాని",
    needs_mfa: true,
  }),
  role("office_admin", [MANAGE, ASSIGN, READ_BASIC], {
    name_en: "Office admin",
    name_te: "ఆఫీసు అడ్మిన్",
    needs_mfa: true,
  }),
  role("office_staff", [READ_BASIC], { name_en: "Office staff", name_te: "ఆఫీసు సిబ్బంది" }),
  role("class_teacher", [READ_BASIC], {
    name_en: "Class teacher",
    name_te: "తరగతి ఉపాధ్యాయులు",
    scoped: true,
  }),
  role("librarian", [READ_BASIC, "document.read"], {
    name_en: "Librarian",
    name_te: "గ్రంథపాలకులు",
    is_system: false,
  }),
];

function user(overrides: Partial<Schemas["UserOut"]> = {}): Schemas["UserOut"] {
  return {
    id: USER,
    membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
    display_name: "Lakshmi Sample",
    email: "lakshmi@school.example",
    preferred_language: "te",
    status: "active",
    expires_at: null,
    roles: ["class_teacher"],
    scopes: [{ type: "section", ref: SECTION }],
    last_login_at: "2026-09-20T04:30:00Z",
    created_at: "2026-06-01T04:30:00Z",
    version: 3,
    profile_shared: false,
    ...overrides,
  };
}

let stub: BffStub;
let unregisterStepUp: (() => void) | null = null;

function setMe(permissions: string[], roles: string[] = ["office_admin"]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions, { roles }));
}

/** GET /roles as the API answers it for the caller: `grantable` false for the given keys. */
function serveRoles(notGrantable: string[]) {
  stub.routes["GET /bff/api/v1/roles"] = () =>
    page(ROLES.map((item) => ({ ...item, grantable: !notGrantable.includes(item.key) })));
}

function body(key: string, index = 0): Record<string, unknown> {
  return JSON.parse(stub.callsTo(key)[index]?.body ?? "{}") as Record<string, unknown>;
}

function serveUser(value: Schemas["UserOut"]) {
  stub.routes[`GET /bff/api/v1/users/${value.id}`] = () =>
    Response.json(value, { headers: { etag: `W/"${value.version}"` } });
}

beforeEach(() => {
  push.mockReset();
  stub = installBffStub("staff");
  Object.assign(stub.routes, structureRoutes());
  stub.routes["GET /bff/api/v1/roles"] = () => page(ROLES);
});

afterEach(() => {
  unregisterStepUp?.();
  unregisterStepUp = null;
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

describe("users list (US-102, FR-IAM-010..014)", () => {
  it("lists staff with role names from /roles, links by ID only, and offers the invite to user.manage", async () => {
    setMe([MANAGE, READ_BASIC]);
    stub.routes["GET /bff/api/v1/users"] = () =>
      page([
        user(),
        user({
          id: CREATED,
          display_name: "Ravi Sample",
          email: null,
          roles: ["librarian", "office_staff"],
          scopes: [],
          status: "invited",
          last_login_at: null,
        }),
      ]);
    renderWithIntl(<UsersScreen />);
    const link = await screen.findByRole("link", { name: "Lakshmi Sample" });
    expect(link).toHaveAttribute("href", `/settings/users/${USER}`);
    const ravi = screen.getAllByRole("row").find((row) => within(row).queryByText("Ravi Sample"));
    // Roles are tags, one list item each, with names from /roles or the messages.
    expect(ravi && (await within(ravi).findByText("Librarian"))).toBeTruthy();
    expect(
      ravi &&
        within(ravi)
          .getAllByRole("listitem")
          .map((item) => item.textContent),
    ).toEqual(["Librarian", "Office staff"]);
    expect(ravi && within(ravi).getByText("None chosen")).toBeInTheDocument();
    expect(ravi && within(ravi).getByText("Invited")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Invite user" })).toHaveAttribute(
      "href",
      "/settings/users/new",
    );
    expect(await screen.findByText("Made for this school")).toBeInTheDocument();
    // No personal data in any URL the screen asked for.
    for (const call of stub.calls) {
      expect(call.url.search).not.toMatch(/Lakshmi|school\.example/);
    }
  });

  it("marks your own row and people whose profile is shared with another school", async () => {
    setMe([MANAGE, READ_BASIC]);
    stub.routes["GET /bff/api/v1/users"] = () =>
      page([
        user({ id: ME_USER, display_name: "Test Clerk" }),
        user({ id: CREATED, display_name: "Ravi Sample", profile_shared: true }),
        user(),
      ]);
    renderWithIntl(<UsersScreen />);
    const rowOf = async (name: string) =>
      (await screen.findByRole("link", { name })).closest("tr") as HTMLElement;
    const mine = await rowOf("Test Clerk");
    expect(within(mine).getByText(messages.en.school.users.youBadge)).toBeInTheDocument();
    const shared = await rowOf("Ravi Sample");
    expect(within(shared).getByText(messages.en.school.users.sharedBadge)).toBeInTheDocument();
    const plain = await rowOf("Lakshmi Sample");
    expect(within(plain).queryByText(messages.en.school.users.youBadge)).toBeNull();
    expect(within(plain).queryByText(messages.en.school.users.sharedBadge)).toBeNull();
  });

  it("without user.manage it says so and asks the API nothing", async () => {
    setMe([READ_BASIC]);
    renderWithIntl(<UsersScreen />);
    expect(await screen.findByText("You can't manage users")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(stub.callsTo("GET /bff/api/v1/users")).toHaveLength(0);
    expect(screen.queryByRole("link", { name: "Invite user" })).not.toBeInTheDocument();
  });

  it("renders in Telugu without missing messages", async () => {
    setMe([MANAGE, READ_BASIC]);
    stub.routes["GET /bff/api/v1/users"] = () => page([user()]);
    renderWithIntl(<UsersScreen />, "te");
    expect(await screen.findAllByText("తరగతి ఉపాధ్యాయులు")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "వినియోగదారుని ఆహ్వానించండి" })).toBeInTheDocument();
  });

  it("the Users and roles menu entry needs user.manage (FR-IAM-011, UX only)", () => {
    const { unmount } = renderWithIntl(
      <SchoolShell permissions={[READ_BASIC]}>
        <p>page</p>
      </SchoolShell>,
    );
    expect(screen.queryByRole("link", { name: "Users and roles" })).not.toBeInTheDocument();
    unmount();
    renderWithIntl(
      <SchoolShell permissions={[MANAGE]}>
        <p>page</p>
      </SchoolShell>,
    );
    expect(screen.getByRole("link", { name: "Users and roles" })).toHaveAttribute(
      "href",
      "/settings/users",
    );
  });
});

describe("invite user (US-102 AC1, FR-IAM-010..012)", () => {
  it("checks the form before sending anything", async () => {
    setMe([MANAGE, READ_BASIC]);
    renderWithIntl(<InviteUserScreen />);
    await userEvent.click(await screen.findByRole("button", { name: "Send invitation" }));
    expect(await screen.findAllByText("Fill in this field.")).toHaveLength(2);
    expect(screen.getByText("Choose at least one role.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Sign-in ID"), "has space");
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    expect(await screen.findByText("Remove the spaces.")).toBeInTheDocument();
    expect(stub.callsTo("POST /bff/api/v1/users")).toHaveLength(0);
  });

  it("invites a class teacher for one section with an Idempotency-Key and opens their page", async () => {
    setMe([MANAGE, READ_BASIC]);
    stub.routes["POST /bff/api/v1/users"] = () =>
      Response.json(user({ id: CREATED, status: "invited" }), { status: 201 });
    renderWithIntl(<InviteUserScreen />);
    await userEvent.type(await screen.findByLabelText("Name"), "Lakshmi Sample");
    await userEvent.type(screen.getByLabelText("Sign-in ID"), "synthetic|synth-a|teacher|9");
    await userEvent.click(screen.getByLabelText("Class teacher"));
    await userEvent.click(screen.getByLabelText("Only some classes or sections"));
    await userEvent.click(await screen.findByLabelText("Class 9 · A"));
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/settings/users/${CREATED}`));
    const call = stub.callsTo("POST /bff/api/v1/users")[0];
    expect(call?.headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(body("POST /bff/api/v1/users")).toEqual({
      display_name: "Lakshmi Sample",
      email: null,
      idp_subject: "synthetic|synth-a|teacher|9",
      preferred_language: "en",
      roles: ["class_teacher"],
      scopes: [{ type: "section", ref: SECTION }],
    });
  });

  it("with Telugu switched off, the invitation asks for no language and sends English (ADR-0036)", async () => {
    setMe([MANAGE, READ_BASIC]);
    stub.routes["POST /bff/api/v1/users"] = () =>
      Response.json(user({ id: CREATED, status: "invited" }), { status: 201 });
    const { container } = renderWithIntl(<InviteUserScreen />);
    await userEvent.type(await screen.findByLabelText("Name"), "Lakshmi Sample");
    expect(screen.queryByRole("radio", { name: "English" })).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/Telugu|[\u0C00-\u0C7F]/);
    await userEvent.type(screen.getByLabelText("Sign-in ID"), "synthetic|synth-a|teacher|9");
    await userEvent.click(screen.getByLabelText("Class teacher"));
    await userEvent.click(screen.getByLabelText("Only some classes or sections"));
    await userEvent.click(await screen.findByLabelText("Class 9 · A"));
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(stub.callsTo("POST /bff/api/v1/users")).toHaveLength(1));
    expect(body("POST /bff/api/v1/users")).toMatchObject({ preferred_language: "en" });
  });

  it("greys out the roles the API marks not grantable, and explains 403 role_not_grantable", async () => {
    setMe([MANAGE, READ_BASIC], ["office_staff"]);
    serveRoles(["owner", "office_admin", "librarian"]);
    stub.routes["POST /bff/api/v1/users"] = () => problem(403, "role_not_grantable");
    renderWithIntl(<InviteUserScreen />);
    expect(await screen.findByLabelText("Office admin")).toBeDisabled();
    expect(screen.getByLabelText("Owner")).toBeDisabled();
    expect(screen.getByLabelText("Librarian")).toBeDisabled();
    expect(screen.getByLabelText("Office staff")).toBeEnabled();
    await userEvent.type(screen.getByLabelText("Name"), "Ravi Sample");
    await userEvent.type(screen.getByLabelText("Sign-in ID"), "synth-ravi");
    await userEvent.click(screen.getByLabelText("Office staff"));
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    expect(await screen.findByText("You can't give or take away this role")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("428: the global step-up prompt confirms and the same request is sent again", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC]);
    const prompt = vi.fn(async () => true);
    unregisterStepUp = registerStepUpHandler("staff", prompt);
    let attempts = 0;
    stub.routes["POST /bff/api/v1/users"] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up?next=%2F" })
        : Response.json(user({ id: CREATED, status: "invited" }), { status: 201 });
    };
    renderWithIntl(<InviteUserScreen />);
    await userEvent.type(await screen.findByLabelText("Name"), "Ravi Sample");
    await userEvent.type(screen.getByLabelText("Sign-in ID"), "synth-ravi");
    await userEvent.click(screen.getByLabelText("Office staff"));
    await userEvent.click(screen.getByLabelText("Whole school"));
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/settings/users/${CREATED}`));
    expect(prompt).toHaveBeenCalledTimes(1);
    const calls = stub.callsTo("POST /bff/api/v1/users");
    expect(calls).toHaveLength(2);
    expect(calls[1]?.headers.get("idempotency-key")).toBe(calls[0]?.headers.get("idempotency-key"));
    expect(body("POST /bff/api/v1/users", 1).scopes).toEqual([{ type: "school", ref: null }]);
  });

  it("shows a 409 duplicate in plain words", async () => {
    setMe([MANAGE, READ_BASIC]);
    stub.routes["POST /bff/api/v1/users"] = () => problem(409, "duplicate");
    renderWithIntl(<InviteUserScreen />, "te");
    await userEvent.type(await screen.findByLabelText("పేరు"), "Ravi Sample");
    await userEvent.type(screen.getByLabelText("సైన్-ఇన్ ID"), "synth-ravi");
    await userEvent.click(screen.getByLabelText("ఆఫీసు సిబ్బంది"));
    await userEvent.click(screen.getByRole("button", { name: "ఆహ్వానం పంపండి" }));
    expect(await screen.findByText("ఈ వ్యక్తికి ఇప్పటికే యాక్సెస్ ఉంది")).toBeInTheDocument();
  });
});

describe("user detail (US-102 AC2, FR-IAM-012..014)", () => {
  it("without role.assign the role and class controls are hidden", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByRole("heading", { name: "Lakshmi Sample" })).toBeInTheDocument();
    expect(await screen.findByText("Class teacher")).toBeInTheDocument();
    expect(screen.getByText("1 section")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save roles" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save classes and sections" })).toBeNull();
    // user.manage still allows suspending and removing.
    expect(screen.getByRole("button", { name: "Suspend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("without user.manage there are no status actions", async () => {
    setMe([ASSIGN, READ_BASIC]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByRole("button", { name: "Save roles" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
  });

  it("replaces roles with PUT and keeps a held role the member cannot change", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["principal"]);
    serveRoles(["owner", "librarian"]);
    serveUser(user({ roles: ["class_teacher", "librarian"] }));
    stub.routes[`PUT /bff/api/v1/users/${USER}/roles`] = () =>
      Response.json(user({ roles: ["office_staff", "librarian"] }));
    renderWithIntl(<UserDetailScreen userId={USER} />);
    const librarian = await screen.findByLabelText("Librarian");
    expect(librarian).toBeDisabled();
    expect(librarian).toBeChecked();
    expect(screen.getByLabelText("Office staff")).toBeEnabled();
    await userEvent.click(screen.getByLabelText("Class teacher"));
    await userEvent.click(screen.getByLabelText("Office staff"));
    await userEvent.click(screen.getByRole("button", { name: "Save roles" }));
    expect(await screen.findByText("Saved. It takes effect within a minute.")).toBeInTheDocument();
    const sent = body(`PUT /bff/api/v1/users/${USER}/roles`).roles as string[];
    expect([...sent].sort()).toEqual(["librarian", "office_staff"]);
  });

  it("an empty role choice is refused before sending", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    await userEvent.click(await screen.findByLabelText("Class teacher"));
    await userEvent.click(screen.getByRole("button", { name: "Save roles" }));
    expect(await screen.findByText("Choose at least one role.")).toBeInTheDocument();
    expect(stub.callsTo(`PUT /bff/api/v1/users/${USER}/roles`)).toHaveLength(0);
  });

  it("replaces classes and sections with PUT, keeping choices from another year until unticked", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(
      user({
        scopes: [
          { type: "section", ref: SECTION },
          { type: "section", ref: OLD_SECTION },
        ],
      }),
    );
    stub.routes[`PUT /bff/api/v1/users/${USER}/scopes`] = () => Response.json(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByLabelText("A section from another year")).toBeChecked();
    await userEvent.click(screen.getByLabelText("Class 9 · A"));
    await userEvent.click(screen.getByLabelText("Class 9"));
    await userEvent.click(screen.getByRole("button", { name: "Save classes and sections" }));
    await screen.findByText("Saved. It takes effect within a minute.");
    expect(body(`PUT /bff/api/v1/users/${USER}/scopes`)).toEqual({
      scopes: [
        { type: "class", ref: CLASS_9 },
        { type: "section", ref: OLD_SECTION },
      ],
    });
  });

  it("'only some' with nothing ticked says how to fix it", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    await userEvent.click(await screen.findByLabelText("Class 9 · A"));
    await userEvent.click(screen.getByRole("button", { name: "Save classes and sections" }));
    expect(
      await screen.findByText("Choose at least one class or section, or choose another option."),
    ).toBeInTheDocument();
    expect(stub.callsTo(`PUT /bff/api/v1/users/${USER}/scopes`)).toHaveLength(0);
  });

  it("suspends with If-Match and explains 409 last_owner", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ roles: ["owner"] }));
    let attempts = 0;
    stub.routes[`PATCH /bff/api/v1/users/${USER}`] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(409, "last_owner")
        : Response.json(user({ status: "suspended", version: 4 }));
    };
    renderWithIntl(<UserDetailScreen userId={USER} />);
    await userEvent.click(await screen.findByRole("button", { name: "Suspend" }));
    const dialog = screen.getByRole("dialog", { name: "Suspend Lakshmi Sample?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Suspend" }));
    expect(await within(dialog).findByText("At least one owner must remain")).toBeInTheDocument();
    const call = stub.callsTo(`PATCH /bff/api/v1/users/${USER}`)[0];
    expect(call?.headers.get("if-match")).toBe('W/"3"');
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ status: "suspended" });
  });

  it("offers only 'cancel' for an invitation and 'give access back' for a suspended person", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ status: "invited" }));
    const { unmount } = renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText("Waiting for them to sign in")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Give access back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    unmount();
    serveUser(user({ status: "suspended" }));
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByRole("button", { name: "Give access back" })).toBeInTheDocument();
  });

  it("sends the invitation email again for an invited person (POST /users/{id}/invitation-email)", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ status: "invited" }));
    const RESEND = `POST /bff/api/v1/users/${USER}/invitation-email`;
    stub.routes[RESEND] = () =>
      Response.json(
        {
          user_id: USER,
          membership_id: "0192f3a4-0000-7000-8000-0000000000e1",
          status: "queued",
          expires_at: "2026-10-11T04:30:00Z",
        },
        { status: 202 },
      );
    renderWithIntl(<UserDetailScreen userId={USER} />);
    await userEvent.click(
      await screen.findByRole("button", { name: detailCopy.resend.button }),
    );
    const dialog = screen.getByRole("dialog", { name: detailCopy.resend.title });
    expect(within(dialog).getByText("lakshmi@school.example", { exact: false })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: detailCopy.resend.button }));
    expect(await within(dialog).findByText(detailCopy.resend.sentTitle)).toBeInTheDocument();
    expect(stub.callsTo(RESEND)).toHaveLength(1);
    expect(stub.callsTo(RESEND)[0]?.body ?? "").toBe("");
  });

  it("explains why the invitation email can't be sent (409 email_disabled, 429)", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ status: "invited" }));
    const RESEND = `POST /bff/api/v1/users/${USER}/invitation-email`;
    let attempts = 0;
    stub.routes[RESEND] = () => {
      attempts += 1;
      return attempts === 1 ? problem(409, "email_disabled") : problem(429, "rate_limited");
    };
    renderWithIntl(<UserDetailScreen userId={USER} />);
    await userEvent.click(
      await screen.findByRole("button", { name: detailCopy.resend.button }),
    );
    const dialog = screen.getByRole("dialog", { name: detailCopy.resend.title });
    const send = within(dialog).getByRole("button", { name: detailCopy.resend.button });
    await userEvent.click(send);
    expect(
      await within(dialog).findByText(messages.en.school.users.errors.email_disabled.title),
    ).toBeInTheDocument();
    await userEvent.click(send);
    expect(
      await within(dialog).findByText(messages.en.school.users.errors.rate_limited.title),
    ).toBeInTheDocument();
  });

  it("offers no resend without an email address, for an active person or without user.manage", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ status: "invited", email: null }));
    const first = renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText("Waiting for them to sign in")).toBeInTheDocument();
    expect(screen.getByText(detailCopy.resend.noEmail)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: detailCopy.resend.button })).toBeNull();
    first.unmount();
    serveUser(user({ status: "active" }));
    const second = renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByRole("button", { name: "Suspend" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: detailCopy.resend.button })).toBeNull();
    second.unmount();
    setMe([READ_BASIC]);
    serveUser(user({ status: "invited" }));
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText("Waiting for them to sign in")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: detailCopy.resend.button })).toBeNull();
  });

  it("a removed person has no actions and no role or class controls", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user({ status: "removed" }));
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText("This person has been removed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save roles" })).not.toBeInTheDocument();
  });

  it("temporary SchoolOS support access is changed only on the Support access page", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC, "breakglass.approve"], ["owner"]);
    serveUser(user({ roles: ["platform_support"], display_name: "SchoolOS support" }));
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText("Temporary SchoolOS support access")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Support access" })).toHaveAttribute(
      "href",
      "/break-glass",
    );
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save roles" })).not.toBeInTheDocument();
  });

  it("marks your own account and offers no suspend or remove for it", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ id: ME_USER }));
    renderWithIntl(<UserDetailScreen userId={ME_USER} />);
    expect(await screen.findByText("This is you")).toBeInTheDocument();
    expect(screen.getByText(messages.en.school.users.detail.selfNoStatus)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    // Your own name, email and language can still be corrected.
    expect(screen.getByRole("button", { name: detailCopy.edit.trigger })).toBeInTheDocument();
  });

  it("a profile shared with another school offers no edit and says why (ADR-0028)", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user({ profile_shared: true }));
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText(detailCopy.profileShared)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: detailCopy.edit.trigger })).toBeNull();
    // Status and roles are the school's own and can still change.
    expect(screen.getByRole("button", { name: "Suspend" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Save roles" })).toBeInTheDocument();
    expect(stub.calls.filter((call) => call.method === "PATCH")).toHaveLength(0);
  });

  it("notes the roles that need two-step sign-in from the API (RoleOut.needs_mfa)", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    stub.routes["GET /bff/api/v1/roles"] = () =>
      page(ROLES.map((item) => (item.key === "librarian" ? { ...item, needs_mfa: true } : item)));
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    const needsMfa = messages.en.school.users.rolesForm.needsMfa;
    const owner = await screen.findByLabelText("Owner");
    expect(owner).toHaveAccessibleDescription(expect.stringContaining(needsMfa));
    expect(screen.getByLabelText("Librarian")).toHaveAccessibleDescription(
      expect.stringContaining(needsMfa),
    );
    expect(screen.getByLabelText("Office staff")).not.toHaveAccessibleDescription();
  });

  it("404: says the person was not found", async () => {
    setMe([MANAGE, READ_BASIC]);
    renderWithIntl(<UserDetailScreen userId={USER} />, "te");
    expect(await screen.findByText("ఈ వ్యక్తి కనబడలేదు")).toBeInTheDocument();
  });

  it("explains 422 roles_required on the role choice", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user());
    stub.routes[`PUT /bff/api/v1/users/${USER}/roles`] = () =>
      problem(422, "roles_required", {
        errors: [{ field: "roles", code: "roles_required", message_key: "errors.roles_required" }],
      });
    renderWithIntl(<UserDetailScreen userId={USER} />);
    await userEvent.click(await screen.findByLabelText("Office staff"));
    await userEvent.click(screen.getByRole("button", { name: "Save roles" }));
    expect(await screen.findByText(messages.en.errors.field.roles_required)).toBeInTheDocument();
  });

  it("notes the roles that reach only the chosen classes (RoleOut.scoped)", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    const teacher = await screen.findByLabelText("Class teacher");
    expect(teacher).toHaveAccessibleDescription(messages.en.school.users.rolesForm.scopedRole);
    expect(screen.getByLabelText("Office staff")).not.toHaveAccessibleDescription();
  });
});

describe("edit a staff member's details (US-102, FR-IAM-010)", () => {
  const PATCH = `PATCH /bff/api/v1/users/${USER}`;

  async function openEdit() {
    await userEvent.click(await screen.findByRole("button", { name: detailCopy.edit.trigger }));
    return screen.getByRole("dialog", { name: detailCopy.edit.title });
  }

  it("builds a body with only what changed; an emptied email is null", () => {
    const current = user();
    expect(
      profileBody(
        current,
        profileSchema.parse({
          display_name: " Lakshmi K ",
          email: "",
          preferred_language: "te",
        }),
      ),
    ).toEqual({ display_name: "Lakshmi K", email: null });
    expect(
      profileBody(
        current,
        profileSchema.parse({
          display_name: "Lakshmi Sample",
          email: "LAKSHMI@school.example",
          preferred_language: "en",
        }),
      ),
    ).toEqual({ preferred_language: "en" });
    expect(
      profileSchema.safeParse({
        display_name: `Lakshmi ${fakeAadhaar()}`,
        email: "",
        preferred_language: "en",
      }).success,
    ).toBe(false);
  });

  it("edits name, email and language with If-Match; the global step-up retries the same call", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user());
    const prompt = vi.fn(async () => true);
    unregisterStepUp = registerStepUpHandler("staff", prompt);
    let attempts = 0;
    stub.routes[PATCH] = () => {
      attempts += 1;
      return attempts === 1
        ? problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up?next=%2F" })
        : Response.json(user({ display_name: "Lakshmi K", email: null, version: 4 }));
    };
    renderWithIntl(<UserDetailScreen userId={USER} />);
    const dialog = await openEdit();
    const name = within(dialog).getByLabelText(detailCopy.edit.name);
    expect(name).toHaveValue("Lakshmi Sample");
    await userEvent.clear(name);
    await userEvent.type(name, "Lakshmi K");
    await userEvent.clear(within(dialog).getByLabelText(detailCopy.edit.email));
    await userEvent.click(within(dialog).getByRole("button", { name: detailCopy.edit.submit }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(2));
    expect(prompt).toHaveBeenCalledTimes(1);
    const call = stub.callsTo(PATCH)[1];
    expect(call?.headers.get("if-match")).toBe('W/"3"');
    expect(body(PATCH, 1)).toEqual({ display_name: "Lakshmi K", email: null });
  });

  it("refuses a full Aadhaar number in the name before sending", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    const dialog = await openEdit();
    await userEvent.type(within(dialog).getByLabelText(detailCopy.edit.name), ` ${fakeAadhaar()}`);
    await userEvent.click(within(dialog).getByRole("button", { name: detailCopy.edit.submit }));
    expect(await within(dialog).findByText(messages.en.validation.noAadhaar)).toBeInTheDocument();
    expect(stub.callsTo(PATCH)).toHaveLength(0);
  });

  it("explains 409 invalid_state (the person was removed meanwhile)", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user());
    stub.routes[PATCH] = () => problem(409, "invalid_state");
    // Telugu switched on explicitly (ADR-0036): the language is offered only then.
    renderWithIntl(<UserDetailScreen userId={USER} />, { telugu: true });
    const dialog = await openEdit();
    await userEvent.selectOptions(within(dialog).getByLabelText(detailCopy.edit.language), "en");
    await userEvent.click(within(dialog).getByRole("button", { name: detailCopy.edit.submit }));
    expect(
      await within(dialog).findByText(messages.en.school.users.errors.invalid_state.title),
    ).toBeInTheDocument();
  });

  it("with Telugu switched off, no language is shown, asked for or sent (ADR-0036)", async () => {
    setMe([MANAGE, READ_BASIC]);
    // The fixture's preferred language is Telugu: it stays stored, never shown.
    serveUser(user());
    stub.routes[PATCH] = () => Response.json(user({ display_name: "Lakshmi K", version: 4 }));
    const { container } = renderWithIntl(<UserDetailScreen userId={USER} />);
    const dialog = await openEdit();
    expect(within(dialog).queryByLabelText(detailCopy.edit.language)).toBeNull();
    expect(container.textContent ?? "").not.toMatch(/Telugu|[\u0C00-\u0C7F]/);
    expect(dialog.textContent ?? "").not.toMatch(/Telugu|[\u0C00-\u0C7F]/);
    const name = within(dialog).getByLabelText(detailCopy.edit.name);
    await userEvent.clear(name);
    await userEvent.type(name, "Lakshmi K");
    await userEvent.click(within(dialog).getByRole("button", { name: detailCopy.edit.submit }));
    await waitFor(() => expect(stub.callsTo(PATCH)).toHaveLength(1));
    expect(body(PATCH, 0)).toEqual({ display_name: "Lakshmi K" });
  });

  it("is not offered for a removed person or without user.manage", async () => {
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user({ status: "removed" }));
    const { unmount } = renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByText("This person has been removed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: detailCopy.edit.trigger })).toBeNull();
    unmount();
    setMe([ASSIGN, READ_BASIC], ["owner"]);
    serveUser(user());
    renderWithIntl(<UserDetailScreen userId={USER} />);
    expect(await screen.findByRole("button", { name: "Save roles" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: detailCopy.edit.trigger })).toBeNull();
  });
});

describe("helpers", () => {
  it("scopesBody maps each choice to the API's ScopeIn list", () => {
    expect(scopesBody({ scope_mode: "none", class_refs: [CLASS_9], section_refs: [] })).toEqual([]);
    expect(scopesBody({ scope_mode: "school", class_refs: [], section_refs: [SECTION] })).toEqual([
      { type: "school", ref: null },
    ]);
    expect(
      scopesBody({ scope_mode: "chosen", class_refs: [CLASS_9], section_refs: [SECTION] }),
    ).toEqual([
      { type: "class", ref: CLASS_9 },
      { type: "section", ref: SECTION },
    ]);
  });

  it("greyed-out roles come only from the API's grantable (no grant rules copied into the web app)", async () => {
    // Even for an owner, a role the API says is not grantable stays greyed out; and a role it
    // says is grantable is offered to a clerk whose own permissions the web app never compares.
    setMe([MANAGE, ASSIGN, READ_BASIC], ["owner"]);
    serveRoles(["owner"]);
    const { unmount } = renderWithIntl(<InviteUserScreen />);
    expect(await screen.findByLabelText("Owner")).toBeDisabled();
    expect(screen.getByLabelText("Office admin")).toBeEnabled();
    unmount();
    setMe([MANAGE, READ_BASIC], ["office_staff"]);
    serveRoles([]);
    renderWithIntl(<InviteUserScreen />);
    expect(await screen.findByLabelText("Office admin")).toBeEnabled();
    expect(screen.getByLabelText("Librarian")).toBeEnabled();
  });
});
