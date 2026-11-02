# Reuse the same qualification when changing a client or model

Run the engine's Python suite first. Then use synthetic material and a separate test workspace with the actual client. Keep the requested model and reasoning settings unchanged unless the operator explicitly selects a comparison. Do not use real business sources or auto-publish the results.

| Case | Fixed input or action | Acceptance evidence |
|---|---|---|
| Question | Explain why task IDs differ from chat IDs | No `begin_task`, no new task directory |
| Document | A synthetic note containing `Sample Safety Review`, two sample sites, document review only, and no external submission or expenditure | Actual MCP begin/reserve/commit/verify; exact required content; originals preserved |
| Unknown | Record the content criterion as unknown and leave independent-review attention open | Completion is rejected; unknown and attention persist |
| Independent review | A separate real client session reads criteria and artifact bytes, without the writer's self-assessment | Distinct real execution/reviewer references; an immutable review artifact; unknown-to-pass history; no fabricated approval |
| Resume | Continue the same task in another session and produce v2 | Same UUID/directory; v1 hash unchanged; current guide revision matches; ordinary reads remain bounded |
| Past evidence | Store a real hook-registered result, then explicitly reopen and change the synthetic artifact | Source evidence can remain current while workspace applicability becomes `requires_revalidation`; same event replay keeps memory identity |
| Upgrade | Advance an unmodified managed skill, then separately test a user-edited synthetic copy | Managed copy updates; user edit remains; rollback/retry and concurrent installers preserve consistent receipts |

Record client/version, actual model configuration provenance, command/tool events, source hashes, result statuses and response byte sizes. Missing evidence is unknown. A passing simulated adapter is not actual vendor compatibility. Compare the same cases before widening use; do not infer new permissions from a score.

The engine does not launch this process automatically. The connected agent performs it only within an authorized task.
