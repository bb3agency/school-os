import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithIntl } from "@/test/render";
import { ready, loading, loadError } from "@/lib/loadable";
import { Alert } from "./Alert";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { TextAreaField, TextField } from "./Input";
import { SelectField } from "./Select";
import { StatCard } from "./StatCard";
import { DataTable } from "./Table";
import { Tabs } from "./Tabs";

describe("form controls: labels and descriptions are associated (NFR-A11Y-001)", () => {
  it("TextField links label, hint and error to the input", () => {
    renderWithIntl(
      <TextField
        name="n"
        label="School name"
        hint="As parents know it."
        error="Fill in this field."
      />,
    );
    const input = screen.getByLabelText("School name");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("As parents know it. Fill in this field.");
  });

  it("valid fields are not marked invalid", () => {
    renderWithIntl(<TextField name="n" label="Email" />);
    expect(screen.getByLabelText("Email")).not.toHaveAttribute("aria-invalid");
    expect(screen.getByLabelText("Email")).not.toHaveAttribute("aria-describedby");
  });

  it("SelectField and TextAreaField are labelled", () => {
    renderWithIntl(
      <>
        <SelectField
          name="plan"
          label="Plan"
          placeholder="Choose a plan"
          options={[{ value: "a", label: "A" }]}
        />
        <TextAreaField name="body" label="Message" hint="Both languages." />
      </>,
    );
    expect(screen.getByRole("combobox", { name: "Plan" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveAccessibleDescription(
      "Both languages.",
    );
  });

  it("every generated id is unique", () => {
    renderWithIntl(
      <>
        <TextField name="a" label="A" hint="h" />
        <TextField name="b" label="B" hint="h" />
      </>,
    );
    const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("Button", () => {
  it("never submits a form by accident", () => {
    renderWithIntl(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("type", "button");
  });
});

describe("Dialog (native <dialog>)", () => {
  it("opens with an accessible name and returns focus to the trigger on close", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <Dialog
        title="Invite operator"
        triggerLabel="Invite"
        closeLabel="Close"
        description="Step-up needed."
      >
        <p>Body</p>
      </Dialog>,
    );
    const trigger = screen.getByRole("button", { name: "Invite" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Invite operator" });
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toHaveAccessibleDescription("Step-up needed.");
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(dialog).not.toHaveAttribute("open");
    expect(trigger).toHaveFocus();
  });
});

describe("Tabs (WAI-ARIA tabs pattern)", () => {
  const items = [
    { id: "en", label: "English", panel: <p>EN panel</p> },
    { id: "te", label: "Telugu", panel: <p>TE panel</p> },
  ];

  it("supports arrow-key navigation with a roving tabindex", async () => {
    const user = userEvent.setup();
    renderWithIntl(<Tabs label="Language" items={items} />);
    const [english, telugu] = screen.getAllByRole("tab");
    expect(screen.getByRole("tablist", { name: "Language" })).toBeInTheDocument();
    expect(english).toHaveAttribute("aria-selected", "true");
    expect(telugu).toHaveAttribute("tabindex", "-1");

    await user.tab();
    expect(english).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(telugu).toHaveFocus();
    expect(telugu).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Telugu" })).toHaveTextContent("TE panel");
    await user.keyboard("{ArrowRight}");
    expect(english).toHaveFocus();
  });

  it("keeps inactive panels in the DOM (hidden) so form values survive", () => {
    renderWithIntl(<Tabs label="Language" items={items} />);
    expect(
      screen.getByText("TE panel", { ignore: false }).closest("[role=tabpanel]"),
    ).toHaveAttribute("hidden");
  });
});

describe("DataTable states", () => {
  const columns = [
    { key: "name", header: "Name", cell: (row: { id: string; name: string }) => row.name },
  ];
  const props = {
    caption: "Schools",
    columns,
    rowKey: (row: { id: string }) => row.id,
    emptyTitle: "No schools yet",
  };

  it("shows a status message while loading", () => {
    renderWithIntl(<DataTable {...props} state={loading} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
  });

  it("shows a fix-it message on error", () => {
    renderWithIntl(<DataTable {...props} state={loadError} />);
    expect(screen.getByText("We couldn't load this information")).toBeInTheDocument();
  });

  it("shows the empty state", () => {
    renderWithIntl(<DataTable {...props} state={ready([])} />);
    expect(screen.getByText("No schools yet")).toBeInTheDocument();
  });

  it("renders a captioned table in a focusable scroll region with column headers", () => {
    renderWithIntl(<DataTable {...props} state={ready([{ id: "1", name: "Sample school" }])} />);
    expect(screen.getByRole("region", { name: /Schools/ })).toHaveAttribute("tabindex", "0");
    const table = screen.getByRole("table", { name: "Schools" });
    expect(within(table).getByRole("columnheader", { name: "Name" })).toHaveAttribute(
      "scope",
      "col",
    );
    expect(within(table).getByRole("cell", { name: "Sample school" })).toBeInTheDocument();
  });
});

describe("StatCard and Alert", () => {
  it("reads '—' as 'Not available'", () => {
    renderWithIntl(
      <dl>
        <StatCard label="MRR" value={null} unavailableLabel="Not available" />
      </dl>,
    );
    expect(screen.getByText("Not available")).toHaveClass("sr-only");
  });

  it("only announces alerts that appear after an action", () => {
    renderWithIntl(
      <>
        <Alert tone="warning">Static</Alert>
        <Alert tone="danger" live>
          Result
        </Alert>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Result");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
