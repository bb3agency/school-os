import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlatformShell } from "@/components/shell/PlatformShell";
import { SchoolShell } from "@/components/shell/SchoolShell";
import type { Locale } from "@/i18n/routing";
import { intlErrors, messages, renderWithIntl } from "@/test/render";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/en/platform/schools",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
  };
});

import SchoolHomePage from "./[locale]/(school)/page";
import SchoolAuditPage from "./[locale]/(school)/audit/page";
import SignedOutPage from "./[locale]/signed-out/page";
import SchoolBillingPage from "./[locale]/(school)/settings/billing/page";
import SchoolStructurePage from "./[locale]/(school)/settings/structure/page";
import SchoolUsersPage from "./[locale]/(school)/settings/users/page";
import PlatformAnnouncementsPage from "./[locale]/platform/announcements/page";
import PlatformAuditPage from "./[locale]/platform/audit/page";
import PlatformBreakGlassPage from "./[locale]/platform/break-glass/page";
import PlatformFlagsPage from "./[locale]/platform/flags/page";
import PlatformFleetPage from "./[locale]/platform/fleet/page";
import PlatformInvoicesPage from "./[locale]/platform/invoices/page";
import PlatformOperatorsPage from "./[locale]/platform/operators/page";
import PlatformDashboardPage from "./[locale]/platform/page";
import PlatformPlansPage from "./[locale]/platform/plans/page";
import ProvisionSchoolPage from "./[locale]/platform/provision/page";
import PlatformSchoolDetailPage from "./[locale]/platform/schools/[schoolId]/page";
import PlatformSchoolsPage from "./[locale]/platform/schools/page";
import PlatformSubscriptionsPage from "./[locale]/platform/subscriptions/page";
import PlatformSupportPage from "./[locale]/platform/support/page";
import PlatformUsagePage from "./[locale]/platform/usage/page";
import SchoolSupportPage from "./[locale]/(school)/support/page";

type Messages = (typeof messages)["en"];
type PageCase = {
  name: string;
  title: (m: Messages) => string;
  render: () => ReactElement | Promise<ReactElement>;
};

const SCHOOL_ID = "0192f3a4-0000-7000-8000-000000000001";
const noSearch = () => Promise.resolve({});

const pages: PageCase[] = [
  { name: "school home", title: (m) => m.school.home.title, render: () => <SchoolHomePage /> },
  {
    name: "school structure",
    title: (m) => m.school.structure.title,
    render: () => <SchoolStructurePage />,
  },
  {
    name: "users and roles",
    title: (m) => m.school.users.title,
    render: () => <SchoolUsersPage />,
  },
  {
    name: "plan and billing",
    title: (m) => m.school.billing.title,
    render: () => <SchoolBillingPage />,
  },
  {
    name: "school support",
    title: (m) => m.school.support.title,
    render: () => <SchoolSupportPage />,
  },
  {
    name: "school audit log",
    title: (m) => m.school.audit.title,
    render: () => SchoolAuditPage({ searchParams: noSearch() }),
  },
  {
    name: "signed out",
    title: (m) => m.auth.signedOut.title,
    render: () =>
      SignedOutPage({ searchParams: Promise.resolve({ error: "signin_failed", reason: "idle" }) }),
  },
  {
    name: "platform dashboard",
    title: (m) => m.platform.dashboard.title,
    render: () => <PlatformDashboardPage />,
  },
  {
    name: "schools list",
    title: (m) => m.platform.schools.title,
    render: () => PlatformSchoolsPage({ searchParams: noSearch() }),
  },
  {
    name: "school detail",
    title: (m) => m.platform.schoolDetail.title,
    render: () =>
      PlatformSchoolDetailPage({
        params: Promise.resolve({ locale: "en", schoolId: SCHOOL_ID }),
        searchParams: Promise.resolve({ tab: "invoices" }),
      }),
  },
  {
    name: "provision school",
    title: (m) => m.platform.provision.title,
    render: () => <ProvisionSchoolPage />,
  },
  { name: "plans", title: (m) => m.platform.plans.title, render: () => PlatformPlansPage({ searchParams: noSearch() }) },
  {
    name: "subscriptions",
    title: (m) => m.platform.subscriptions.title,
    render: () => PlatformSubscriptionsPage({ searchParams: noSearch() }),
  },
  {
    name: "invoices",
    title: (m) => m.platform.invoices.title,
    render: () => PlatformInvoicesPage({ searchParams: noSearch() }),
  },
  { name: "usage", title: (m) => m.platform.usage.title, render: () => PlatformUsagePage({ searchParams: noSearch() }) },
  { name: "flags", title: (m) => m.platform.flags.title, render: () => <PlatformFlagsPage /> },
  { name: "fleet", title: (m) => m.platform.fleet.title, render: () => PlatformFleetPage({ searchParams: noSearch() }) },
  {
    name: "announcements",
    title: (m) => m.platform.announcements.title,
    render: () => <PlatformAnnouncementsPage />,
  },
  {
    name: "support tickets",
    title: (m) => m.platform.support.title,
    render: () => PlatformSupportPage({ searchParams: noSearch() }),
  },
  {
    name: "break-glass",
    title: (m) => m.platform.breakGlass.title,
    render: () => <PlatformBreakGlassPage />,
  },
  {
    name: "operators",
    title: (m) => m.platform.operators.title,
    render: () => <PlatformOperatorsPage />,
  },
  {
    name: "platform audit",
    title: (m) => m.platform.audit.title,
    render: () => PlatformAuditPage({ searchParams: noSearch() }),
  },
];

const locales: Locale[] = ["en", "te"];

// Screens load through the BFF; here the API "does not exist yet" (404).
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ status: 404, code: "not_found" }, { status: 404 })),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("page shells render in both languages (NFR-I18N-001, FR-PLT-001..030)", () => {
  for (const locale of locales) {
    for (const page of pages) {
      it(`${page.name} [${locale}]`, async () => {
        renderWithIntl(await page.render(), locale);
        const heading = screen.getByRole("heading", { level: 1 });
        expect(heading).toHaveTextContent(page.title(messages[locale]));
        // No missing keys or ICU argument errors while rendering.
        expect(intlErrors).toEqual([]);
      });
    }
  }

  it("support tickets always show the student-data warning (operator and school)", async () => {
    renderWithIntl(await PlatformSupportPage({ searchParams: noSearch() }), "te");
    expect(screen.getByText(messages.te.support.piiWarningTitle)).toBeVisible();
    renderWithIntl(<SchoolSupportPage />, "en");
    expect(screen.getByText(messages.en.support.piiWarningTitle)).toBeVisible();
  });

  it("dashboard shows — for every KPI while nothing has loaded", () => {
    renderWithIntl(<PlatformDashboardPage />, "en");
    const kpis = screen.getByRole("region", { name: messages.en.platform.dashboard.kpisLabel });
    expect(within(kpis).getAllByText("—")).toHaveLength(9);
    expect(within(kpis).getAllByText(messages.en.common.notAvailable)).toHaveLength(9);
  });
});

describe("app shells", () => {
  it("school shell has a skip link, main landmark, nav and language switcher", () => {
    renderWithIntl(<SchoolShell>content</SchoolShell>, "te");
    expect(screen.getByRole("link", { name: messages.te.common.skipToContent })).toHaveAttribute(
      "href",
      "#main",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(
      screen.getByRole("navigation", { name: messages.te.school.nav.label }),
    ).toBeInTheDocument();
    const languages = screen.getByRole("navigation", { name: messages.te.language.label });
    expect(within(languages).getByRole("link", { name: "English" })).toHaveAttribute("lang", "en");
    expect(screen.queryByText(messages.te.platform.badge)).not.toBeInTheDocument();
  });

  it("platform shell is visibly marked as the platform admin panel", () => {
    renderWithIntl(<PlatformShell>content</PlatformShell>, "en");
    expect(screen.getByText(messages.en.platform.badge)).toBeVisible();
    const nav = screen.getByRole("navigation", { name: messages.en.platform.nav.label });
    expect(
      within(nav).getByRole("link", { name: messages.en.platform.nav.schools }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(nav).getByRole("link", { name: messages.en.platform.nav.dashboard }),
    ).not.toHaveAttribute("aria-current");
  });
});
