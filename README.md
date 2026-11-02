[English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/readme-header-dark.svg">
  <img src="assets/brand/readme-header-light.svg" alt="Retulo — Local workspaces for AI agents." width="800">
</picture>

# Retulo

**Local workspaces for AI agents.**

Retulo gives AI agents a dedicated local workspace for file-producing work. It keeps source material, previous output versions, decisions, remaining work, checks, and approvals together, helping the agent keep track of the work. Once the Codex integration is connected and trusted, your agent can apply this workspace procedure to everyday file requests without being asked to use Retulo each time. When resuming in another conversation, identify the task or workspace to continue from its recorded state.

[![Preview 0.4.0rc1](assets/badges/preview.svg)](docs/RELEASE.md)
[![Local checks; see qualification](assets/badges/checks.svg)](docs/QUALIFICATION.md)
[![Python 3.11 or later](assets/badges/python.svg)](#support)
[![MIT license](assets/badges/license.svg)](LICENSE)

**macOS local preview** · CLI and MCP · English, 简体中文, 한국어

[Documentation site](https://thestack-ai.github.io/retulo/site/) · [Quick start](#quick-start) · [Demo](#demo) · [Docs](#documentation) · [Get help](#get-help)

![One task continues across conversations, keeping its sources, versions, and recorded progress.](assets/demo/continuity.svg)

*The diagram explains the workflow: the agent identifies the existing task before continuing it. Retulo does not automatically remember every conversation.*

## What you can do

| Your work | What Retulo provides |
|---|---|
| Ask for a file in the usual way | Workspace tools and guidance for the connected agent, without naming Retulo in every request |
| Continue in another conversation | The same task identity, workspace, current state, and detailed history |
| Revise an existing result | Reserved output paths, numbered output versions, and change detection |
| Check what is finished | Separate records for saved files, content checks, user approval, and external delivery |
| Update a staged workflow | Which steps are affected by changes to declared inputs |
| Repeat a process with new material | Versioned workflow recipes that start new tasks with fresh checks |

The agent interprets your request and runs its tools. Retulo manages the workspace and records. Questions and planning alone do not create a workspace.

<a id="quick-start"></a>
## Quick start

Use **Python 3.11 or later** on macOS. Unpack the source archive or open a source checkout, then run these commands from its project directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/retulo --help
```

The last command should display Retulo's command help. This preview uses source installation; the wheel provides the core CLI/MCP package, while the Codex installer is included in the source checkout.

Preview the Codex integration, review the listed changes, then apply it:

```sh
.venv/bin/python scripts/install_codex.py
.venv/bin/python scripts/install_codex.py --apply
```

The installer adds a `retulo` MCP server, one skill, a managed instruction block, and three context hooks. It preserves unrelated configuration and reports conflicting user edits. Review and trust the hook definitions in Codex, then connect the integration. The installer does not restart an existing MCP connection, and installation alone does not establish that the client has adopted it.

| Item | Default location |
|---|---|
| Configuration | `~/.config/retulo/config.json` |
| Workspaces | `~/Documents/RetuloWorkspaces` |
| Skill | `~/.agents/skills/retulo/SKILL.md` |
| Installation snapshots | `~/.local/state/retulo/install` |

Keep the source checkout in place while its integration is installed. Use the installer's path flags for other locations. To work in an existing repository, add that specific directory with `--source-root PATH`; this option can be repeated. Existing code stays in its repository.

## Start, revise, and continue

After connecting and trusting the integration, request ordinary work:

> Create a visitor guide as a Markdown file. Preserve the source material.

> Fix this project's total calculation and run its existing tests.

The agent identifies or starts the task before writing, uses the reserved output path, registers the saved file, and records the relevant checks.

To continue in another conversation, provide the task ID, workspace path, or a previous artifact:

> Continue the guide in this workspace. Add a contact section and keep the previous version.

Retulo returns a short view of the current state and remaining work. Detailed records stay available when needed. It does not guess which unrelated conversation you meant or automatically restart a stopped task.

<a id="demo"></a>
## See it work

Start with a small file task. This example creates a visitor guide, then uses a fresh coordinator to find the same task by ID and add a section. It checks that the workspace stays the same, the source and first output remain intact, and the new output has fresh checks.

```sh
.venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo
```

Read the actual [first guide](examples/continuity/visitor-guide-v001.md), [continued guide](examples/continuity/visitor-guide-v002.md), and [recorded result](examples/continuity/result.json). The example runs separate CLI processes and writes its own synthetic guide text. It makes no model calls and leaves global settings alone. It verifies CLI task continuity; adoption by a new Codex conversation is not tested by this script.

Then try an update across several production steps:

The report example uses this checkout's real file inventory. It creates an HTML report, updates the affected steps, preserves the earlier report, saves a recipe, and starts a new report task with different inputs.

```sh
.venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo
```

Choose a **new, empty output directory** for each example. The report example uses separate CLI and producer processes. It requires no model account or network service and does not change global installation settings. Its JSON result includes the generated report paths and the steps that were rebuilt or reused.

![Declared inputs connect to production steps and output versions; affected steps can be rebuilt while an independent step is reused.](assets/demo/workflow.svg)

The qualified example rebuilt **five affected stages and reused one independent stage** after an input update. This is an observed example, not a general saving guarantee. See the [qualification record](docs/QUALIFICATION.md).

## Update and repeat work

> The data changed. Update this report and preserve the previous version.

For a declared workflow, Retulo connects inputs, steps, and exact output versions. It identifies which steps need work and supplies fixed input snapshots and reserved output paths. The host agent runs the producer tools. Previous completed results remain available while an update is in progress.

> Use the same process with this month's data.

A recipe pins a workflow, typed parameters, and completion requirements. New inputs create a **new task with fresh checks**; previous approvals and passed results are not inherited. Retulo does not execute commands from a recipe. Read the [workflow and recipe contract](docs/WORKFLOWS.md) for the supported file/step granularity and reuse rules.

## CLI and MCP

```sh
.venv/bin/retulo --config CONFIG_PATH call capabilities --arguments '{}'
.venv/bin/retulo --config CONFIG_PATH get-context --task-id TASK_UUID --json
.venv/bin/retulo --config CONFIG_PATH verify --task-id TASK_UUID --json
```

`RETULO_CONFIG` can supply the configuration path. Configuration is created only by an explicit `init-config` or installer operation. JSON is the state contract; `WORKSPACE.md` is a readable view of current state. Request IDs make retries idempotent, and mutations check the last observed revision.

Main Brain is an optional evidence and memory integration. Neither Main Brain nor an additional agent orchestrator, including OmO/OmO Native, is required for core operation.

<a id="support"></a>
## Support and scope

| Area | Current scope |
|---|---|
| Release | `0.4.0rc1`, preview |
| Runtime | Python 3.11+; source and installed-wheel checks on Python 3.11 and 3.14 |
| Qualified environment | macOS, local filesystem |
| Interfaces | CLI and MCP; packaged Codex integration |
| Other environments | Linux, Windows, NAS, multiple hosts, and other agent clients need separate validation |

Retulo operates on the files and tasks registered through its workflow. It does not intercept all writes made by unrelated host tools. File integrity checks do not establish content quality or user approval. The core does not call models, launch agents, watch folders, or publish results.

## Update or remove

Keep the installation snapshot returned by the installer. Updates advance unchanged managed defaults and preserve user-edited files. [Restore instructions](docs/RESTORE.md) explain how to reverse an installation step without deleting workspaces or their artifacts.

The public name, paths, and MCP key differ from the earlier development prototype. Prototype installations are not migrated automatically. Review overlapping automatic file-work instructions before installing both in the same environment.

<a id="documentation"></a>
## Documentation

- [Local documentation site](docs/SITE.md)
- [Frequently asked questions](docs/FAQ.md)
- [한국어 사용 안내](docs/USER_GUIDE.ko.md)
- [Workflows and recipes](docs/WORKFLOWS.md)
- [Agent and optional memory integration](docs/INTEGRATIONS.md)
- [Task history and managed upgrades](docs/SUSTAINABILITY.md)
- [Format and safety contract](docs/FORMAT_AND_SAFETY.md) · [Architecture](docs/ARCHITECTURE.md)
- [Qualification](docs/QUALIFICATION.md) · [Release notes](docs/RELEASE.md) · [Changelog](CHANGELOG.md)

<a id="get-help"></a>
## Get help and contribute

Use the repository's **Issues** tab to report a reproducible bug, propose a feature, or point out a documentation problem. Start with the [FAQ](docs/FAQ.md), include your version and environment, and use a small example with synthetic data. Share only redacted error details.

For sensitive findings, follow [Security reporting](SECURITY.md). For development setup, focused checks, and contribution expectations, see [Contributing](CONTRIBUTING.md).

To verify a source checkout:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

Tests use temporary synthetic fixtures and require no model account. Local checks and GitHub-hosted CI results are reported separately in the qualification record.

## License

Retulo is [MIT licensed](LICENSE). See [dependency notices](THIRD_PARTY_NOTICES.md) for third-party software and included license files.
