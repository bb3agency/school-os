import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { Loadable } from "@/lib/loadable";
import { messages, renderWithIntl } from "@/test/render";
import { Alert } from "./Alert";
import { Button, IconButton } from "./Button";
import { LoadFade } from "./LoadFade";
import { KpiCard } from "./StatCard";
import { DataTable } from "./Table";
import { TickValue } from "./TickValue";

/**
 * Component states and their (CSS-only) motion hooks (docs/17 §5.5; NFR-A11Y-001).
 * jsdom applies no CSS: these tests pin which elements carry the motion classes and, above
 * all, that nothing animates on a first render.
 */

describe("Button states", () => {
  it("presses (the .pressable feedback) and keeps its colour fade to hover only", () => {
    render(<Button>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveClass("pressable");
    // Colour never transitions on a variant change or a keyboard press (WCAG 1.4.3).
    expect(button.className).not.toMatch(/(^|\s)transition-colors/);
  });

  it("loading: aria-busy, a decorative spinner, the label stays", () => {
    render(
      <Button loading disabled>
        Working…
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Working…" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    const spinner = button.querySelector("svg");
    expect(spinner).toHaveAttribute("aria-hidden", "true");
  });

  it("not loading: no aria-busy and no spinner (backward compatible)", () => {
    render(<Button>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button.querySelector("svg")).toBeNull();
  });

  it("icon buttons press too", () => {
    render(
      <IconButton label="Add">
        <span>+</span>
      </IconButton>,
    );
    expect(screen.getByRole("button", { name: "Add" })).toHaveClass("pressable");
  });
});

describe("Alert", () => {
  it("rises in only when it appears after an action (live)", () => {
    const { rerender } = render(<Alert tone="success">Saved</Alert>);
    expect(screen.getByText("Saved").closest("div.flex")).not.toHaveClass("alert-in");
    rerender(
      <Alert tone="success" live>
        Saved
      </Alert>,
    );
    expect(screen.getByRole("status")).toHaveClass("alert-in");
  });
});

describe("TickValue (KPI number change)", () => {
  it("does not animate the first value or the first data after '—'", () => {
    const { container, rerender } = render(<TickValue value={null}>—</TickValue>);
    expect(container.querySelector(".value-tick")).toBeNull();
    rerender(<TickValue value="12">12</TickValue>);
    expect(container.querySelector(".value-tick")).toBeNull();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("a changed real value rises in, once per change", () => {
    const { container, rerender } = render(<TickValue value="12">12</TickValue>);
    rerender(<TickValue value="13">13</TickValue>);
    expect(container.querySelector(".value-tick")).toHaveTextContent("13");
    expect(container.querySelector("[data-tick]")).toHaveAttribute("data-tick", "1");
    rerender(<TickValue value="13">13</TickValue>);
    expect(container.querySelector("[data-tick]")).toHaveAttribute("data-tick", "1");
  });

  it("KpiCard keeps its label/value group and '—' for a missing value", () => {
    render(<KpiCard label="Open checks" value={null} unavailableLabel="Not available" />);
    const group = screen.getByRole("group", { name: "Open checks" });
    expect(group).toHaveTextContent("—");
    expect(screen.getByText("Not available")).toHaveClass("sr-only");
  });
});

describe("LoadFade (skeleton → content)", () => {
  it("content that replaces a placeholder fades in", () => {
    const { container, rerender } = render(<LoadFade loading>Loading</LoadFade>);
    expect(container.querySelector(".content-in")).toBeNull();
    rerender(<LoadFade loading={false}>Ready</LoadFade>);
    expect(container.querySelector(".content-in")).toHaveTextContent("Ready");
  });

  it("content that was ready at first render shows at once (no page-load animation)", () => {
    const { container } = render(<LoadFade loading={false}>Ready</LoadFade>);
    expect(container.querySelector(".content-in")).toBeNull();
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("DataTable: skeleton first, then the table fades in", () => {
    const columns = [{ key: "name", header: "Name", cell: (row: { id: string }) => row.id }];
    const wrapper = ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider locale="en" messages={messages.englishOnly} timeZone="Asia/Kolkata">
        {children}
      </NextIntlClientProvider>
    );
    const table = (state: Loadable<readonly { id: string }[]>) => (
      <DataTable
        caption="Students"
        columns={columns}
        state={state}
        rowKey={(row) => row.id}
        emptyTitle="None"
      />
    );
    const { container, rerender } = render(table({ status: "loading" }), { wrapper });
    expect(screen.getByRole("status")).toBeInTheDocument();
    rerender(table({ status: "ready", data: [{ id: "Sample student A" }] }));
    expect(container.querySelector(".content-in table")).not.toBeNull();
    expect(screen.getByRole("cell", { name: "Sample student A" })).toBeInTheDocument();
  });

  it("DataTable with data at first render: no fade", () => {
    renderWithIntl(
      <DataTable
        caption="Students"
        columns={[{ key: "name", header: "Name", cell: (row: { id: string }) => row.id }]}
        state={{ status: "ready", data: [{ id: "Sample student A" }] }}
        rowKey={(row) => row.id}
        emptyTitle="None"
      />,
    );
    expect(document.querySelector(".content-in")).toBeNull();
    expect(screen.getByRole("table")).toHaveClass("tabular-nums");
  });
});
