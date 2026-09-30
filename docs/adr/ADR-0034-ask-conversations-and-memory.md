# ADR-0034: Ask conversations, context and per-user memory

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-30 |
| Deciders | Product owner (requested 2026-09-30: "it should have context and memory also; build this end to end"); lead |
| Amends / supersedes | Amends FR-KB-012 (docs/03) and [ADR-0008](ADR-0008-tools-not-text-to-sql.md) (adds `search_my_conversations` to the tool whitelist); builds on [ADR-0005](ADR-0005-llm-gateway-and-provider.md) (every model call through the gateway) |

## Context

"Ask the school" answered one question at a time; follow-ups got the same user's last three
questions of the browser session for 30 minutes, never answers (docs/06 §5 conversation rules,
M2 wave 5). The product owner asked on 2026-09-30 for the behaviour staff know from general AI
chat products: a conversation history they can rename, pin and delete, regenerate and
edit-and-resend, progress while the answer is prepared, follow-up suggestions, context that
survives a long conversation, and a memory of the user's own preferences across conversations.

FR-KB-012 read "Conversation context MUST be session-scoped; no cross-user memory." Memory across
conversations changes that requirement, so it needs a decision. What makes it risky here:

1. **Invariant 8.** Earlier answers quote records and documents. If a person loses access (a
   class teacher moves section, a document's ACL changes), history, summaries and memory must not
   carry what they may no longer see back into a prompt or onto the screen.
2. **Children's and staff data (docs/08).** A memory that stored "Ravi's father is a farmer"
   would be a second, uncontrolled copy of student data outside the records and the
   maker-checker flow (invariant 6), and personal data about staff that nobody reviews.
3. **Cost.** Every extra model call is metered against the school's monthly budget (FR-KB-011).

## Decision

**Conversations (FR-KB-012 as amended).** A conversation is one user's thread in one school
(`kb.conversations`; its id is the `session_id` its questions carry). Only its owner can list,
open, rename, pin, delete, continue, regenerate or edit it (404 for anyone else, invariant 3).
Titles, rolling summaries, numbered citations and follow-ups are ciphertext under the school's
data key, covered by DEK rotation and the SEC-012 census.

**Context.** A question MUST carry only its own conversation's context: the last N current
(not superseded) turns within a token budget, verbatim, plus a rolling summary of older turns.
An earlier answer, and the summary, go to the model ONLY when every source they cited is still
visible to the caller NOW (re-checked through the owning modules' services); otherwise the
answer is left out (the question stays) and the summary is forgotten and rebuilt from what is
visible. History and summary are context, never evidence: retrieval still runs afresh, filtered
by the caller's current permissions in SQL before ranking, and citations are validated against
this request's tool results only. The same re-check applies when history is shown (a withheld
source loses its title and snippet, and that answer and its follow-ups are withheld).

**Memory (new).** Cross-conversation memory is per user AND per school (`kb.user_memories`),
visible, editable and deletable by that user only, never cross-user. It MUST hold only the
user's own preferences and work context ("prefers answers in Telugu", "keep answers short",
"I am the class teacher of IX-A", "I handle UDISE+ submissions"). It MUST NEVER hold facts
about students, guardians or other staff (no C2/C3 record data), Aadhaar-like numbers, or
anything the model read in records. Enforcement, in this order, for every item before it is
stored (typed, edited or suggested):

1. local rules: no Aadhaar-like, phone or email (`core.redaction`), no date, no run of five or
   more digits, at most 200 characters;
2. no name or value from the records (student, finding, change, fee sources) the user was shown
   in that conversation;
3. the `memory_screen` role through the gateway must answer `self`; `others` or `unsure`
   refuse, and when the model cannot be asked the item is NOT stored ("when in doubt, don't").

Items are created explicitly (settings, or "remember that ..." in Ask, which saves at once and
is answered with a fixed reply, never by the answer model) or suggested by Ask (at most one per
answer, from the follow-up call). **A suggestion is stored only as `pending` and is used only
after the user confirms it; an unconfirmed suggestion is deleted after 24 hours.** Confirmed
items reach the model as one stable system block right after the static system prompt, as
"user preferences/context": never evidence, never cited, never able to widen permissions or
scope. The user can turn memory off (nothing is then stored, suggested or used; items stay
listed so they can be deleted); the school can turn it off for everyone with the tenant setting
`ai_memory_enabled` (default on). Deleting an item deletes its row; "forget everything" deletes
them all. Items end with the user's membership of the school (daily job, and the membership FK)
and with offboarding. Create, edit, confirm, delete, forget-all and the switch are audited with
ids and counts only.

**Chat search.** `search_my_conversations` joins the ADR-0008 whitelist: read-only, the caller's
own non-deleted conversations in the current school, the newest 30, decrypted in memory and
scored by words (no plaintext index); its results are citable as
`sos://conversation/{id}#q{query_id}` and validated like any citation; an earlier answer in a
result is included only when its sources are still visible.

**Cost controls.** Follow-ups, rewrite, summary and the memory screen are cheap, metered,
budget-checked gateway roles; the summary is made by a worker job after the answer, never on a
question's path; exact repeats that used documents only are reused under the rules of docs/06
"Cost and performance design" (same access fingerprint, sources still current and visible).

## Consequences

- Good: staff get a chat that remembers the thread and their own preferences, without a second
  copy of student data and without widening anyone's access.
- Good: deleting, turning off and forgetting are immediate and complete for memory.
- Bad / costs: up to three extra cheap calls per question (rewrite for a follow-up, follow-ups,
  memory screen when an item is saved or suggested) and a summary job for long conversations;
  every history view re-checks each cited source (bounded per request by a cache).
- Bad: a memory screen that cannot run refuses to store; users may be told to try again later.
- Follow-up work: tune the screen and the suggestion prompt with real (consented) feedback; a
  per-school report of memory use for the DPO (counts only); PO questions in docs/14.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep context session-only (FR-KB-012 as it was) | The product owner asked for memory and persistent conversations. |
| Store suggested memories at once, let users delete | Unsafe default: a wrong suggestion would be used before anyone saw it. |
| Let the answer model write memories through a tool | Violates invariant 9 (AI tools are read-only); a person confirms instead. |
| A consolidated memory summary per user (as some products do) | Harder to see, edit and delete item by item; out of scope (lead, 2026-09-30). |
| Send earlier answers without re-checking sources | Breaks invariant 8 after any permission change. |

## Related requirements

FR-KB-005, FR-KB-008, FR-KB-009, FR-KB-011, FR-KB-012 (amended), SEC-012, SEC-018, SEC-019,
invariants 3, 5, 7, 8, 9, 13; docs/03, docs/05 §6.4, docs/06 §5, §7, §13.5, docs/08 §7, §8, §11,
docs/09 Knowledge.
