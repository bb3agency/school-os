# ADR-0036: English first; Telugu hidden behind one switch

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-30 |
| Deciders | Product owner ("forget about Telugu in this whole thing for now; let's deal with English first; hide all Telugu and its related things from every nook and corner of this whole project", 2026-09-30) |
| Amends / supersedes | Amends the bilingual (English + Telugu) requirement in CLAUDE.md §1, §3, §7, §8 and §10, docs/01, docs/02, docs/03 (FR-KB-006 and every "EN/TE" output), docs/06 and docs/17 for the time being. Does not supersede any ADR. |

## Context

SchoolOS was specified bilingual: parent-facing output in English and Telugu, every UI string in `en` and `te`, Telugu AI answers for Telugu questions, bilingual certificates, notices and notifications, bundled Noto Sans Telugu. The product owner decided to launch in English first and to hide Telugu everywhere until it is picked up again.

## Decision

1. **One switch.** `SOS_TELUGU_ENABLED` (default `false`), read by the API and worker only through `app.core.languages` (`telugu_enabled()`, `enabled_languages()`, `output_language()`), and by the web app server-side. Nothing else decides whether Telugu is shown.
2. **Hidden, not deleted.** Telugu message catalogs, templates, prompts, fonts, eval sets and code paths stay in the repository, dormant, so bringing Telugu back is a configuration change plus a review, not a rewrite. With the switch off nobody sees Telugu: no language switcher, `/te` URLs redirect to `/en`, no Telugu labels, form fields, columns, PDF sections, notice or notification text, e-mails or AI answers (a question in Telugu is answered in English), and no Telugu font is loaded.
3. **Stored data is untouched.** Existing `*_te` columns and API fields stay (backward-compatible schema and API, invariant 12); with the switch off the API no longer fills or shows Telugu-only output and the web app never renders it. Student data captured in Telugu script from source documents (e.g. a register) is data, not UI: data-quality matching still reads it, but screens show the English fields.
4. **Tests.** Telugu behaviour keeps its tests, run with the switch on explicitly, so it cannot rot; new tests pin the default: with the switch off no Telugu text reaches a screen, document, message or answer. No test is deleted or weakened.
5. **Definition of done, for now.** UI strings must exist in `en`; `te` catalog entries are optional while the switch is off (keep existing ones).

## Consequences

- Good: a simpler English launch; reversible by one setting.
- Bad: parents who read only Telugu get English notices until Telugu returns; the dormant Telugu paths still cost test time; docs that describe bilingual behaviour now carry "hidden while `SOS_TELUGU_ENABLED` is off" notes.
- To bring Telugu back: set `SOS_TELUGU_ENABLED=true` in one environment, re-run the Telugu evals and a Telugu review of catalogs, templates and prompts, then record the decision in a new ADR.
- Note (2026-09-30, knowledge module and evals; docs/06 §5.1, §11, §13.8):
  - *Answer language.* A Telugu or code-mixed question is accepted and searched as written; the answer's `language` (SSE `meta.language`, `kb.queries.language`) is `en` while the switch is off; the detected style of the question is audit data only (`kb.query.asked` `question_language`). The answer cache reuses only answers in the current language.
  - *Prompts.* English-first versions are the default (`answer_system` v3, `followups` v2, `circular_reading` v2, `parent_notice` v2, each with the sentence "Write in English only"); the bilingual versions stay as the switch-on prompts. Server-side checks hold whatever a model writes: Telugu answer prose falls back to search-only (the cited passages), the streamed preview stops before Telugu script, Telugu follow-ups, memory suggestions, summaries, metadata and notice fields are dropped, a Telugu deadline title gets a configured English title.
  - *The `translation` role is kept on.* It serves retrieval only (its output is a search query, never shown) and lets an English question find a Telugu-script circular, so it does not depend on the switch. It is not wired yet; when it is, its text must never be displayed.
  - *Kept as data, not hidden:* cited passages and deadline quotes (the school's own words, the evidence), Telugu questions and their standalone rewrite (search only), contextual chunk headers (index only), a memory note in the user's own words. Titles derived from a Telugu first question become "New conversation".
  - *Evals.* Every Telugu dataset and gate still runs, with the switch on; a new pass with it off adds two hard gates (`english_first_telugu_outputs == 0`, `english_first_english_answer_rate >= 1.0`).
