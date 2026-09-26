# ADR-0008: Read-only typed tools instead of text-to-SQL

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

Many staff questions are about structured records ("DOB of admission no. 1234", "how many students in Class 9"). A common approach is to let the model write SQL. Generated SQL is hard to secure (scope and sensitivity rules live in services, not only in RLS), hard to validate, and can leak data outside the user's sections or cause expensive queries. Prompt injection inside documents could steer generated SQL.

## Decision

- The model answers structured questions only by calling **whitelisted, typed, read-only tools** (`find_students`, `get_student_facts`, `get_value_history`, `count_students`, `list_findings`, `list_documents`, `search_documents`; 06 §7).
- Each tool calls a module **service** with the caller's `UserContext`, so RLS, scopes, sensitivity (C3) and permission rules apply exactly as in the UI.
- Tool inputs are schema-validated; field lists are enums; result sizes are capped; small-cell suppression applies to sensitive counts.
- Tool results are returned as `search_result` blocks with `sos://` source URIs so they can be cited and validated.
- No tool writes data or reaches the network (CLAUDE.md §6 invariant 9). Writes suggested by the model are completed by a human through normal endpoints.
- Maximum 3 tool rounds per answer.

## Consequences

- Good: the AI cannot see more than the user; security rules are enforced in one place.
- Good: tools are testable and evaluable (leakage = 0 hard gate).
- Bad: questions outside the tool set get "not found" until a new tool is added; new tools are added deliberately with tests and evals.
- Bad: more upfront engineering than text-to-SQL.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Text-to-SQL with a read-only role | Scope/sensitivity rules bypassed; injection risk; unpredictable cost |
| Text-to-SQL over curated views | Still hard to prove scope safety and to cite results |
| No structured answers (documents only) | Misses the most common office questions |

## Related requirements

FR-KB-003, FR-KB-004, FR-KB-005, FR-KB-010, SEC-018, SEC-020; 06 §7–9; 07 §12 (LLM01, LLM06).
