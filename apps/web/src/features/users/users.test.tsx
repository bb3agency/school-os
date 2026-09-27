import type { components } from "@schoolos/api-client";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import { registerStepUpHandler } from "@/lib/bff/step-up";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, renderWithIntl } from "@/test/render";
import { me, SECTION, structureRoutes } from "@/test/school-fixtures";
import { InviteUserScreen } from "./InviteUserScreen";
import { canGrantRole, scopesBody } from "./parts";
import { UserDetailScreen } from "./UserDetailScreen";
import { UsersScreen } from "./UsersScreen";

type Schemas = components["schemas"];

const push = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/settings/users",
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
    ...overrides,
  };
}

const ROLES: Schemas["RoleOut"][] = [
  role("owner", [MANAGE, ASSIGN, READ_BASIC, "audit.read"], {
    name_en: "Owner",
    name_te: "యజమాని",
  }),
  role("office_admin", [MANAGE, ASSIGN, READ_BASIC], {
    name_en: "Office admin",
    name_te: "ఆఫీసు అడ్మిన్",
  }),
  role("office_staff", [READ_BASIC], { name_en: "Office staff", name_te: "ఆఫీసు సిబ్బంది" }),
  role("class_teacher", [READ_BASIC], {
    name_en: "Class teacher",
    name_te: "తరగతి ఉపాధ్యాయులు",
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
    ...overrides,
  };
}

let stub: BffStub;
let unregisterStepUp: (() => void) | null = null;

function setMe(permissions: string[], roles: string[] = ["office_admin"]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions, { roles }));
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
    expect(link).toHaveAttribute("href", `/en/settings/users/${USER}`);
    const ravi = screen.getAllByRole("row").find((row) => within(row).queryByText("Ravi Sample"));
    expect(ravi && (await within(ravi).findByText("Librarian and Office staff"))).toBeTruthy();
    expect(ravi && within(ravi).getByText("None chosen")).toBeInTheDocument();
    expect(ravi && within(ravi).getByText("Invited")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Invite user" })).toHaveAttribute(
      "href",
      "/en/settings/users/new",
    );
    expect(await screen.findByText("Made for this school")).toBeInTheDocument();
    // No personal data in any URL the screen asked for.
    for (const call of stub.calls) {
      expect(call.url.search).not.toMatch(/Lakshmi|school\.example/);
    }
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
      "/en/settings/users",
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
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/settings/users/${CREATED}`));
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

  it("greys out roles needing more than user.manage, and explains 403 role_not_grantable", async () => {
    setMe([MANAGE, READ_BASIC], ["office_staff"]);
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
        ? problem(428, "step_up_required", { step_up_url: "/bff/auth/step-up?next=%2Fen" })
        : Response.json(user({ id: CREATED, status: "invited" }), { status: 201 });
    };
    renderWithIntl(<InviteUserScreen />);
    await userEvent.type(await screen.findByLabelText("Name"), "Ravi Sample");
    await userEvent.type(screen.getByLabelText("Sign-in ID"), "synth-ravi");
    await userEvent.click(screen.getByLabelText("Office staff"));
    await userEvent.click(screen.getByLabelText("Whole school"));
    await userEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/en/settings/users/${CREATED}`));
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
      "/en/break-glass",
    );
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save roles" })).not.toBeInTheDocument();
  });

  it("marks your own account and warns before you suspend yourself", async () => {
    setMe([MANAGE, READ_BASIC]);
    serveUser(user({ id: ME_USER }));
    renderWithIntl(<UserDetailScreen userId={ME_USER} />);
    expect(await screen.findByText("This is you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Suspend" }));
    const dialog = screen.getByRole("dialog", { name: "Suspend Lakshmi Sample?" });
    expect(
      within(dialog).getByText("This is your own account. You will lose access to this school."),
    ).toBeInTheDocument();
  });

  it("404: says the person was not found", async () => {
    setMe([MANAGE, READ_BASIC]);
    renderWithIntl(<UserDetailScreen userId={USER} />, "te");
    expect(await screen.findByText("ఈ వ్యక్తి కనబడలేదు")).toBeInTheDocument();
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

  it("canGrantRole mirrors the API's grant rules (owner any; invite of non-MFA built-in roles)", () => {
    const [owner, officeAdmin, officeStaff, , librarian] = ROLES as [
      Schemas["RoleOut"],
      Schemas["RoleOut"],
      Schemas["RoleOut"],
      Schemas["RoleOut"],
      Schemas["RoleOut"],
    ];
    const clerk = { roles: ["office_staff"], permissions: [MANAGE, READ_BASIC] };
    expect(canGrantRole(officeStaff, clerk, "invite")).toBe(true);
    expect(canGrantRole(officeAdmin, clerk, "invite")).toBe(false);
    expect(canGrantRole(librarian, clerk, "invite")).toBe(false);
    const principal = { roles: ["principal"], permissions: [MANAGE, ASSIGN, READ_BASIC] };
    expect(canGrantRole(officeAdmin, principal, "assign")).toBe(true);
    expect(canGrantRole(owner, principal, "assign")).toBe(false);
    expect(canGrantRole(librarian, principal, "invite")).toBe(false);
    expect(canGrantRole(owner, { roles: ["owner"], permissions: [] }, "assign")).toBe(true);
  });
});
