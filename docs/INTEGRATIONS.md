# Agent integration

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

Hooks for the same event can run concurrently. Workspace hooks do not depend on another hook's output or execution order. No `Stop` or `Interrupt` hook is installed, no continuation is generated, and existing handlers keep their order and definitions. Pending reservations and recorded problems are shown as context on a later prompt, not converted into an automatic retry.

Subagent hooks use the parent's session ID. The `SubagentStart` context and global guidance therefore assign task state to the coordinator; workers use supplied task IDs and reserved paths and return evidence. The current Codex coordinator owns execution, directly or through available built-in subagents. The workspace ledger does not itself launch or replace an AI runtime.

Codex requires native trust review for each new non-managed hook definition. The installer reports this requirement and never manufactures trusted hashes or disables hook trust. Verify the actual host's hook inventory and execution after reviewing the exact installed definitions. Source: [official Codex hooks documentation](https://learn.chatgpt.com/docs/hooks).

## Another provider

First run the synthetic contract tests; then validate the actual provider's file access, MCP/CLI calls, output paths, failure reporting and cross-session continuation. No other vendor's actual compatibility is currently certified.
