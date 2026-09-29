import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as Navigation from "next/navigation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FeesPage from "@/app/[locale]/(school)/fees/page";
import TallyLedgersPage from "@/app/[locale]/(school)/settings/tally/ledgers/page";
import TallyConnectorPage from "@/app/[locale]/(school)/settings/tally/page";
import { installBffStub, page, problem, uninstallBffStub, type BffStub } from "@/test/bff-stub";
import { intlErrors, messages, renderWithIntl } from "@/test/render";
import { me, STUDENT, TENANT } from "@/test/school-fixtures";
import {
  balanceKind,
  CONFIGURE,
  DEVICE_MANAGE,
  enrolCommand,
  FINANCE_READ,
  ifMatch,
  type ConnectorStatus,
  type Device,
  type DuesPage,
  type Group,
  type Party,
  type PartyDetail,
} from "./data";
import { FeeDuesScreen } from "./FeeDuesScreen";
import { TallyConnectorScreen } from "./TallyConnectorScreen";
import { TallyLedgersScreen } from "./TallyLedgersScreen";

/**
 * Tally connector screens (M6; ADR-0032 Proposed; behind `tally.connector.enabled`):
 * US-1801 enrol an office PC agent, US-1802 its status and revocation, US-1803 ledger groups and
 * ledger ↔ student links, US-1804 fee dues. Synthetic names and amounts only.
 */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/settings/tally",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

const en = messages.en.tally;
const te = messages.te.tally;
const DEVICE = "0192f3a4-0000-7000-8000-00000000a001";
const PARTY = "0192f3a4-0000-7000-8000-00000000a101";
const GROUP_DEBTORS = "0192f3a4-0000-7000-8000-00000000a201";
const GROUP_SALARY = "0192f3a4-0000-7000-8000-00000000a202";
const SIBLING = "0192f3a4-0000-7000-8000-00000000d002";
const CODE = "KQ7M-P2XW-9HTD";
const LEDGER = "Synthetic Parent Ledger A";

function status(overrides: Partial<ConnectorStatus> = {}): ConnectorStatus {
  return {
    devices_active: 1,
    silent: false,
    last_sync_at: "2026-09-29T04:30:00Z",
    as_of: "2026-09-29",
    company: "Synthetic Vidyalaya 2026-27",
    groups_selected: 1,
    parties: 3,
    parties_linked: 2,
    parties_unlinked: 1,
    total_due: "1250000.00",
    unlinked_due: "4500.00",
    ...overrides,
  };
}

function device(overrides: Partial<Device> = {}): Device {
  return {
    id: DEVICE,
    name: "Accounts PC",
    status: "active",
    agent_version: "0.1.0",
    platform: "Windows-10",
    tally_product: "TallyPrime 5.0",
    enrolled_at: "2026-09-01T04:30:00Z",
    revoked_at: null,
    last_seen_at: "2026-09-29T04:30:00Z",
    last_sync_at: "2026-09-29T04:30:00Z",
    silent: false,
    outdated: false,
    version: 2,
    ...overrides,
  };
}

function group(id: string, name: string, selected: boolean): Group {
  return {
    id,
    company: "Synthetic Vidyalaya 2026-27",
    name,
    parent: name === "Sundry Debtors" ? "Current Assets" : null,
    present: true,
    selected,
    version: 1,
  };
}

function party(overrides: Partial<Party> = {}): Party {
  return {
    id: PARTY,
    ledger_name: LEDGER,
    group_name: "Sundry Debtors",
    closing_balance: "12500.00",
    as_of: "2026-09-29",
    present: true,
    links: [],
    ...overrides,
  };
}

const student = {
  student_id: STUDENT,
  display_name: "Synthetic Student One",
  admission_no: "S-0001",
  class_section: "Class 6 A",
};

function detail(overrides: Partial<PartyDetail> = {}): PartyDetail {
  return { ...party(), candidates: [student], ...overrides };
}

function dues(overrides: Partial<DuesPage> = {}): DuesPage {
  return {
    data: [
      {
        student_id: STUDENT,
        display_name: "Synthetic Student One",
        admission_no: "S-0001",
        class_section: "Class 6 A",
        total_due: "12500.00",
        ledgers: 1,
        as_of: "2026-09-29",
      },
    ],
    next_cursor: null,
    totals: {
      students_with_dues: 1,
      total_due: "12500.00",
      unlinked_parties: 1,
      unlinked_due: "4500.00",
      as_of: "2026-09-29",
    },
    ...overrides,
  };
}

let stub: BffStub;

beforeEach(() => {
  stub = installBffStub("staff");
});
afterEach(() => {
  uninstallBffStub();
  expect(intlErrors).toEqual([]);
});

function signedIn(permissions: string[]) {
  stub.routes["GET /bff/api/v1/me"] = () => Response.json(me(permissions, { roles: ["owner"] }));
}

function connectorOn() {
  stub.routes["GET /bff/api/v1/tally/status"] = () => Response.json(status());
  stub.routes["GET /bff/api/v1/tally/devices"] = () => Response.json([device()]);
  stub.routes["GET /bff/api/v1/tally/groups"] = () =>
    Response.json([
      group(GROUP_DEBTORS, "Sundry Debtors", true),
      group(GROUP_SALARY, "Staff Salary", false),
    ]);
}

describe("helpers", () => {
  it("names a balance in words, never by colour only (WCAG 1.4.1)", () => {
    expect(balanceKind("12500.00")).toBe("due");
    expect(balanceKind("-300.00")).toBe("advance");
    expect(balanceKind("0.00")).toBe("settled");
    expect(balanceKind("not a number")).toBe("settled");
  });

  it("builds the enrolment command and the If-Match value", () => {
    expect(enrolCommand("https://school.example", TENANT, CODE)).toBe(
      `sos-tally-agent enrol --server https://school.example --school ${TENANT} --code ${CODE}`,
    );
    expect(ifMatch(3)).toBe('W/"3"');
  });
});

describe("connector screen (US-1801, US-1802, US-1803; FR-TALLY-001/002/004/009)", () => {
  it("says the connector is not switched on while the school's flag is off (404)", async () => {
    signedIn([DEVICE_MANAGE, CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/status"] = () => problem(404, "not_found");
    renderWithIntl(<TallyConnectorScreen />);
    expect(await screen.findByText(en.off.title)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/tally/devices")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: en.enrol.add })).toBeNull();
  });

  it("explains who can manage it, without calling the API, to someone without access", async () => {
    signedIn(["student.read_basic"]);
    renderWithIntl(<TallyConnectorScreen />);
    expect(await screen.findByText(en.connector.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/tally/status")).toHaveLength(0);
  });

  it("shows the status and warns when the office PC has gone silent", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/tally/status"] = () => Response.json(status({ silent: true }));
    renderWithIntl(<TallyConnectorScreen />);
    expect(await screen.findByText(en.status.silentTitle)).toBeInTheDocument();
    expect(screen.getByText("Synthetic Vidyalaya 2026-27")).toBeInTheDocument();
    expect(screen.getByText("2 of 3")).toBeInTheDocument();
    // A finance reader sees the status only: no agents, no groups.
    expect(screen.queryByText(en.devices.title)).toBeNull();
    expect(screen.queryByText(en.groups.title)).toBeNull();
    expect(stub.callsTo("GET /bff/api/v1/tally/devices")).toHaveLength(0);
    expect(screen.getByRole("link", { name: en.connector.toDues })).toHaveAttribute(
      "href",
      "/en/fees",
    );
  });

  it("makes a one-time enrolment code with a recent MFA sign-in and shows it once", async () => {
    signedIn([DEVICE_MANAGE]);
    connectorOn();
    stub.routes["POST /bff/api/v1/tally/enrolment-codes"] = () =>
      Response.json(
        {
          id: "0192f3a4-0000-7000-8000-00000000a301",
          code: CODE,
          device_name: "Accounts PC",
          expires_at: "2026-09-29T05:00:00Z",
        },
        { status: 201 },
      );
    renderWithIntl(<TallyConnectorScreen />);
    expect((await screen.findAllByText("Accounts PC")).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: en.enrol.add }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(messages.en.common.stepUpNote)).toBeInTheDocument();
    const name = within(dialog).getByLabelText(new RegExp(en.enrol.deviceName));
    await userEvent.clear(name);
    await userEvent.type(name, "Accounts PC");
    await userEvent.click(within(dialog).getByRole("button", { name: en.enrol.create }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/tally/enrolment-codes")).toHaveLength(1),
    );
    const call = stub.callsTo("POST /bff/api/v1/tally/enrolment-codes")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ device_name: "Accounts PC" });
    expect(call?.url.search).toBe("");
    expect(await screen.findByText(CODE)).toBeInTheDocument();
    expect(screen.getByText(en.enrol.onceTitle)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`--school ${TENANT} --code ${CODE}`))).toBeInTheDocument();
    // The code never goes into the URL (browser storage is forbidden by lint, docs/07 §5.2).
    expect(window.location.href).not.toContain(CODE);
    const done = screen.getByRole("button", { name: en.enrol.done });
    expect(done).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: en.enrol.handedOver }));
    expect(done).toBeEnabled();
    await userEvent.click(done);
    await waitFor(() => expect(screen.queryByText(CODE)).toBeNull());
  });

  it("says so when the school already has the most agents it may have", async () => {
    signedIn([DEVICE_MANAGE]);
    connectorOn();
    stub.routes["POST /bff/api/v1/tally/enrolment-codes"] = () => problem(409, "too_many_devices");
    renderWithIntl(<TallyConnectorScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.enrol.add }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: en.enrol.create }));
    expect(await screen.findByText(en.errors.too_many_devices.title)).toBeInTheDocument();
    expect(screen.getByText(en.errors.too_many_devices.body)).toBeInTheDocument();
  });

  it("revokes an agent with If-Match and a recent MFA sign-in", async () => {
    signedIn([DEVICE_MANAGE]);
    connectorOn();
    stub.routes[`POST /bff/api/v1/tally/devices/${DEVICE}/revoke`] = () =>
      Response.json(device({ status: "revoked", revoked_at: "2026-09-29T05:00:00Z", version: 3 }));
    renderWithIntl(<TallyConnectorScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.devices.revoke }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(en.devices.revokeBody)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: en.devices.revoke }));
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/tally/devices/${DEVICE}/revoke`)).toHaveLength(1),
    );
    const call = stub.callsTo(`POST /bff/api/v1/tally/devices/${DEVICE}/revoke`)[0];
    expect(call?.headers.get("If-Match")).toBe('W/"2"');
  });

  it("lets the accountant choose the ledger groups, keyboard only", async () => {
    signedIn([CONFIGURE]);
    connectorOn();
    stub.routes["PUT /bff/api/v1/tally/groups/selection"] = () =>
      Response.json([group(GROUP_DEBTORS, "Sundry Debtors", true)]);
    renderWithIntl(<TallyConnectorScreen />);
    const debtors = await screen.findByRole("checkbox", { name: /Sundry Debtors/ });
    expect(debtors).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Staff Salary/ })).not.toBeChecked();
    expect(screen.getByText(en.groups.privacyTitle)).toBeInTheDocument();
    // An accountant does not manage agents.
    expect(screen.queryByRole("button", { name: en.enrol.add })).toBeNull();
    debtors.focus();
    await userEvent.keyboard(" ");
    expect(debtors).not.toBeChecked();
    await userEvent.keyboard(" ");
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: en.groups.save })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() =>
      expect(stub.callsTo("PUT /bff/api/v1/tally/groups/selection")).toHaveLength(1),
    );
    expect(
      JSON.parse(stub.callsTo("PUT /bff/api/v1/tally/groups/selection")[0]?.body ?? "{}"),
    ).toEqual({ company: "Synthetic Vidyalaya 2026-27", group_ids: [GROUP_DEBTORS] });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved. 1 group is selected.");
  });

  it("renders in Telugu", async () => {
    signedIn([DEVICE_MANAGE, CONFIGURE]);
    connectorOn();
    renderWithIntl(<TallyConnectorScreen />, "te");
    expect(await screen.findByRole("heading", { name: te.connector.title })).toBeInTheDocument();
    expect(await screen.findByText(te.devices.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: te.enrol.add })).toBeInTheDocument();
  });
});

describe("ledger links (US-1803, FR-TALLY-006)", () => {
  it("lists the ledgers not linked yet and links one to a suggested student", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/parties"] = () => page([party()]);
    stub.routes[`GET /bff/api/v1/tally/parties/${PARTY}`] = () => Response.json(detail());
    stub.routes[`POST /bff/api/v1/tally/parties/${PARTY}/links`] = () =>
      Response.json(party({ links: [student] }), { status: 201 });
    renderWithIntl(<TallyLedgersScreen />);
    expect(await screen.findByText(LEDGER)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/tally/parties")[0]?.url.searchParams.get("link")).toBe(
      "unlinked",
    );
    expect(screen.getByText(en.balance.due)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: `Link ${LEDGER}` }));
    const panel = await screen.findByRole("region", { name: en.link.title });
    expect(within(panel).getByRole("heading", { name: en.link.title })).toHaveFocus();
    expect(within(panel).getByText(en.link.personDecides)).toBeInTheDocument();
    await userEvent.click(
      await within(panel).findByRole("button", {
        name: "Link to Synthetic Student One (S-0001, Class 6 A)",
      }),
    );
    await waitFor(() =>
      expect(stub.callsTo(`POST /bff/api/v1/tally/parties/${PARTY}/links`)).toHaveLength(1),
    );
    expect(
      JSON.parse(stub.callsTo(`POST /bff/api/v1/tally/parties/${PARTY}/links`)[0]?.body ?? "{}"),
    ).toEqual({ student_id: STUDENT });
    expect(await within(panel).findByText(en.link.linkedTitle)).toBeInTheDocument();
  });

  it("searches ledgers and students by name in a POST body, never in the URL (SEC-008)", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/parties"] = () => page([party()]);
    stub.routes["POST /bff/api/v1/tally/parties/search"] = () => page([party()]);
    stub.routes[`GET /bff/api/v1/tally/parties/${PARTY}`] = () =>
      Response.json(detail({ candidates: [] }));
    stub.routes["POST /bff/api/v1/students/search"] = () =>
      page([
        {
          id: SIBLING,
          display_name: "Synthetic Student Two",
          admission_no: "S-0002",
          class_section: "Class 3 B",
          status: "active",
          section_id: null,
          match: { field: "display_name", score: 0.9 },
        },
      ]);
    renderWithIntl(<TallyLedgersScreen />);
    await screen.findByText(LEDGER);
    await userEvent.type(screen.getByLabelText(en.ledgers.searchLabel), "Parent Ledger");
    await userEvent.click(screen.getByRole("button", { name: en.ledgers.search }));
    await waitFor(() =>
      expect(stub.callsTo("POST /bff/api/v1/tally/parties/search")).toHaveLength(1),
    );
    const call = stub.callsTo("POST /bff/api/v1/tally/parties/search")[0];
    expect(JSON.parse(call?.body ?? "{}")).toEqual({ query: "Parent Ledger", link: "unlinked" });
    expect(call?.url.search).not.toContain("Parent");

    await userEvent.click(await screen.findByRole("button", { name: `Link ${LEDGER}` }));
    const panel = await screen.findByRole("region", { name: en.link.title });
    expect(await within(panel).findByText(en.link.noCandidates)).toBeInTheDocument();
    await userEvent.type(within(panel).getByLabelText(en.link.searchLabel), "Student Two");
    await userEvent.click(within(panel).getByRole("button", { name: en.link.search }));
    expect(
      await within(panel).findByRole("button", {
        name: "Link to Synthetic Student Two (S-0002, Class 3 B)",
      }),
    ).toBeInTheDocument();
    const search = stub.callsTo("POST /bff/api/v1/students/search")[0];
    expect(JSON.parse(search?.body ?? "{}")).toEqual({ query: "Student Two", limit: 10 });
    expect(search?.url.search).toBe("");
  });

  it("explains a ledger that left Tally since the last sync", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/parties"] = () => page([party()]);
    stub.routes[`GET /bff/api/v1/tally/parties/${PARTY}`] = () => Response.json(detail());
    stub.routes[`POST /bff/api/v1/tally/parties/${PARTY}/links`] = () => problem(409, "party_gone");
    renderWithIntl(<TallyLedgersScreen />);
    await userEvent.click(await screen.findByRole("button", { name: `Link ${LEDGER}` }));
    const panel = await screen.findByRole("region", { name: en.link.title });
    await userEvent.click(
      await within(panel).findByRole("button", { name: /^Link to Synthetic Student One/ }),
    );
    expect(await within(panel).findByText(en.errors.party_gone.title)).toBeInTheDocument();
  });

  it("unlinks a student after confirming", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/parties"] = () => page([party({ links: [student] })]);
    stub.routes[`DELETE /bff/api/v1/tally/parties/${PARTY}/links/${STUDENT}`] = () =>
      new Response(null, { status: 204 });
    renderWithIntl(<TallyLedgersScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.ledgers.unlink }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Tally is not changed/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: en.ledgers.unlink }));
    await waitFor(() =>
      expect(
        stub.callsTo(`DELETE /bff/api/v1/tally/parties/${PARTY}/links/${STUDENT}`),
      ).toHaveLength(1),
    );
  });

  it("filters linked ledgers", async () => {
    signedIn([CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/parties"] = () => page([party()]);
    renderWithIntl(<TallyLedgersScreen />);
    await screen.findByText(LEDGER);
    await userEvent.click(screen.getByRole("radio", { name: en.ledgers.filters.linked }));
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/tally/parties")
          .some((call) => call.url.searchParams.get("link") === "linked"),
      ).toBe(true),
    );
  });

  it("explains who can link ledgers, without calling the API", async () => {
    signedIn([FINANCE_READ]);
    renderWithIntl(<TallyLedgersScreen />);
    expect(await screen.findByText(en.ledgers.noAccessTitle)).toBeInTheDocument();
    expect(stub.callsTo("GET /bff/api/v1/tally/parties")).toHaveLength(0);
  });
});

describe("fee dues (US-1804, FR-TALLY-007)", () => {
  it("shows the totals, the Tally date and the students with dues", async () => {
    signedIn([FINANCE_READ, CONFIGURE]);
    stub.routes["GET /bff/api/v1/tally/dues"] = () => Response.json(dues());
    renderWithIntl(<FeeDuesScreen />);
    expect(await screen.findByRole("link", { name: "Synthetic Student One" })).toHaveAttribute(
      "href",
      `/en/students/${STUDENT}`,
    );
    expect(screen.getAllByText("₹12,500.00").length).toBeGreaterThan(0);
    expect(screen.getByText(en.dues.unlinkedTitle)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: en.dues.linkLedgers })).toHaveAttribute(
      "href",
      "/en/settings/tally/ledgers",
    );
  });

  it("does not offer linking to a finance reader who cannot link", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/tally/dues"] = () => Response.json(dues());
    renderWithIntl(<FeeDuesScreen />);
    await screen.findByRole("link", { name: "Synthetic Student One" });
    expect(screen.queryByText(en.dues.unlinkedTitle)).toBeNull();
  });

  it("says the connector is off, or that fee dues are not for this person", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/tally/dues"] = () => problem(404, "not_found");
    const { unmount } = renderWithIntl(<FeeDuesScreen />);
    expect(await screen.findByText(en.off.title)).toBeInTheDocument();
    unmount();
    signedIn(["student.read_basic"]);
    renderWithIntl(<FeeDuesScreen />, "te");
    expect(await screen.findByText(te.dues.noAccessTitle)).toBeInTheDocument();
  });

  it("pages with the cursor the API gave", async () => {
    signedIn([FINANCE_READ]);
    stub.routes["GET /bff/api/v1/tally/dues"] = (_request, url) =>
      Response.json(url.searchParams.get("cursor") ? dues() : dues({ next_cursor: "c2" }));
    renderWithIntl(<FeeDuesScreen />);
    await userEvent.click(await screen.findByRole("button", { name: en.next }));
    await waitFor(() =>
      expect(
        stub
          .callsTo("GET /bff/api/v1/tally/dues")
          .some((call) => call.url.searchParams.get("cursor") === "c2"),
      ).toBe(true),
    );
    expect(await screen.findByRole("button", { name: en.previous })).toBeInTheDocument();
  });
});

describe("pages", () => {
  it("render the three Tally screens", async () => {
    signedIn([FINANCE_READ, CONFIGURE, DEVICE_MANAGE]);
    connectorOn();
    stub.routes["GET /bff/api/v1/tally/dues"] = () => Response.json(dues());
    stub.routes["GET /bff/api/v1/tally/parties"] = () => page([party()]);
    const first = renderWithIntl(<FeesPage />);
    expect(await screen.findByRole("heading", { name: en.dues.title, level: 1 })).toBeVisible();
    first.unmount();
    const second = renderWithIntl(<TallyConnectorPage />);
    expect(
      await screen.findByRole("heading", { name: en.connector.title, level: 1 }),
    ).toBeVisible();
    second.unmount();
    renderWithIntl(<TallyLedgersPage />);
    expect(await screen.findByRole("heading", { name: en.ledgers.title, level: 1 })).toBeVisible();
  });
});
