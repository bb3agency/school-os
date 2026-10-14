import { screen, within } from "@testing-library/react";
import type * as Navigation from "next/navigation";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolShell } from "@/components/shell/SchoolShell";
import type { Locale } from "@/i18n/routing";
import { intlErrors, messages, renderWithIntl } from "@/test/render";

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return {
    ...actual,
    usePathname: () => "/students",
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
    useParams: () => ({ locale: "en" }),
    notFound: () => {
      throw new Error("NEXT_NOT_FOUND");
    },
  };
});

import ImportPage from "./[locale]/(school)/imports/[importId]/page";
import ImportsPage from "./[locale]/(school)/imports/page";
import NextRegisterRowPage from "./[locale]/(school)/register-photos/[batchId]/next/page";
import RegisterPhotoBatchPage from "./[locale]/(school)/register-photos/[batchId]/page";
import RegisterRowPage from "./[locale]/(school)/register-photos/items/[itemId]/page";
import RegisterPhotosPage from "./[locale]/(school)/register-photos/page";
import StudentPage from "./[locale]/(school)/students/[studentId]/page";
import NewStudentPage from "./[locale]/(school)/students/new/page";
import StudentsPage from "./[locale]/(school)/students/page";

type Messages = (typeof messages)["en"];
const UUID = "0192f3a4-0000-7000-8000-00000000c101";
const params = <K extends string>(key: K, value = UUID) =>
  Promise.resolve({ locale: "en", [key]: value } as { locale: string } & Record<K, string>);
const noSearch = () => Promise.resolve({});

const pages: Array<{
  name: string;
  title: (m: Messages) => string;
  render: () => ReactElement | Promise<ReactElement>;
}> = [
  { name: "students", title: (m) => m.students.list.title, render: () => <StudentsPage /> },
  {
    name: "add a student",
    title: (m) => m.students.create.title,
    render: () => <NewStudentPage />,
  },
  {
    name: "one student",
    title: (m) => m.students.detail.loadingTitle,
    render: () => StudentPage({ params: params("studentId") }),
  },
  { name: "imports", title: (m) => m.imports.list.title, render: () => <ImportsPage /> },
  {
    name: "one import",
    title: (m) => m.imports.detail.loadingTitle,
    render: () => ImportPage({ params: params("importId"), searchParams: noSearch() }),
  },
  {
    name: "register photos",
    title: (m) => m.extraction.list.title,
    render: () => <RegisterPhotosPage />,
  },
  {
    name: "one batch",
    title: (m) => m.extraction.batch.loadingTitle,
    render: () => RegisterPhotoBatchPage({ params: params("batchId") }),
  },
  {
    name: "one register row",
    title: (m) => m.extraction.review.loadingTitle,
    render: () => RegisterRowPage({ params: params("itemId"), searchParams: noSearch() }),
  },
  {
    name: "next register row",
    title: (m) => m.extraction.review.openingNext,
    render: () => NextRegisterRowPage({ params: params("batchId"), searchParams: noSearch() }),
  },
];

// Screens load through the BFF; here the API answers 404 for everything.
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ status: 404, code: "not_found" }, { status: 404 })),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("student, import and register-photo pages render in both languages (NFR-I18N-001)", () => {
  for (const locale of ["en", "te"] as Locale[]) {
    for (const page of pages) {
      it(`${page.name} [${locale}]`, async () => {
        renderWithIntl(await page.render(), locale);
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
          page.title(messages[locale]),
        );
        expect(intlErrors).toEqual([]);
      });
    }
  }

  it("refuses ids that are not UUIDs (404) before calling the API", async () => {
    await expect(StudentPage({ params: params("studentId", "../me") })).rejects.toThrow(
      "NEXT_NOT_FOUND",
    );
    await expect(
      ImportPage({ params: params("importId", "x"), searchParams: noSearch() }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    await expect(
      RegisterRowPage({ params: params("itemId", "1"), searchParams: noSearch() }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("the school menu offers the new screens only with their permission", () => {
    renderWithIntl(<SchoolShell permissions={["student.read_basic"]}>content</SchoolShell>, "en");
    const nav = screen.getByRole("navigation", { name: messages.en.school.nav.label });
    expect(within(nav).getByRole("link", { name: messages.en.students.nav })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: messages.en.imports.nav })).toBeNull();
    expect(within(nav).queryByRole("link", { name: messages.en.extraction.nav })).toBeNull();
  });
});
