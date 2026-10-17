import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
import { Drawer } from "./Drawer";
import { ErrorSummary } from "./ErrorSummary";
import { FilterChips } from "./FilterChips";
import { Menu } from "./Menu";
import { Pagination } from "./Pagination";
import { Popover } from "./Popover";
import { SaveBar } from "./SaveBar";
import { Steps } from "./Steps";
import { TOAST_MS, ToastProvider, useToast } from "./Toaster";

/** docs/17 §4.2-§4.4: overlays, feedback and form patterns (NFR-A11Y-001). */

describe("Menu (APG menu button)", () => {
  function setup() {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    renderWithIntl(
      <>
        <Menu
          label="Actions for Sample student A"
          entries={[
            { id: "edit", label: "Edit", onSelect: onEdit, icon: "pencil" },
            { id: "open", kind: "link", label: "Open record", href: "/students/1" },
            { id: "sep", kind: "separator" },
            { id: "delete", label: "Delete", onSelect: onDelete, danger: true },
          ]}
        />
        <button type="button">Outside</button>
      </>,
    );
    return {
      onEdit,
      onDelete,
      button: screen.getByRole("button", { name: "Actions for Sample student A" }),
    };
  }

  it("opens from the keyboard on the first item; arrows wrap; Escape returns focus", async () => {
    const user = userEvent.setup();
    const { button } = setup();
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.focus();
    await user.keyboard("{Enter}");
    const menu = screen.getByRole("menu", { name: "Actions for Sample student A" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(within(menu).getByRole("menuitem", { name: "Edit" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(within(menu).getByRole("menuitem", { name: "Open record" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(within(menu).getByRole("menuitem", { name: "Edit" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(within(menu).getByRole("menuitem", { name: "Delete" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("Up opens on the last item; a letter jumps; Enter runs and closes", async () => {
    const user = userEvent.setup();
    const { button, onDelete } = setup();
    button.focus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Delete" })).toHaveFocus();
    await user.keyboard("o");
    expect(screen.getByRole("menuitem", { name: "Open record" })).toHaveAttribute(
      "href",
      "/students/1",
    );
    expect(screen.getByRole("menuitem", { name: "Open record" })).toHaveFocus();
    await user.keyboard("d{Enter}");
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a click outside closes it", async () => {
    const user = userEvent.setup();
    const { button } = setup();
    await user.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("radio items report their state", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <Menu
        label="Row density"
        entries={[
          { kind: "radio", id: "a", label: "Comfortable", checked: true, onSelect: vi.fn() },
          { kind: "radio", id: "b", label: "Compact", checked: false, onSelect: vi.fn() },
        ]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Row density" }));
    expect(screen.getByRole("menuitemradio", { name: "Comfortable" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("menuitemradio", { name: "Compact" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });
});

describe("Popover (disclosure, non-modal)", () => {
  it("opens a named panel, Escape closes it and returns focus", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <Popover trigger="Columns" title="Show columns">
        <label>
          <input type="checkbox" /> Class
        </label>
      </Popover>,
    );
    const button = screen.getByRole("button", { name: "Columns" });
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("dialog", { name: "Show columns" });
    expect(panel).toHaveFocus();
    expect(panel).not.toHaveAttribute("aria-modal");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button).toHaveFocus();
  });
});

function ToastButton() {
  const { show } = useToast();
  return (
    <button type="button" onClick={() => show({ title: "Receipt sent", body: "On WhatsApp" })}>
      Send
    </button>
  );
}

describe("Toaster (CSS-only status region)", () => {
  it("announces in one polite status region, dismisses by button and by time", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderWithIntl(
        <ToastProvider>
          <ToastButton />
        </ToastProvider>,
      );
      const region = screen.getByRole("status", { name: "Notifications from SchoolOS" });
      expect(region).toHaveAttribute("aria-live", "polite");
      expect(region).toBeEmptyDOMElement();
      await user.click(screen.getByRole("button", { name: "Send" }));
      expect(region).toHaveTextContent("Receipt sent");
      await user.click(within(region).getByRole("button", { name: "Dismiss" }));
      expect(region).toBeEmptyDOMElement();
      await user.click(screen.getByRole("button", { name: "Send" }));
      act(() => {
        vi.advanceTimersByTime(TOAST_MS + 10);
      });
      expect(region).toBeEmptyDOMElement();
    } finally {
      vi.useRealTimers();
    }
  });

  it("outside a provider, show() is a safe no-op", async () => {
    const user = userEvent.setup();
    renderWithIntl(<ToastButton />);
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.queryByText("Receipt sent")).toBeNull();
  });
});

describe("Pagination", () => {
  it("names the range in Indian grouping and disables what cannot be used", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const onSize = vi.fn();
    renderWithIntl(
      <Pagination
        from={1}
        to={50}
        total={1240}
        hasPrevious={false}
        hasNext
        onPrevious={vi.fn()}
        onNext={onNext}
        pageSize={50}
        pageSizes={[25, 50, 100]}
        onPageSizeChange={onSize}
      />,
    );
    const nav = screen.getByRole("navigation", { name: "Pages" });
    expect(nav).toHaveTextContent("1–50 of 1,240");
    expect(within(nav).getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(within(nav).getByRole("button", { name: "Next" }));
    expect(onNext).toHaveBeenCalled();
    await user.selectOptions(within(nav).getByRole("combobox", { name: "Rows per page" }), "100");
    expect(onSize).toHaveBeenCalledWith(100);
  });
});

describe("ErrorSummary (GOV.UK pattern)", () => {
  it("takes focus, lists each problem as a link that focuses its field", async () => {
    const user = userEvent.setup();
    renderWithIntl(
      <form>
        <ErrorSummary errors={[{ fieldId: "dob", message: "Enter the date of birth" }]} />
        <label htmlFor="dob">Date of birth</label>
        <input id="dob" />
      </form>,
    );
    const summary = screen.getByRole("alert");
    expect(summary).toHaveFocus();
    expect(
      within(summary).getByRole("heading", { name: "There is a problem" }),
    ).toBeInTheDocument();
    await user.click(within(summary).getByRole("link", { name: "Enter the date of birth" }));
    expect(screen.getByLabelText("Date of birth")).toHaveFocus();
  });

  it("renders nothing without errors", () => {
    const { container } = renderWithIntl(<ErrorSummary errors={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Steps", () => {
  it("marks done, current and upcoming without relying on colour", () => {
    renderWithIntl(
      <Steps
        label="Import steps"
        current={1}
        steps={[
          { id: "upload", label: "Upload", href: "/imports/1" },
          { id: "check", label: "Check rows" },
          { id: "apply", label: "Apply" },
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Import steps" });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("UploadDone");
    expect(within(items[0] as HTMLElement).getByRole("link")).toHaveAttribute("href", "/imports/1");
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(items[2]).toHaveTextContent("ApplyNot started");
  });
});

describe("SaveBar and FilterChips", () => {
  it("SaveBar says whether changes are unsaved", () => {
    const { unmount } = renderWithIntl(
      <SaveBar dirty>
        <button type="submit">Save changes</button>
      </SaveBar>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Changes not saved");
    unmount();
    renderWithIntl(
      <SaveBar dirty={false}>
        <button type="submit">Save changes</button>
      </SaveBar>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No changes");
  });

  it("chips have named remove buttons and Clear all", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    const onClear = vi.fn();
    renderWithIntl(
      <FilterChips
        chips={[
          { id: "class", label: "Class", value: "7-B" },
          { id: "status", label: "Status", value: "Active" },
        ]}
        onRemove={onRemove}
        onClear={onClear}
      />,
    );
    expect(screen.getByRole("list", { name: "Active filters" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove filter Class: 7-B" }));
    expect(onRemove).toHaveBeenCalledWith("class");
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onClear).toHaveBeenCalled();
  });
});

function DrawerHost() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Details
      </button>
      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title="Sample student A"
        closeLabel="Close"
      >
        <p>Class 7-B</p>
      </Drawer>
    </>
  );
}

describe("Drawer (side sheet on the native dialog)", () => {
  it("opens modal and named, closes with its button and returns focus", async () => {
    const user = userEvent.setup();
    renderWithIntl(<DrawerHost />);
    const opener = screen.getByRole("button", { name: "Details" });
    await user.click(opener);
    const drawer = screen.getByRole("dialog", { name: "Sample student A" });
    expect(drawer).toHaveAttribute("open");
    expect(drawer).toHaveClass("drawer", "drawer-end");
    expect(within(drawer).getByText("Class 7-B")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(drawer).not.toHaveAttribute("open");
    expect(opener).toHaveFocus();
  });

  it("a click on the backdrop closes it", async () => {
    const user = userEvent.setup();
    renderWithIntl(<DrawerHost />);
    await user.click(screen.getByRole("button", { name: "Details" }));
    const drawer = screen.getByRole("dialog", { name: "Sample student A" });
    fireEvent.click(drawer);
    expect(drawer).not.toHaveAttribute("open");
  });
});
