import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BusyLink, ExpiredLink, LinkFrame, PaidPage, type LinkEnvelope } from "./PublicPages";

/**
 * Parent pages (ADR-0044, ADR-0046 decision 2; FR-PUB-008, FR-PUB-009; US-2401..US-2403; docs/17
 * §5.8; wave R2-3H). Synthetic school and child; worst-case values from the design review
 * catalogue (long names, en-IN grouping, crores).
 */

const TELUGU = /[ఀ-౿]/;
const SCHOOL = "Sri Venkateswara Vidya Niketan English Medium High School, Kakinada";
const frame = {
  expires_at: "2026-11-16T10:00:00+00:00",
  language: "en" as const,
  school_name: SCHOOL,
  notice: null,
  receipt: null,
  dues: null,
  optin: null,
};

const notice: LinkEnvelope = {
  ...frame,
  kind: "parent_notice",
  notice: {
    title: "Sports day",
    body: "Sports day is on 14/11/2026 at 9:00.\n\nChildren come in sports uniform.\nBring water.",
    date: "2026-10-17",
    has_pdf: true,
  },
};

const receipt: LinkEnvelope = {
  ...frame,
  kind: "parent_receipt",
  receipt: {
    receipt_number: "RCT/2026-27/000412",
    receipt_date: "2026-10-17",
    student_name: "Venkata Naga Sai Lakshmi Prasanna Kumari Bhimavarapu",
    class_section: "IX-A",
    admission_no: "2019/0457/A",
    lines: [
      { label: "Term 1", head_name: "Tuition fee", amount: "12345678.50" },
      { label: "Term 1", head_name: "Late fee", amount: "0.00" },
    ],
    amount: "12345678.50",
    amount_in_words:
      "One crore twenty-three lakh forty-five thousand six hundred seventy-eight rupees and fifty paise only",
    mode: "upi_qr",
    cancelled_on: null,
    has_pdf: false,
  },
};

const dues: LinkEnvelope = {
  ...frame,
  kind: "parent_dues",
  dues: {
    child_first_name: "Venkata",
    class_name: "Class IX",
    as_of: "2026-10-18",
    instalments: [
      {
        instalment_no: 1,
        label: "Term 1",
        due_on: "2026-09-18",
        due: "3550.00",
        late_fee: "50.00",
        overdue: true,
      },
      {
        instalment_no: 2,
        label: "Term 2",
        due_on: "2026-11-17",
        due: "3500.00",
        late_fee: "0.00",
        overdue: false,
      },
    ],
    total_due: "7050.00",
    late_fee_total: "50.00",
    pay_online: true,
    upi: {
      payee_name: "Synthetic Model School",
      upi_id: "synthetic.school@upi",
      qr_svg:
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>',
    },
    office_phone: "+91 98480 00000",
  },
};

const optin: LinkEnvelope = {
  ...frame,
  kind: "parent_optin",
  optin: {
    categories: [
      { category: "notices", status: "opted_in", stopped_by_parent: false },
      { category: "attendance", status: "opted_out", stopped_by_parent: true },
      { category: "fees", status: "not_asked", stopped_by_parent: false },
    ],
    language_choice: "school",
    school_language: "en",
  },
};

describe("parent page frame", () => {
  it("shows the school, the expiry and the forwarding warning in English", () => {
    const { container } = render(<LinkFrame envelope={notice} />);
    expect(container.textContent).toContain(SCHOOL);
    expect(container.textContent).toContain("16/11/2026");
    expect(container.textContent).toContain("Do not forward it.");
    expect(container.textContent).toContain("sent through SchoolOS");
    expect(TELUGU.test(container.textContent ?? "")).toBe(false);
    expect(container.querySelector('link[rel="stylesheet"]')).toBeNull();
    expect(container.querySelector("main")?.getAttribute("lang")).toBe("en");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull(); // CSP style-src 'self'
  });

  it("is Telugu, with the self-hosted Telugu font, when the parent language is te", () => {
    const { container } = render(<LinkFrame envelope={{ ...receipt, language: "te" }} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("ఫీజు రసీదు");
    expect(container.querySelector("main")?.getAttribute("lang")).toBe("te");
    const font = document.querySelector('link[rel="stylesheet"]');
    expect(font?.getAttribute("href")).toBe("/p/fonts/telugu/noto-sans-telugu.css");
  });

  it("the expired and busy pages name no school", () => {
    const expired = render(<ExpiredLink />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "This link has expired or is not valid",
    );
    expect(expired.container.textContent).not.toContain("Synthetic");
    expired.unmount();
    render(<BusyLink />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("try again");
  });
});

describe("notice page (US-2401)", () => {
  it("shows the title, date, text with its paragraphs and the PDF link", () => {
    const { container } = render(<LinkFrame envelope={notice} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sports day");
    expect(container.textContent).toContain("Dated 17/10/2026");
    expect(container.querySelectorAll(".public-prose p")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Open the PDF" })).toHaveAttribute(
      "href",
      "/p/document",
    );
  });

  it("says when the PDF could not be opened, and has no link without a PDF", () => {
    render(
      <LinkFrame
        envelope={{ ...notice, notice: { ...notice.notice!, has_pdf: false } }}
        notice={{ kind: "pdf" }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("could not be opened");
    expect(screen.queryByRole("link", { name: "Open the PDF" })).toBeNull();
  });
});

describe("receipt page (US-2402)", () => {
  it("shows the receipt card with en-IN money and no truncation", () => {
    const { container } = render(<LinkFrame envelope={receipt} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Fee receipt");
    const text = container.textContent ?? "";
    expect(text).toContain("RCT/2026-27/000412");
    expect(text).toContain("Venkata Naga Sai Lakshmi Prasanna Kumari Bhimavarapu");
    expect(text).toContain("2019/0457/A");
    expect(text).toContain("₹1,23,45,678.50");
    expect(text).toContain("UPI");
    expect(text).toContain("The PDF is not ready yet");
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
  });

  it("puts a red cancelled banner above the card and offers no PDF", () => {
    render(
      <LinkFrame
        envelope={{ ...receipt, receipt: { ...receipt.receipt!, cancelled_on: "2026-10-18" } }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Cancelled on 18/10/2026");
    expect(screen.queryByText("The PDF is not ready yet", { exact: false })).toBeNull();
  });
});

describe("dues page (US-2403)", () => {
  it("shows first name, class, instalments, totals, the pay form with CSRF and the UPI QR", () => {
    const { container } = render(<LinkFrame envelope={dues} csrf="csrf-token-value" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Fees due for Venkata");
    const text = container.textContent ?? "";
    expect(text).toContain("Class IX");
    expect(text).toContain("Overdue");
    expect(text).toContain("includes late fee ₹50.00");
    expect(text).toContain("₹7,050.00");
    const form = container.querySelector("form#pay");
    expect(form?.getAttribute("method")).toBe("post");
    expect(form?.getAttribute("action")).toBe("/p/pay");
    expect(form?.querySelector('input[name="csrf"]')?.getAttribute("value")).toBe(
      "csrf-token-value",
    );
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByRole("button", { name: "Pay the ticked fees online" })).toBeInTheDocument();
    const qr = screen.getByRole("img", { name: "UPI QR code of Synthetic Model School" });
    expect(qr.getAttribute("src")).toMatch(/^data:image\/svg\+xml;base64,/);
    expect(text).toContain("synthetic.school@upi");
    expect(text).toContain("never asks for card or bank details");
    expect(screen.getByRole("link", { name: /School office/ })).toHaveAttribute(
      "href",
      "tel:+919848000000",
    );
  });

  it("one instalment: 'Pay ₹X online' without checkboxes", () => {
    const one = { ...dues.dues!, instalments: [dues.dues!.instalments[1]!], total_due: "3500.00" };
    const { container } = render(<LinkFrame envelope={{ ...dues, dues: one }} csrf="c" />);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(container.querySelector('input[type="hidden"][name="instalment"]')).toHaveAttribute(
      "value",
      "2",
    );
    expect(screen.getByRole("button", { name: "Pay ₹3,500.00 online" })).toBeInTheDocument();
  });

  it("without online payments: no form, the UPI section open; errors say what to do", () => {
    const { container } = render(
      <LinkFrame
        envelope={{ ...dues, dues: { ...dues.dues!, pay_online: false } }}
        notice={{ kind: "pay", error: "not_connected" }}
      />,
    );
    expect(container.querySelector("form")).toBeNull();
    expect(container.querySelector("details")?.hasAttribute("open")).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent("Pay at the school office or by UPI");
  });

  it("nothing due", () => {
    render(
      <LinkFrame
        envelope={{ ...dues, dues: { ...dues.dues!, instalments: [], total_due: "0.00" } }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Nothing is due now");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("opt-in page (ADR-0046 decision 2, ADR-0044 decision 4)", () => {
  it("shows the categories, how to stop and the language form", () => {
    const { container } = render(<LinkFrame envelope={optin} csrf="c" />);
    const text = container.textContent ?? "";
    expect(text).toContain("You get these");
    expect(text).toContain("Stopped");
    expect(text).toContain("Not joined");
    expect(text).toContain("reply STOP");
    expect(text).toContain("You stopped these by sending STOP.");
    const form = container.querySelector('form[action="/p/optin"]');
    expect(form?.getAttribute("method")).toBe("post");
    const school = screen.getByRole("radio", { name: "The school's choice (English)" });
    expect((school as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("radio", { name: /Telugu/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save my choice" })).toBeInTheDocument();
  });

  it("confirms a saved choice", () => {
    render(<LinkFrame envelope={optin} csrf="c" notice={{ kind: "saved", ok: true }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Saved.");
  });
});

describe("payment return page", () => {
  const base = { envelope: dues, refValue: "a".repeat(32), checks: 0, maxChecks: 12 };

  it("thanks the parent once Razorpay confirmed", () => {
    render(
      <PaidPage {...base} payment={{ status: "paid", amount: "3500.00", receipt_issued: true }} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Thank you. Your payment of ₹3,500.00");
  });

  it("checks again by itself while waiting, then offers 'Check again'", () => {
    const waiting = { status: "created" as const, amount: "3500.00", receipt_issued: false };
    const { unmount } = render(<PaidPage {...base} payment={waiting} />);
    const meta = document.querySelector('meta[http-equiv="refresh"]');
    expect(meta?.getAttribute("content")).toBe(`5;url=/p/paid?ref=${"a".repeat(32)}&n=1`);
    unmount();
    render(<PaidPage {...base} checks={12} payment={waiting} />);
    expect(screen.getByRole("link", { name: "Check again" })).toBeInTheDocument();
  });

  it("not paid and unknown payments say so", () => {
    const { unmount } = render(
      <PaidPage {...base} payment={{ status: "expired", amount: "1.00", receipt_issued: false }} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("not completed");
    unmount();
    const { container } = render(<PaidPage {...base} payment={null} />);
    expect(container.textContent).toContain("could not find this payment");
  });
});
