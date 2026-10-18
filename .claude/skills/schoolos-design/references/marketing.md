# Marketing site (welcome, features, pricing, about, security)

- **Register paper**: paper canvas, ledger-green accent, Instrument Serif headlines with at most one italic accent phrase per page, ruled hairlines instead of glows or grids. Product mock-ups inside `.mk-app` keep the real console look so screenshots stay honest.
- **Story**: an operational product for a school office. Lead with the job (enter once, catch mismatches before the portal does, answer from the school's own records with sources), then how it works in three steps, then proof, then pricing.
- **Proof is real or absent**: no invented schools, logos, testimonials, ratings, counts or charts. Mock-ups use clearly synthetic sample data labelled "Sample data". Security claims match docs/07 and docs/08 exactly.
- **Pricing**: follow ADR-0038 and the existing explainer; no monthly/annual toggle; plain explanation of what is included and what costs extra (e.g. WhatsApp messages are paid to Meta by the school, online payments go to the school's own Razorpay account).
- **No slop template**: no purple gradients, glassmorphism, floating blobs, bento grids for their own sake, icon-heading-description tiles with interchangeable copy, or CTA blocks that repeat the hero. Run the audit-ai-design-slop removal test on every section.
- **Motion**: Lenis smooth scroll on the homepage only, off under reduced motion and on touch; keyboard and anchor scrolling stay native; no scroll-jacked reveals that hide content until scrolled.
- **Performance**: server-rendered, illustrations as cached `<img>`, no third-party scripts or font CDNs, Core Web Vitals in the good range on a mid-range phone.
- Every link and CTA keeps its target; sign-in stays one click from every page.
