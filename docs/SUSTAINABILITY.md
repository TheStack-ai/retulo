# Sustainable task structure

This upgrade keeps the task's evidence, decisions and failure history usable when the conversation, model or tool changes. It adds no model runner, background watcher, external channel or second state store.

## What is retained and what is shown

`task.json` remains the authoritative ledger. Operation receipts, artifact versions, approval snapshots and review history remain on disk. A compact response is a read-only projection, not a destructive compaction or a new truth source.

`get_context` and ordinary `resume_task` default to compact output, bounded to 24 KiB of UTF-8 JSON. Current goal, status, next action, rubric status counts, attention, decisions, latest artifacts and approval status are prioritized. Omitted and clipped material is labeled and counted. A missing detail never means the criterion passed or that permission was granted.

Use `get_context(detail="full")` for an explicit full read, or `read_records(section=..., offset=..., limit=..., expected_revision=...)` for a page. Paging binds to the observed revision. Large records become explicit previews with a full-record recovery path; `next_offset` still advances. Reading never changes the ledger or operation replay result.

## Rubrics, reviews and attention

A completion criterion's `text` states the observable end condition. Optional `basis` records where the criterion came from. Omit `human_review` or use literal `false` for ordinary work without human review. A nonempty string states actual required human judgement and implies `requires_approval=true`; contradicting it with an explicitly false approval requirement is rejected. Literal `false` never overrides an explicit approval requirement. Approval still binds an exact artifact version and hash.

Results may be `passed`, `failed`, `pending` or `unknown`. Empty or unknown results cannot complete a task. A passed result references a registered, unchanged artifact as `artifact:<artifact_id>:<artifact_revision>`; a prefixed reference without its revision is invalid. Optional `requires_independent_review` requires `review.mode="independent"` with distinct, supplied `reviewer_ref` and `execution_ref` values. These are caller-supplied references; the engine does not authenticate the reviewer or infer semantic quality from a string. A client must supply actual host evidence and directly inspect the result.

Changed criterion results append to `criteria_history`. Failed and unknown results remain visible after a later pass. Existing v1 results are preserved when history is introduced. Replay does not duplicate history. A failed output is not recorded as a successful result. Repeated failures can motivate a proposed correction; only actual validation supports promoting that correction.

`attention` is separate from task status. It names the kind, owner, reason and requested action. Omitting it leaves it unchanged; `null` explicitly clears it. An unresolved attention record blocks completion. Recording or reading it sends no notification and does not resume a task. Missing structured information stays unknown.

## Safe integration upgrades

The installer distinguishes the installed managed base, the current file and the candidate skill by hash. An unchanged managed base can advance to the candidate. A user edit remains intact and produces reviewable conflict details. Previous installed defaults and receipts remain available. Unknown or damaged provenance does not become permission to overwrite a differing file.

Installation stays explicit. Version 0.3 adds a managed global guidance block and three read-only context hooks while preserving existing hook definitions and order. It does not change models, unrelated MCP servers or source-root permissions. A newly connected session must load the updated skill and tool schema, and Codex must trust the exact new hook definitions; writing installation files does not prove an existing session adopted them. Managed hook and guidance upgrades preserve user edits with the same baseline/conflict and rollback principles.

## Bounded recurring cost

The harness launches no model, classifier, watcher, retry loop or polling service. Ordinary questions create no task. The coordinator loads the skill once per task/context, reuses valid checks and updates records only after meaningful changes. Simple work stays direct; subagents are for a concrete independent workload or required review, not routine bookkeeping.

Hook JSON is capped at 2 KiB for SessionStart, 1.5 KiB for UserPromptSubmit and 2 KiB for SubagentStart. Candidate omission and scan limits remain explicit. IDs are never clipped; an input that cannot fit safely returns no hook context, leaving the global instructions in place. Exported MCP tool definitions have a 24 KiB regression budget, and shared MCP guidance a 1,200-byte budget. These are serialized UTF-8 byte limits, not claimed model-token savings. The host's actual token usage and caching remain separate measurements.

## Design references and boundaries

- [Threads series by jay.ai.daily](https://www.threads.com/@jay.ai.daily/post/DfLhMWmih7M): judgement before memory, short current context and long records, durable continuation, learning from verified corrections and testing before upgrades. Product claims and attributed experimental statistics were not treated as verified engineering results.
- [DSH philosophy](https://github.com/TheStack-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/docs/000-philosophy.md): minimize the attention a system consumes.
- [DSH cards](https://github.com/TheStack-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/src/app/cards.ts): compact current state and explicit detail reads.
- [DSH prompt versions](https://github.com/TheStack-ai/dsh-superagent-prototype/blob/6337fc823ce20251c8de8085948165c81aaa701e/src/app/prompts.ts): retain a user's override and detect a changed default.

These patterns were adapted to the existing deterministic Python engine. DSH's service, accounts, automatic resume, role-specific models, polling, UI and external messaging were not installed. Its prototype also documents unbounded historical retention; it is not evidence that storage growth is solved. This engine retains its explicit state-size/operation limits and does not silently discard receipts to pass them.
