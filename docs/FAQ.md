# Frequently asked questions

[English overview](../README.md) · [简体中文介绍](../README.zh-CN.md) · [한국어 소개](../README.ko.md)

## What is Retulo?

Retulo is a local workspace engine for AI agents doing file-producing work. It records a task's identity, goal, source material, output versions, decisions, remaining work, checks, and approvals. A connected agent uses those records to follow the work and continue it later. Retulo also identifies affected steps in declared workflows and manages versioned recipes for repeated work.

## Do I have to mention Retulo in every request?

After the Codex integration is connected and trusted, its guidance asks the current agent to use Retulo for ordinary authorized file work. You can ask for a document or code change in the usual way. The agent still decides whether the request is file work, which task it belongs to, and which tools to run. Questions and planning alone do not create a workspace.

The context hooks only supply bounded, read-only task candidates. They do not create, select, resume, or complete a task on their own. Installing the files does not prove that an already-running client has adopted them; review and connect the exact installed integration. See the [integration contract](INTEGRATIONS.md).

## How do I continue in another conversation?

Provide the existing task ID, workspace path, or a previous artifact. The agent identifies the same task, inspects the registered files, and reads a concise view of the current state. Detailed history remains available when needed.

A task ID is independent of a conversation ID. One conversation can include several tasks, and several conversations can work on the same task when they are explicitly connected. Retulo does not remember every chat automatically or infer an unseen task history. It does not restart a stopped task without a current request.

The [continuity example](../examples/continuity/README.md) checks this through real CLI processes: a fresh coordinator finds the same task and saves a second guide version while preserving the source and first output. The script uses synthetic text and makes no model calls. It does not test a newly connected Codex conversation.

## What happens to source material and earlier results?

Sources are registered as references or preserved copies, according to the selected operation. Retulo does not automatically move or delete the originals. Managed outputs use reserved staging paths and are registered as numbered versions after the saved bytes are observed. Earlier versions remain available. Changed or missing registered files are reported during verification or resume.

An existing code repository keeps its layout and source files. The configured source root lets Retulo register the relevant code changes and checks without moving the project into a new folder.

## Does Retulo control every write made by the agent?

No. Its path, revision, and preservation checks apply to operations performed through the registered Retulo workflow. It is not a sandbox or an interceptor for arbitrary shell commands, editors, hosted generators, or other agent tools. The host remains responsible for following the returned paths and the user's authorized scope. See [format and safety](FORMAT_AND_SAFETY.md).

## Does a verified file mean the result is good or approved?

File integrity, content validation, user approval, and external delivery are separate records. Matching a saved hash confirms the registered bytes; it does not judge the writing, design, calculations, or suitability of the content. A check records its stated scope and evidence. Approval belongs to a specific artifact version, and a new version does not inherit it automatically.

## Does Retulo run models, agents, or recipe commands?

The core does not call a model, launch an agent, execute a recipe action, watch folders, or publish results. The current host agent interprets the request and runs its existing producer and validation tools. Retulo supplies workspace state, fixed workflow inputs, reserved outputs, and the records needed to assess progress.

Retulo can hold goals, completion criteria, and unresolved questions to help the agent work clearly. Interpreting an ambiguous request and producing a useful result remain the agent's responsibilities.

## How does partial regeneration work?

The agent first declares the workflow's file inputs, steps, parameters, relevant environment information, and outputs. Retulo compares the current observations with the recorded runs and identifies which steps need work. Reuse also requires intact outputs and the necessary checks; it is not based on an old success label alone.

The host agent executes the affected steps. Previous completed results remain preserved while the update is in progress. The supported unit is a declared file or step; Retulo does not automatically discover every dependency or edit individual elements inside arbitrary documents. See [workflows and recipes](WORKFLOWS.md).

## When should I use a recipe instead of continuing a task?

Continue the existing task when revising the same result. Use a recipe when repeating a process for new material, a different period, or another customer. A recipe fixes a workflow revision, typed inputs, and completion requirements. A new recipe task starts with fresh checks and does not inherit old approvals or passed verdicts.

## Are Main Brain or OmO required?

Neither is required for installation or core operation. Main Brain is an optional integration for retrieving and storing decisions and evidence with provenance. It has a separate role from the task and file ledger. OmO/OmO Native are optional executors governed by the host's own rules. Removing them does not remove Retulo's task records, tools, or output versions. See [integrations](INTEGRATIONS.md) and [task history](SUSTAINABILITY.md).

## Which environments are verified?

The current preview is qualified on macOS with a local filesystem. Python 3.11 is the minimum; source and normally installed-wheel checks have run on Python 3.11 and 3.14. CLI/MCP and the packaged Codex integration have distinct qualification records. Linux, Windows, NAS, multiple hosts, and other agent clients need their own validation.

Read the [qualification record](QUALIFICATION.md) for exact methods and limits. Local checks do not imply a GitHub-hosted CI run or automatic adoption by an existing client connection.

## How do I try it without changing my Codex settings?

Install the source into a virtual environment and run the [continuity or report example](../README.md#demo) with a new, empty output directory for each run. They use real CLI processes, require no model account or network service, and leave global installation settings alone. The Codex installer also previews its changes unless you pass `--apply`.

## Can I update or remove the integration?

Keep the installation snapshot returned by the installer. Updates preserve user edits and report conflicts. The restore tool can reverse an installation step after checking the exact paths and recorded bytes, without deleting task workspaces. Follow the [restore instructions](RESTORE.md); apply and restore tests should use isolated settings, not real user configuration.

## Where should I ask for help?

Use the repository's Issues tab for a reproducible bug, feature request, or documentation problem. Include your version, environment, and a small example with synthetic data. Do not upload whole workspaces, conversation histories, private source documents, or credentials. Use the [security reporting route](../SECURITY.md) for sensitive findings and the [contribution guide](../CONTRIBUTING.md) for development work.
