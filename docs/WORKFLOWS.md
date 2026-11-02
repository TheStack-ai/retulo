# Incremental workflows and recipes

Retulo records the relationship between inputs, work steps and exact artifact
versions. The current host agent interprets the user's request and executes its
existing tools. The core only validates contracts, observes registered files,
plans changes and manages recoverable state.

## Two related operations

- **Update an existing result:** keep the task ID, bind the changed input, and
  execute the steps identified by `plan_rebuild`.
- **Repeat a process for new work:** pin a recipe revision and bind new inputs
  with `begin_from_recipe`. This creates a new task with fresh validation state.

The agent can use these operations for ordinary requests such as “update the
report with this data” or “use last month's process.” A small one-off edit can
continue using the original v1 file-work path.

## Definition, binding and execution

A workflow definition declares file-input slots, typed parameters, relevant
environment keys, steps and final targets. A step names its input ports and
output slots, provides an action description, and declares its required checks.
An input port refers to either a file-input slot or a specific upstream step
output. The definition contains no runtime source paths or success verdicts.

Definitions are bounded, strict JSON. Invalid references, dependency cycles,
duplicate output ownership and unsafe output names are rejected. Parameters are
typed strings, integers, finite numbers or booleans; explicit defaults and enums
are supported. Parameter binding never performs text replacement or evaluates
template expressions. The packaged schema documents the structural contract;
the normalizer additionally checks references and dependency semantics.

`define_workflow` activates schema v2 for the task. Existing task IDs, artifact
versions, receipts, approvals and history remain. Old data does not become
invented execution history. An old v1 engine rejects v2 task state; it must not
silently ignore the new completion rules. New engines keep the v1 path available.

`bind_input` connects an input slot to a registered source observation:

- `follow` checks the current original against that observation. A changed file
  needs explicit registration and rebinding; it is never silently adopted.
- `pinned` uses a preserved source copy. Later changes to the original do not
  change the selected bytes.

Historical source observations remain visible. Current task validation uses the
active bindings, so a legitimate input update does not leave the old reference
as a permanent completion error.

## Plan and run

`plan_rebuild` is read-only. It reports the target closure in dependency order,
each step's reasons and one of `reusable`, `needs_run`, `blocked`, or `unknown`.
Changed upstream work prevents a downstream step from consuming an old result
as if it belonged to the new input. Replan after upstream completion.

Reuse requires matching step definition, selected parameter and environment
values, resolved input fingerprints, intact outputs and required check evidence.
Dependency coverage is explicit: `declared_complete` is a caller's contract claim,
not proof that Retulo intercepted every file read. Partial or unknown coverage
does not earn automatic reuse. The default reuse policy is manual; workflows
explicitly opt individual steps into `if_unchanged`, or use `never`.

The initial planner conservatively includes exact references in fingerprints.
It does not promise content-only deduplication, minimal execution across every
possible graph, or identical results from nondeterministic tools.

The host sequence is:

1. Call `begin_step_run` with the current revision and plan digest. It records the
   exact definition and inputs and returns fixed input snapshot paths.
2. Call `reserve_output` for each declared slot with the run ID and slot name.
3. Execute the existing producer against those paths outside the core.
4. Commit the observed bytes through `commit_artifact`.
5. Run the required checks and call `finish_step_run` with actual evidence refs.

`finish_step_run` checks current input/definition conditions and all required
outputs before changing the step's active output set. A failure or interruption
does not erase partial bytes. A run still marked started after interruption is
not proof that a process is alive; the host must reconcile it. The core does not
restart a stopped task or retry an external action.

Historical run outcome and current freshness are separate. An input change does
not rewrite “succeeded then” into “failed now.” A reusable result means the saved
bytes still fit the declared contract; it does not establish a new human approval.

## Final results and validation

File `latest` remains the most recently registered version. `active_outputs`
identifies complete step output sets. `current_deliverable_set` identifies the
coherent final target set promoted by successful task completion.

Completion checks the exact upstream versions consumed by selected runs, current
inputs, the final artifact set and the task's required criteria. This avoids
assembling a result from unrelated “latest” files. The previous completed set is
preserved during regeneration; if its inputs have changed it is historical,
not current. External delivery and user approval remain separate records.

Filesystem publication and ledger updates reuse the existing journal and
idempotency receipts. They are recoverable operations, not a filesystem-wide
transaction or a guarantee of exactly-once external API execution.

## Recipe manifests

A recipe is an immutable JSON artifact with an ID/revision, normalized workflow,
typed input contract, completion-criterion definitions and optional origin and
qualification references. It contains no copied approvals, passed criterion
results, completed state or executable authority.

The current agent selects the reusable successful path and writes the manifest
through the existing reservation/commit API. A completed source task stays
read-only: author the recipe in current authorized work instead of reopening its
origin merely to save a recipe.

`check_recipe` distinguishes structural validity from evidence about referenced
examples. A manifest's claim is not independently authenticated by its JSON
shape. Missing historical evidence remains unverified, not implicitly passed.

`begin_from_recipe` pins the managed manifest's exact artifact revision/hash and
binds allowed source files and typed parameter values. The creation receipt
includes the initial recipe seed. Retrying the same request returns the same
task; using that ID with different declared bindings is a conflict. A newly
created recipe revision does not alter an already-started task.

The host checks actual capability requirements before work. Recipe requirements
do not install software, expand roots or authorize messages, publication or
purchases. Additional user approval is only necessary when the actual task or
existing completion criteria require it.

## Bounded context and storage

Compact context shows workflow identity, counts, pinned recipe identity and
small current summaries. Full definitions, run histories and qualification
records are available through revision-bound `read_records` calls. They are not
injected into every prompt or lifecycle hook.

Short state stays in `task.json`; detailed outputs/logs use managed artifacts.
Existing state/journal size and receipt limits remain. The engine reports a
limit instead of dropping evidence or claiming that unbounded retention is
solved. Main Brain remains optional.

## Runnable example

From an installed source checkout:

```sh
.venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo
```

Use an empty destination. Optionally supply `--baseline-zip SOURCE_SNAPSHOT.zip`
to compare an earlier source snapshot with the current repository. Without it,
the first input uses a subset of the actual inventory and the update uses the
full selected inventory.

The example performs real CLI calls in separate processes and invokes
`report_fixture.py` as an explicit host-side producer. It creates an HTML/SVG
report, updates the inventory-dependent steps while reusing the profile,
preserves the earlier final report, authors a recipe and creates a second report
from the actual test/document inventory. Its evidence records input hashes,
executed/reused steps, result paths and idempotent recipe creation.

The example does not inspect visuals, simulate an actual customer's approval or
establish that another client has installed the new hooks. Those are separate
checks. Initial partial regeneration is at declared file/step granularity;
arbitrary Office-object edits and mutable repository rollback are not supported.
