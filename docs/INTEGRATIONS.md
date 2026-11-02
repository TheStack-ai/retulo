# Agent and Main Brain integration

## Agent contract

The CLI and official MCP SDK stdio server call the same deterministic core. The public MCP instructions/skill tell an agent to distinguish questions from authorized file work, begin first, reserve exact destinations, commit observed artifacts, checkpoint meaningful changes and inspect on resume. SessionStart/Stop are not task creation/completion signals.

For staged or repeated work the same agent can define a workflow, plan affected
steps and execute the existing producer tools using Retulo's fixed input and
reserved output paths. The optional recipe path starts a new task with typed
inputs. Neither operation introduces another agent runtime. The packaged skill
describes the natural-language update/repeat flows; actual host adoption still
requires a connected client using that version of the skill and API.

Codex does not need to change the current chat's cwd: use the returned absolute output paths and explicit command working directories. Subchat creation and app navigation are optional host facilities, not engine dependencies. This engine never launches them.

MCP exposure and a configured skill do not prove all agents will follow the contract. Test the actual client's behavior. Do not claim control over arbitrary shell side effects or hosted tools without output-path support.

## Default Codex harness

The Codex installer adds a managed user `AGENTS.md` block and command hooks for `SessionStart`, `UserPromptSubmit` and `SubagentStart`. The user can request ordinary document or development work without naming the engine. The global procedure asks the current agent to distinguish actual file work from questions, select the correct task, reserve outputs and reconcile evidence before reporting completion.

`retulo --config CONFIG codex-hook` accepts the supported Codex event JSON on stdin. It reads only bounded registered task metadata and returns at most 4 KiB of hook JSON. It never reads the transcript or artifact bodies, calls a model, creates a task, recovers a transaction or changes a task's status. Invalid input/configuration fails open without creating state. The global instructions remain the fallback when a hook is unavailable.

`harness_context(session_id, cwd, turn_id, limit)` exposes the same read-only discovery through CLI/MCP. Exact Codex session bindings and registered workspace/source paths produce candidate cards. Multiple matches and incomplete scans stay explicit; no task is selected automatically. The current request determines whether to start new work or resume a known task. A session can contain several tasks. A new session can continue an existing task using its actual path, and the coordinator records the real new session binding.

Hooks for the same event can run concurrently. Workspace hooks do not consume Main Brain hook output or depend on its execution order. No `Stop` or `Interrupt` hook is installed, no continuation is generated, and existing OmO/Main Brain handlers keep their order and definitions. Pending reservations and recorded problems are shown as context on a later prompt, not converted into an automatic retry.

Subagent hooks use the parent's session ID. The `SubagentStart` context and global guidance therefore assign task state to the coordinator; workers use supplied task IDs and reserved paths and return evidence. The current Codex coordinator owns execution, directly or through available built-in subagents. OmO/OmO Native are optional executors; when present they retain their own delegation and security policy. Their removal does not remove task state, hooks, tools, output paths or the Main Brain bridge, and must not trigger automatic installation or repair. Main Brain retains provenance and memory ownership. The workspace ledger does not itself launch or replace an AI runtime.

Codex requires native trust review for each new non-managed hook definition. The installer reports this requirement and never manufactures trusted hashes or disables hook trust. Verify the actual host's hook inventory and execution after reviewing the exact installed definitions. Source: [official Codex hooks documentation](https://learn.chatgpt.com/docs/hooks).

## Main Brain

Main Brain is an optional receiver integration and is not required for installation or core operation. Configure its local MCP command, arguments and a deliberately mapped scope in the private engine configuration. Never embed private endpoints/accounts in source examples.

The bridge exposes `brain_prepare`, `brain_capture`, `brain_sync`, `brain_receipt` and `review_context` in addition to core tools. It communicates only through MCP and never writes the Brain database directly.

`get_context`, `resume_task`, `brain_prepare` and `review_context` default to compact projections. Explicit full detail and `read_records` preserve drilldown access. `review_context(event_id=..., detail="full")` reads one exact saved event. Capture-time workspace revision/artifact metadata and current source validity are separate fields; see [sustainability contracts](SUSTAINABILITY.md).

1. Read current workspace state, then retrieve prior decisions with `brain_prepare`/Main Brain search.
2. Verify needed sources using `read_evidence`.
3. Run `retulo verify` through an actual authorized shell tool from the mapped execution scope.
4. Use the actual hook/admin-registered source ID and exact tool quote in `brain_capture`. The engine does not invent source registration.
5. The bridge saves an immutable local outbox request in a task-specific namespace and uses the receiver's exact idempotent receipt before retrying an ambiguous response. An incomplete downstream receipt triggers repair of only the unfinished effects, preserving the memory revision.
6. On revisit, `review_context` combines current filesystem facts, prior saved events and freshly read evidence. The connected AI compares decisions/results and proposes choices. No background model or scheduled review is started.

The current native Brain hook records completed shell executions, not every MCP tool result. A hook source must have the correct scope/role and a complete bounded quote. An unknown/stale/cross-scope source is not silently reclassified, registered or saved under another scope.

Receiver additions are backward compatible: optional capture `idempotency={producer_id,event_id,payload_digest}` and readonly `capture_receipt`. The digest contract is UTF-8 SHA-256 of normalized semantic capture JSON; see `brain.payload_digest`. The receipt is a historical acknowledgement, not proof that evidence is still current.

If Brain is unavailable or rejects evidence, outputs remain intact and sync remains pending/unknown. Retrying uses the original payload and event ID. A fresh verification is a new event, never a modified replay. Synced memory, downstream index/checkpoint state and current evidence validity must be reported separately.

## Another provider

First run the synthetic contract tests; then validate the actual provider's file access, MCP/CLI calls, output paths, failure reporting and cross-session continuation. Source provenance into Brain needs its own trusted adapter. No other vendor's actual compatibility is currently certified.
