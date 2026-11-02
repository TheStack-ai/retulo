# Retulo 0.4.0rc1

Local workspaces for AI agents, with incremental workflows and reusable recipes.

The current agent runs producer tools. The core does not launch models, execute
recipe commands, watch folders or publish results. v1 tasks remain supported;
workflow-enabled tasks use v2, which an old engine must reject.

## Included

- Stable task UUIDs and explicit cross-conversation continuation.
- Reserved output paths, immutable versions and byte verification.
- Revision checks, idempotent requests and preserved review history.
- Explicit input bindings and scoped steps for partial regeneration.
- Fixed execution snapshots, complete step output sets and coherent final targets.
- Typed, version-pinned recipes that create new tasks with fresh checks.
- CLI and 29 typed MCP tools with bounded context and explicit detail reads.
- A runnable HTML/SVG report example based on actual repository inventories.
- Preview-first Codex installation and guarded snapshot restoration.
- MIT licensing, dependency notices and reproducible synthetic checks.

## Validation

379 regression tests passed on Python 3.14.4 source and on a normally installed
wheel in a fresh Python 3.11.15 virtualenv. Isolated installation, reinstall,
read-only hooks, real MCP transport and restoration also passed. The real report
example exercised changed-input regeneration, previous-output preservation and
new-task recipe reuse. Independent reviewers reproduced and rechecked fixes.

See [qualification details](QUALIFICATION.md) for methods, earlier failed
attempts, runtime distinctions and limits. Source, wheel and sdist contents are
inspected separately. Download the [published candidate](https://github.com/TheStack-ai/retulo/releases/tag/v0.4.0rc1) or browse the [documentation site](https://thestack-ai.github.io/retulo/site/).

## Scope

Start with the source-install instructions in the README on macOS with a local filesystem. Other operating systems, NAS and multi-host operation are not qualified. Main Brain is optional. A wheel contains the core CLI/MCP, not the source integration installer.

Partial regeneration operates at declared file/step granularity. Recipes do not
inherit approvals or establish that a new input will satisfy a past judgement.
Existing clients must connect to the intended package and skill version; the
example and release checks do not replace an operating installation.

Review and trust the exact new hook definitions in the client. Prior prototype client runs do not substitute for this step. [GitHub-hosted checks](https://github.com/TheStack-ai/retulo/actions/runs/41441041094) passed on macOS with Python 3.11 and 3.14. No general token-cost or content-quality guarantee is made.
