# Who uses SchoolOS, and what that means for a screen

One shell serves every role. Navigation, the "Today" home, palette entries and bottom bars are all filtered by the signed-in person's permissions (the same `sections` list the sidebar uses), so a person never sees a link to a page they can't open.

| Role | Device and setting | Home ("Today") | Navigation |
|---|---|---|---|
| Office clerk / office admin | Shared office PC, 1366×768, keyboard, patchy internet | Work waiting: findings, change requests, import errors, certificates; readiness strip | Full sidebar; palette ranked toward records |
| Accountant / fee counter | Counter PC, receipt printer, queue of parents | Today's collection by mode, cash to hand over, approvals, day close | Fees group first; the counter is keyboard-only |
| Class / subject teacher | Android phone, between periods | My classes today: attendance per section, marks due, circular tasks; own sections only | Phone bottom bar: Home, Attendance, Marks, Ask |
| Principal / owner | Phone and PC | Status strip (readiness %, blockers, fees collected vs due, approvals) and an approvals inbox | Phone bottom bar: Home, Approvals, Ask, Messages |
| Exam coordinator | Office PC | Marks completeness, board exports | Sidebar |
| Auditor (read only) | PC | Read-only views; no primary write actions | Sidebar |
| Parent | Cheap Android phone via a WhatsApp link, 2G/3G | No shell: one server-rendered page per link, almost no JS | None |
| Operator (control plane) | Laptop | Fleet health, failing jobs, invoices due, support queue | Dark neutral chrome with amber marker, its own nav |

## Rules that follow

- **Low digital literacy:** icons always have a text label; plain verbs ("Collect fees", not "Process"); no hover-only or gesture-only actions; a help link on complex screens; consequence lines before irreversible steps.
- **Shared PCs:** never store personal data in localStorage, recents or the palette; per-viewer conveniences only (density, column choice). "Lock now" stays reachable.
- **Patchy internet:** keep form input when a request fails; idempotent submits (receipts, attendance); page and filters in the URL so a reload restores the view (except search text that could hold personal data, which stays in memory: SEC-008); debounce and abort searches.
- **Teachers see only their sections** (invariant 3). A teacher screen never offers a school-wide filter.
- **Purpose limit** (invariant 14): student insight screens carry the purpose note; no marketing use.
- **English first** (ADR-0036): every screen in English; Telugu catalog kept in step but hidden behind `SOS_TELUGU_ENABLED`.
