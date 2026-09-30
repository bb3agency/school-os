import { render, screen } from "@testing-library/react";
import { m, MotionConfigContext } from "motion/react";
import type * as Navigation from "next/navigation";
import { NextIntlClientProvider } from "next-intl";
import { useContext } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/Button";
import { KpiCard } from "@/components/ui/StatCard";
import { DataTable } from "@/components/ui/Table";
import { messages } from "@/test/render";
import { AppShell } from "./AppShell";
import { MotionProvider } from "./MotionProvider";

/** Motion foundation (docs/17 §5.5; NFR-A11Y-001, SEC-010). */

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof Navigation>();
  return { ...actual, usePathname: () => "/en/students" };
});

function ReducedMotionProbe() {
  const config = useContext(MotionConfigContext);
  return (
    <p>
      {config.reducedMotion} {String(config.transition?.duration)}
    </p>
  );
}

describe("MotionProvider (docs/17 §5.5)", () => {
  it("renders its children without a wrapper element and allows the light m.* components", () => {
    const { container } = render(
      <MotionProvider>
        <m.p>Inside</m.p>
      </MotionProvider>,
    );
    expect(container.firstElementChild?.tagName).toBe("P");
    expect(screen.getByText("Inside")).toBeInTheDocument();
  });

  it("follows the viewer's prefers-reduced-motion (reducedMotion='user')", () => {
    render(
      <MotionProvider>
        <ReducedMotionProbe />
      </MotionProvider>,
    );
    // "user": with prefers-reduced-motion, transforms jump and only opacity fades. The
    // default transition is the shared 200ms token.
    expect(screen.getByText("user 0.2")).toBeInTheDocument();
  });
});

describe("server HTML of the console has no style attributes (CSP, SEC-010)", () => {
  it("AppShell with buttons, KPI cards and a data table renders no inline style", () => {
    const html = renderToString(
      <NextIntlClientProvider locale="en" messages={messages.englishOnly} timeZone="Asia/Kolkata">
        <AppShell
          homeHref="/"
          navLabel="Main"
          sections={[
            {
              id: "records",
              label: "Records",
              items: [{ href: "/students", label: "Students", icon: "users" }],
            },
          ]}
          topbarActions={<Button variant="secondary">Tool</Button>}
        >
          <h1>Page</h1>
          <KpiCard label="Open checks" value="12" unavailableLabel="Not available" />
          <Button loading disabled>
            Working…
          </Button>
          <DataTable
            caption="Sample"
            columns={[{ key: "name", header: "Name", cell: (row: { id: string }) => row.id }]}
            state={{ status: "ready", data: [{ id: "Sample student A" }] }}
            rowKey={(row) => row.id}
            emptyTitle="Nothing yet"
          />
        </AppShell>
      </NextIntlClientProvider>,
    );
    expect(html).toContain("Sample student A");
    expect(html).not.toMatch(/\sstyle=/);
  });
});
