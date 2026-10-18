# Review loop

## 1. Worst-case data (method from the break-ui skill; values for SchoolOS)
Enter the values through the same boundary as demo data (fixtures, stand-in API, synthetic seed), never by editing markup. Spread them across rows.

| Field | Worst realistic values |
|---|---|
| Student / guardian names | `Venkata Naga Sai Lakshmi Prasanna Kumari Bhimavarapu`, `Mohammed Abdul Rahman Siddiqui`, `K` (initial only), `S. Ramesh`, `ADDEPALLI PADMA KAVYA` (all caps from registers), surname-first order, a name with a dot and no space (`B.Sai`) |
| School names | `Sri Venkateswara Vidya Niketan English Medium High School, Kakinada`; a one-word name |
| Class / section | `Nursery`, `LKG`, `UKG`, `10`, `XII`, `9-A (Telugu medium)`, a section named `Rose` |
| IDs | admission no `2019/0457/A`, UDISE+ PEN 11 digits, APAAR ID 12 digits, receipt `RCPT/2026-27/000412`, UTR 22 characters, Aadhaar last 4 only |
| Money | ₹0, ₹1, ₹12,500, ₹1,23,45,678.50 (en-IN grouping), negative adjustments, a concession that makes dues zero |
| Counts | 0, 1 (plural), 54 sections, 1,284 students, 10,000+ ledger rows (pagination) |
| Dates | 31/03 (financial year end), 29/02, a date in the future, academic year `2026-27` |
| Text from data | a 2,000-character circular, a file name `TC_Request_Letter_Final_Signed_v3 (1).pdf`, `<b>` or `&amp;` in a note (must render literally) |
| States | every status at once (Studying, Left, TC issued, Suspended), missing optional fields, no permission, the current user in the list, offline, slow (3G) |

Check at the real container width, 320px, 1366×768 and 1440×900, at 200% zoom. Report Broken / Ugly / Fragile with file:line and the fix.

## 2. Slop audit (audit-ai-design-slop skill)
Classify findings as quality defects or slop patterns; P0 blocks completion, accessibility or shows fake proof; P1 harms comprehension or trust; P2 repeated slop or inconsistency; P3 polish. Removal first: what job does this element do, and would removing it lose anything?

## 3. Browser acceptance
- Viewports: 1366×768 (primary), 1440×900, 768×1024, 390×844, 360×640, 320px reflow, 200% zoom.
- Keyboard only end to end; screen reader names on every control; reduced motion on; forced colors on.
- Real data shapes from the synthetic seed (sign in through /dev/sign-in with a synthetic subject for each role that uses the screen).
- Network throttled to slow 3G for parent and verification pages.
- Print preview for anything printable.

## 4. Scoring rubric (0-10 each; ship at ≥ 8 overall with no dimension under 6)
Task clarity (one obvious next step) · hierarchy · consistency with the primitives · copy · states (empty, error, loading, offline, no permission) · accessibility · phone fit · performance · honesty (no fake proof, correct numbers). Have a second agent that did not build the screen score it from screenshots and the diff ("blind judge"); fix the lowest dimension first.

## 5. QA checklist before merge
- [ ] Protected contracts unchanged (API calls, query keys, BFF routes, permissions, idempotency, If-Match, step-up, maker-checker)
- [ ] One primary action; consequence lines on irreversible actions
- [ ] Figures font and en-IN grouping on every number; nothing numeric truncated
- [ ] All states present; errors say how to fix
- [ ] en strings added, te kept in step, parity test passes
- [ ] No server-rendered style attributes or injected style tags
- [ ] 24px targets (44px for primary touch); focus visible and not obscured
- [ ] Reduced motion respected; nothing animates except transform/opacity
- [ ] `npm run lint`, `typecheck`, `test`, `build`, `format:check` pass; e2e responsive suite if layout changed
- [ ] docs/17 updated when a primitive or rule changed
