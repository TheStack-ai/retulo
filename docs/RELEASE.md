# Retulo 0.4.0rc2

Local workspaces for AI agents, with incremental workflows and reusable recipes.

This candidate removes a private memory-system bridge from the public package.
Retulo provides 24 task, file, workflow and recipe tools through its CLI and MCP.
It remains independent of an additional orchestration runtime. Existing local
configuration files must contain only the documented core settings; unsupported
keys fail with a configuration error and are not executed.

## Security correction

The development release inspector previously parsed every TAR/ZIP metadata
record before applying its 10,000-member limit. A deliberately supplied hostile
archive could therefore waste developer-process memory and CPU. ZIP and ZIP64
central records are now counted before `ZipFile` construction; TAR physical
headers, extension bodies, nesting, total bytes and effective PAX metadata are
bounded as they are parsed. Sparse TAR forms are rejected by the inspector.
The inspector never extracts archives. This issue concerns manually invoked
release checks; it does not introduce a runtime network endpoint.

## Preserved functionality

- Stable task UUIDs and explicit continuation across conversations.
- Reserved paths, immutable output versions, integrity and revision checks.
- v1 tasks, opt-in v2 workflows, scoped regeneration and execution history.
- Typed, version-pinned recipes that create new tasks with fresh checks.
- Preview-first Codex installation and guarded snapshot restoration.
- English, Chinese and Korean documentation with the existing visual design.

See [qualification](QUALIFICATION.md) for the exact checks and their scope.
Download the [candidate release](https://github.com/TheStack-ai/retulo/releases/tag/v0.4.0rc2)
or browse the [documentation site](https://thestack-ai.github.io/retulo/site/).

## Scope

The qualified target is macOS with a local filesystem and Python 3.11 or later.
A wheel contains the CLI/MCP core; install the Codex integration from the source
package. Review and trust the exact hook definitions in the client. Tests do not
establish user approval or automatic adoption by an already-connected client.
The core does not launch models, execute recipe commands, watch folders or publish
results. Other operating systems, NAS and multi-host operation are not qualified.
