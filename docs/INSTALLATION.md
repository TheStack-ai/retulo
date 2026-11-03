# Installation and agent connection

This guide covers the `0.5.0rc1` preview. The normal installation uses a GitHub release wheel and a persistent uv tool environment. You do not need to keep a source checkout or maintain a virtual environment.

## Install on macOS or Linux

```sh
curl -fsSL https://thestack-ai.github.io/retulo/install.sh | sh
retulo --version
```

Expect `retulo 0.5.0rc1`. The bootstrap downloads this release's wheel and `SHA256SUMS`, verifies the wheel's SHA-256 digest, and installs it with uv. It uses an existing uv if available, otherwise installs a private copy; uv obtains a suitable Python when needed. The wheel includes the CLI, MCP server, agent setup and restoration code, and managed instruction templates.

The default command directory is `~/.local/bin`. If it is missing from your `PATH`, the installer prints the command to run in your current terminal:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer does not edit shell profiles. To make the command available in future terminals, add the printed export to your own shell configuration. The installed environment persists between invocations, so agent launchers do not depend on an ephemeral `uvx` session.

macOS with a local filesystem is the tested environment. The bootstrap targets macOS and Linux; Linux installation is intended for CI qualification. Windows requires manual Python setup and separate validation; the shell bootstrap is not a Windows installer. See [qualification](QUALIFICATION.md) for recorded results.

Retulo is distributed here through [GitHub Releases](https://github.com/TheStack-ai/retulo/releases/tag/v0.5.0rc1). This preview is not published to PyPI or npm. Installing an unqualified package name from another registry is not this installation route.

### Review the installer or customize its execution

You can download and inspect the same script before executing it:

```sh
curl -fsSL https://thestack-ai.github.io/retulo/install.sh -o /tmp/retulo-install.sh
less /tmp/retulo-install.sh
sh /tmp/retulo-install.sh --help
sh /tmp/retulo-install.sh --dry-run
sh /tmp/retulo-install.sh
```

The script supports `--bin-dir DIR` for the command directory, `--uv PATH` for a specific uv executable, and `--asset-dir DIR` for a directory containing the release wheel and `SHA256SUMS`. The asset directory supplies the Retulo release assets; installing uv, Python, or dependencies can still require downloads. `--dry-run` previews its planned commands. Run `--help` for the current options.

### Use an existing uv installation

If you already manage uv and Python, install the same release wheel directly:

```sh
uv tool install --python 3.11 https://github.com/TheStack-ai/retulo/releases/download/v0.5.0rc1/retulo-0.5.0rc1-py3-none-any.whl
retulo --version
```

The bootstrap additionally performs the published `SHA256SUMS` check for you. Both routes keep a persistent tool environment. Do not configure an agent by running `uvx` for setup: a temporary cache interpreter is unsuitable for a saved MCP launcher.

## Connect an existing project

Install and sign in to **Codex or Claude Code** independently. Retulo adds local workspace tools and guidance; the client continues to handle AI execution and its own permissions. The Claude integration is for the local Claude Code client, not claude.ai.

Replace `/absolute/path/to/project` with an **existing project directory**. Setup defaults to the current directory if `--project` is omitted, but an explicit path makes the target clear. It rejects the filesystem root, your whole home directory, missing directories, and paths containing symbolic links. Existing repositories remain in place.

Setup is a preview unless you add `--apply`. Review the reported files and paths, then apply the same options.

### Codex

```sh
retulo setup codex --project /absolute/path/to/project
retulo setup codex --project /absolute/path/to/project --apply
retulo doctor codex --project /absolute/path/to/project --connect
```

The setup command:

- Creates the Retulo configuration if needed, or preserves and updates the existing configuration's allowed source roots for the selected project.
- Registers the `retulo` MCP server in user-level Codex settings using the installed package's persistent Python runtime.
- Installs a Retulo skill, a managed block in user-level `AGENTS.md`, and Codex context hooks.
- Preserves unrelated settings and saves installation snapshots for guarded restoration.

Restart Codex and open a new chat in the selected project. Review and trust the installed hooks when the client requests it. In Codex CLI, use `codex mcp get retulo` to inspect the launcher and `/mcp` to check active servers. Ask the agent to call Retulo's `capabilities` and confirm `workspace_home`.

The Codex connection is user-level; its Retulo configuration limits which existing source directories the engine accepts. Run setup with each additional project you intend to allow. It preserves existing allowlist entries. Retulo's roots do not replace the client's file-access permissions.

### Claude Code

```sh
retulo setup claude --project /absolute/path/to/project
retulo setup claude --project /absolute/path/to/project --apply
retulo doctor claude --project /absolute/path/to/project --connect
```

The setup command manages only the `retulo` server entry in the project's `.mcp.json` and a marked Retulo instruction block in its `CLAUDE.md`. It creates or updates the shared Retulo configuration for the selected project, preserves unrelated configuration and instructions, and saves restoration snapshots. You do not need to paste instructions or construct MCP JSON yourself. Claude Code does not receive the Codex context hooks.

Start a new Claude Code session in that project. Trust the project, approve the MCP server if prompted, and check `/mcp`. Ask Claude to call Retulo's `capabilities`. Because `.mcp.json` is project-level, setup must run in each project where you want this connection.

### Check actual work

`doctor` checks configuration, the installed runtime, managed integration files, source scope, and relevant directory access. Adding `--connect` launches the configured Retulo server, performs an MCP handshake, checks the tool list, and calls `capabilities`. It makes no model call and does not run a production task.

A successful local check does not establish that a new agent chat follows the file workflow. Run the [first-task and continuation exercise](../README.md#first-task) in the connected client, then inspect the actual task, output versions, and check records.

## Custom paths

| Option | Meaning | Default |
|---|---|---|
| `--project` | Existing project to allow and connect | Current directory |
| `--config` | Retulo configuration file | `RETULO_CONFIG` or `~/.config/retulo/config.json` |
| `--workspace-home` | Managed workspace location | Existing configuration or `~/Documents/RetuloWorkspaces` |
| `--codex-home` | Codex user settings directory | `~/.codex` |
| `--skills-home` | Parent directory for the Codex skill | `~/.agents/skills` |
| `--snapshot-home` | Installation snapshot directory | `~/.local/state/retulo/install`; Claude uses its `claude/` subdirectory |

Setup, doctor, and disconnect accept these path options; `--codex-home` and `--skills-home` apply to Codex. `--config` is also accepted before the command. Preserve the same non-default options across preview, apply, diagnosis, and disconnection. Existing configurations keep their workspace location; a different `--workspace-home` does not silently migrate existing workspaces.

For example, preview and apply a custom configuration and workspace location:

```sh
retulo setup codex --project /absolute/path/to/project \
  --config /absolute/path/to/retulo-config.json \
  --workspace-home /absolute/path/to/RetuloWorkspaces
retulo setup codex --project /absolute/path/to/project \
  --config /absolute/path/to/retulo-config.json \
  --workspace-home /absolute/path/to/RetuloWorkspaces --apply
```

## CLI-only use and another MCP client

If no agent setup has created a configuration, create one explicitly:

```sh
retulo init-config --workspace-home "$HOME/Documents/RetuloWorkspaces" \
  --allow-source-root /absolute/path/to/project
retulo capabilities --json
```

`init-config` never replaces an existing configuration. Repeat `--allow-source-root` when initially configuring several source directories. Questions and read-only checks do not create tasks or workspaces.

For another MCP client, use the absolute executable path returned by `command -v retulo` with arguments `--config`, the absolute configuration path, and `mcp`, over stdio. Keep absolute local paths in the client's configuration. Every other client needs its own connection and file-access validation.

## Disconnect and update

Run the bootstrap again to install this release. After a runtime or integration update, preview and apply setup with the same project and custom path options, then restart the agent client so it loads the updated connection. Keep installation snapshots; they preserve the settings needed for guarded rollback.

To remove the managed connection:

```sh
retulo disconnect codex --project /absolute/path/to/project
retulo disconnect codex --project /absolute/path/to/project --apply
```

For Claude Code, use `claude` instead:

```sh
retulo disconnect claude --project /absolute/path/to/project
retulo disconnect claude --project /absolute/path/to/project --apply
```

Disconnect preserves Retulo tasks, workspace files, artifacts, and the core configuration, including its recorded roots. It removes the managed client connection using the installation snapshots and checks for conflicts. For Codex this is a user-level connection, so disconnection affects that Codex setup across projects. For Claude Code it affects the selected project's connection. It does not uninstall Codex or Claude Code. The [restore guide](RESTORE.md) covers snapshot inspection and rollback details.

Prototype installations with different public names, paths, or MCP keys are not migrated automatically. Review overlapping automatic file-work instructions before using both in the same environment.

## Development only: source checkout

This section is for contributors and the repository's example scripts. It is optional for normal installation and agent connection.

Use Python 3.11 or later and a new development directory:

```sh
git clone --branch v0.5.0rc1 --depth 1 https://github.com/TheStack-ai/retulo.git "$HOME/retulo-dev"
cd "$HOME/retulo-dev"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/retulo --version
```

You can instead unpack the release source archive and run the commands from `python3 -m venv .venv` inside it. The repository's [contribution guide](../CONTRIBUTING.md) describes focused checks and development expectations.

From the prepared checkout, run the optional examples with new, empty output directories:

```sh
.venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo
.venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo
```

These examples use separate CLI processes and synthetic local work, not model calls or a new client chat. See their [qualification scope](QUALIFICATION.md). A client connected to a development runtime still needs that checkout and environment to remain available; the normal release installation does not depend on them.
