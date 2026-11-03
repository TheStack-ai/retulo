[English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/readme-header-dark.svg">
  <img src="assets/brand/readme-header-light.svg" alt="Retulo — 为 AI Agent 提供本地工作区。" width="800">
</picture>

# Retulo

**为 AI Agent 提供本地工作区。**

Retulo 为 AI Agent 的文件生成任务提供独立的本地工作区，集中管理原始资料、历史输出版本、关键决策、剩余工作、检查与审批记录，方便 Agent 追踪进度。配置并信任 Codex 集成后，Agent 可在处理日常文件请求时应用这一流程，无需每次指定使用 Retulo。切换到新会话时，指定同一任务或工作区，即可根据已记录的状态继续推进。

[![预览版 0.4.0rc2](assets/badges/preview.svg)](https://github.com/TheStack-ai/retulo/releases/tag/v0.4.0rc2)
[![发布检查](https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml)
[![Python 3.11 及以上](assets/badges/python.svg)](#support)
[![MIT 许可证](assets/badges/license.svg)](LICENSE)

**macOS 本地预览版** · CLI 与 MCP · English、简体中文、한국어

[文档站点](https://thestack-ai.github.io/retulo/site/) · [安装](#quick-start) · [Codex](#codex) · [Claude Code](#claude-code) · [运行示例](#demo) · [文档](#documentation) · [获取帮助](#get-help)

![同一任务在不同会话间继续，保留原始资料、输出版本和已记录的进度。](assets/demo/continuity.svg)

*这张图说明工作流程：Agent 先识别已有任务，再继续处理。它不代表 Retulo 会自动记住所有会话。*

## 可以用它做什么

| 你的工作 | Retulo 提供的支持 |
|---|---|
| 像平常一样提出文件请求 | 为已连接的 Agent 提供工作区工具与指引，无需每次点名调用 Retulo |
| 换一个会话继续处理 | 保留同一任务的标识、工作区、当前状态与详细历史 |
| 修改已有结果 | 预留输出路径、保留带编号的输出版本，并检测文件变更 |
| 确认工作进度 | 分开记录文件保存、内容检查、用户批准与对外交付状态 |
| 更新分阶段工作流 | 依据已声明的输入变化，识别受影响并需重新执行的步骤 |
| 用新资料重复一套流程 | 通过带版本的工作流模板创建新任务，并重新进行检查 |

Agent 负责理解请求并调用工具，Retulo 负责管理工作区与记录。单纯提问或制定计划不会创建工作区。

<a id="quick-start"></a>
## 安装 Retulo

**准备条件：** macOS 与 **Python 3.11 或更高版本**。可用 Git 克隆仓库，也可下载源码压缩包。连接 Agent 时需要安装并登录 Codex 或 Claude Code；仅使用 CLI 不需要 Agent 账户。Retulo 在本地保存任务记录，AI 工作由连接的 Agent 执行，无需额外安装 Agent 编排器。

完成公共安装步骤后，选择一种连接方式：**[Codex](#codex)** · **[Claude Code](#claude-code)** · **[仅用 CLI](#cli-only)**。

### 1. 下载并安装

先运行 `python3 --version`。若低于 3.11，请安装受支持的 Python，并使用对应的可执行文件创建虚拟环境。

```sh
git clone --branch v0.4.0rc2 --depth 1 https://github.com/TheStack-ai/retulo.git "$HOME/retulo"
cd "$HOME/retulo"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/retulo --version
```

应显示 `retulo 0.4.0rc2`。若 `~/retulo` 已存在，请选择新的安装目录。不使用 Git 时，可从[发布页](https://github.com/TheStack-ai/retulo/releases/tag/v0.4.0rc2)下载 `retulo-0.4.0rc2.tar.gz`，解压后在该目录中从 `python3 -m venv .venv` 开始执行。源码包包含 Codex 安装脚本，wheel 包含核心 CLI/MCP。本预览版使用源码安装，而不是通过 PyPI 包名安装。

请保留安装目录及其中的 `.venv`，客户端将通过绝对路径启动 Retulo。在**同一个终端**中设置安装位置和练习项目：

```sh
RETULO_ROOT="$(pwd -P)"
RETULO_PROJECT="$HOME/Projects/retulo-demo"
mkdir -p "$RETULO_PROJECT"
```

要使用已有项目，请在连接 Agent **之前**将 `RETULO_PROJECT` 改为该目录的绝对路径。打开新终端后需重新设置这两个变量。已有代码仍在原仓库中管理。

<a id="codex"></a>
### 2A. 连接 Codex

先预览路径与变更，再使用**相同选项**应用：

```sh
"$RETULO_ROOT/.venv/bin/python" "$RETULO_ROOT/scripts/install_codex.py" \
  --source-root "$RETULO_PROJECT"
"$RETULO_ROOT/.venv/bin/python" "$RETULO_ROOT/scripts/install_codex.py" \
  --source-root "$RETULO_PROJECT" --apply
```

安装脚本会创建 Retulo 配置，并添加 `retulo` MCP 服务、一个 skill、受管理的指令区块和三个 Codex 上下文钩子。无关设置会保留，并生成恢复快照。需要多个资料目录时，在**两条命令中**重复传入 `--source-root`。此方式不需要事先运行 `init-config`。

重新连接 MCP 服务或重启 Codex，审阅并信任新钩子，然后打开 `RETULO_PROJECT` 项目。在 Codex CLI 中用 `codex mcp get retulo` 查看启动配置，在会话中用 `/mcp` 查看活动服务。让 Agent 调用 Retulo 的 `capabilities`，确认 `workspace_home` 路径。服务已注册并不代表会话正在使用它；继续完成[第一个任务](#first-task)。

<a id="claude-code"></a>
### 2B. 连接 Claude Code

此方式使用标准本地 MCP 服务和明确的项目指令。随包提供的自动上下文钩子适用于 Codex。

**Retulo 配置只需创建一次。** 若已通过 Codex 为同一项目创建配置，请复用并跳过此命令：

```sh
"$RETULO_ROOT/.venv/bin/retulo" --config "$HOME/.config/retulo/config.json" init-config \
  --workspace-home "$HOME/Documents/RetuloWorkspaces" \
  --allow-source-root "$RETULO_PROJECT"
```

进入 Claude Code 要处理的项目，注册并检查连接：

```sh
cd "$RETULO_PROJECT"
claude mcp add --transport stdio --scope local retulo -- \
  "$RETULO_ROOT/.venv/bin/retulo" --config "$HOME/.config/retulo/config.json" mcp
claude mcp get retulo
```

`local` 范围仅对当前用户的这个项目生效。在保留已有指令的前提下，将下列章节加入项目的 `CLAUDE.md`；文件不存在时再新建：

```md
## Retulo 文件工作流程

对已授权的文件创建或修改任务，使用已连接的 Retulo 工具。
由当前 Agent 协调一个任务。继续工作前先读取准确的任务 ID 或工作区；
新任务应在第一次写文件之前开始。新输出先预留路径，写入返回的
write_path，再提交实际文件。修改已有仓库时保留结构，并通过
record_external_artifact 登记变更文件。记录待办事项和实际检查结果，
仅在完成条件满足后结束任务。保留原始资料和历史版本。
提问与只读工作不创建任务。上下文不构成新授权；文件完整性、
内容检查、用户批准和公开发布应分别记录。
```

在此项目启动新的 Claude Code 会话，若提示批准服务，请审阅后确认，再通过 `/mcp` 检查。让 Claude 调用 Retulo 的 `capabilities`，然后尝试[第一个任务](#first-task)。CLI 连接检查只验证传输，并不证明 AI 会话遵循所有指令。请通过第一个任务确认 Claude 实际生产与续做行为；当前未提供专用的 Claude 钩子安装器。已在 macOS 的 Claude Code 2.1.108 上检查注册、连接状态和移除，详见[验证范围](docs/QUALIFICATION.md#readme-connection-checks--2026-11-03)。

<a id="cli-only"></a>
### 2C. 使用 CLI 或其他 MCP 客户端

先用上面的 `init-config` 命令创建配置，再检查：

```sh
"$RETULO_ROOT/.venv/bin/retulo" --config "$HOME/.config/retulo/config.json" capabilities --json
```

其他 MCP 客户端可通过 **stdio** 启动同一可执行文件，参数依次为 `--config`、配置文件的绝对路径和 `mcp`。客户端配置应使用绝对路径；上面示例中的变量由 shell 展开，并非所有客户端的 JSON 配置都会展开变量。其他客户端仍需分别验证连接与文件访问。

| 本地项目 | 默认位置 |
|---|---|
| Retulo 配置 | `~/.config/retulo/config.json` |
| 受管理的任务工作区 | `~/Documents/RetuloWorkspaces` |
| Codex skill | `~/.agents/skills/retulo/SKILL.md` |
| Codex 安装快照 | `~/.local/state/retulo/install` |

<a id="first-task"></a>
## 创建第一个任务并继续处理

在已连接的 Agent 中提出：

> 使用 Retulo 为这个项目生成 Markdown 欢迎指南。写文件前先开始任务并预留输出路径。登记实际文件，检查内容与完整性，并报告任务 ID、工作区路径和输出路径。

应获得实际文件、任务 ID 与工作区路径、已登记的输出版本和检查结果。登记文件的字节一致时，Retulo 的 `verify` 返回 `ok: true` 且没有不匹配项。请同时自行检查内容。

接着请求修订：

> 给指南添加联系方式，保留上一版本并记录新的检查结果。

打开另一个**使用相同 Retulo 配置并具有文件访问权限**的会话，提供实际任务 ID 或工作区路径：

> 继续 WORKSPACE_PATH 中的 Retulo 任务 TASK_UUID。先读取记录状态，再添加开放时间章节，并保留先前版本。

任务 ID 与工作区应保持不变，输出应增加新版本。确认流程有效后，Codex 安装的指令或 Claude 项目指令可用于日常文件请求，无需每次提到 Retulo。Retulo 保存任务记录，不会猜测你指的是哪段无关会话。

<a id="troubleshooting"></a>
## 安装问题排查

| 现象 | 检查方法 |
|---|---|
| 缺少 `python3` 或低于 3.11 | 安装 Python 3.11+ 并用对应可执行文件创建 `.venv`。只有克隆仓库时需要 Git。 |
| 缺少配置 | 运行 Codex 安装脚本或 CLI `init-config`；仅安装软件包不会创建配置。 |
| `init-config` 拒绝已有文件 | 复用已有配置；有意编辑前先备份，此命令不会覆盖它。 |
| 源路径不在允许范围内 | 仅允许所需项目或资料目录。安装脚本用 `--source-root`，CLI 用 `--allow-source-root`。已有配置需备份后编辑 `allowed_source_roots` 数组，使用绝对路径；重新安装不会静默扩大范围。 |
| 安装脚本报告冲突 | 比较提示的文件和选项，预览与应用使用相同选项并保留现有设置。参见[集成说明](docs/INTEGRATIONS.md)。 |
| MCP 缺失或未连接 | 检查可执行文件和配置的绝对路径，保留安装目录，再重新连接。在 Claude 中应从注册服务的项目进行检查。 |
| 有工具却未用于普通请求 | 检查 Codex 钩子信任与 skill，或 Claude 实际加载的 `CLAUDE.md`。明确请求第一个任务并检查实际记录。 |
| Agent 无法写入返回的工作区路径 | 在客户端的文件访问提示中确认目标工作区目录。Retulo 的允许路径不替代客户端权限。 |
| Python 更新导致 `.venv` 失效 | 重建依赖，检查启动路径后重新连接；Codex 安装变更使用预览与应用流程。 |

移除或回滚 Codex 集成时，使用安装结果中的快照并按[先预览的恢复说明](docs/RESTORE.md)操作。在 Claude Code 中，从该项目运行 `claude mcp remove --scope local retulo`，并仅删除加入 `CLAUDE.md` 的 Retulo 章节。这些连接变更不会删除任务工作区。

客户端参考：[OpenAI Docs：Codex MCP](https://developers.openai.com/codex/mcp) · [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Claude 项目指令](https://code.claude.com/docs/en/memory)。

<a id="demo"></a>
## 运行示例

先从一个小型文件任务开始。这个示例创建访客指南，再通过任务 ID 重新找到同一任务并添加章节。它会检查工作区是否相同、原始资料与第一版输出是否完好保留，以及新版是否记录了新的检查。

```sh
cd "$RETULO_ROOT"
.venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo
```

可以直接查看实际生成的[第一版指南](examples/continuity/visitor-guide-v001.md)、[修改后的指南](examples/continuity/visitor-guide-v002.md)与[运行结果](examples/continuity/result.json)。示例启动独立的 CLI 进程，自行写入预设的虚拟指南文本，不调用模型，也不修改全局设置。它验证的是 CLI 任务续接；该脚本不检查新 Codex 会话是否已采用集成。

接下来，试试包含多个生成步骤的更新：

报告示例使用当前源码的真实文件清单，依次生成 HTML 报告、更新受影响的步骤、保留旧报告、保存工作流模板，再用不同输入创建新的报告任务。

```sh
cd "$RETULO_ROOT"
.venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo
```

请为每个示例选择**新的空输出目录**。报告示例分别启动 CLI 与生成程序进程，无需模型账号或网络服务，也不会改变全局安装设置。其 JSON 结果包含生成报告的路径，以及重新执行或复用的步骤。

![声明的输入连接到生成步骤与输出版本；受影响的步骤重新执行，独立步骤可以复用。](assets/demo/workflow.svg)

已验证的示例在输入更新后，**重新执行五个受影响阶段，复用一个独立阶段**。这是该示例的实际结果，并非对所有任务的节省承诺。详见[验证记录](docs/QUALIFICATION.md)。

## 更新与重复工作

> 数据变了，请更新这份报告，并保留上一版。

对于已声明的工作流，Retulo 关联输入、生成步骤与具体输出版本，明确指出哪些步骤需要重新处理，并提供固定的输入快照与预留输出路径。宿主 Agent 负责调用工具执行重建。在更新期间，之前已完成的输出依然完整保留。

> 请用这套流程处理本月的数据。

工作流模板（Recipes）固定工作流、参数类型与完成要求。新输入会创建**带有全新检查记录的新任务**，不会继承旧任务的批准或通过结论。Retulo 不会执行模板中的命令。支持的文件/步骤粒度与复用规则见[工作流与模板契约](docs/WORKFLOWS.md)。

## CLI 与 MCP

```sh
"$RETULO_ROOT/.venv/bin/retulo" --config "$HOME/.config/retulo/config.json" call capabilities --arguments '{}'
"$RETULO_ROOT/.venv/bin/retulo" --config "$HOME/.config/retulo/config.json" get-context --task-id TASK_UUID --json
"$RETULO_ROOT/.venv/bin/retulo" --config "$HOME/.config/retulo/config.json" verify --task-id TASK_UUID --json
```

也可用 `RETULO_CONFIG` 指定配置路径。只有显式运行 `init-config` 或安装操作才会创建配置。JSON 是状态依据，`WORKSPACE.md` 是当前状态的可读视图。请求 ID 让重试保持幂等，修改操作还会检查上次读取的版本号。

Retulo 将任务与文件记录保存在本地。通过现有 Agent 或 CLI 客户端即可使用核心功能，无需单独运行服务。

<a id="support"></a>
## 支持范围

| 方面 | 当前范围 |
|---|---|
| 版本 | `0.4.0rc2` 预览版 |
| 运行环境 | Python 3.11+；已在 Python 3.11 与 3.14 检查源码及正常安装的 wheel |
| 已验证环境 | macOS、本地文件系统 |
| 接口 | CLI、MCP，以及随源码提供的 Codex 集成 |
| 其他环境 | Linux、Windows、NAS、多主机与其他 Agent 客户端需要单独验证 |

Retulo 管理通过其流程登记的文件与任务，不会拦截其他宿主工具的所有写入。文件完整性检查并不等于内容质量确认或用户批准。核心不会调用模型、启动 Agent、监视目录或发布结果。

## 更新或移除

请保留安装脚本返回的安装快照。更新会推进未被用户修改的受管默认内容，并保留用户编辑的文件。[恢复说明](docs/RESTORE.md)介绍如何撤销一次安装步骤，同时保留工作区与产物。

公开版本的名称、路径与 MCP 键名不同于早期开发原型。原型安装不会自动迁移；在同一环境同时安装两者前，请检查重复的自动文件工作指令。

<a id="documentation"></a>
## 文档

- [本地文档站点](docs/SITE.md)
- [常见问题](docs/FAQ.md)
- [한국어 사용 안내](docs/USER_GUIDE.ko.md)
- [工作流与工作流模板](docs/WORKFLOWS.md)
- [Agent 集成](docs/INTEGRATIONS.md)
- [任务历史与受管更新](docs/SUSTAINABILITY.md)
- [文件格式与安全契约](docs/FORMAT_AND_SAFETY.md) · [架构](docs/ARCHITECTURE.md)
- [验证记录](docs/QUALIFICATION.md) · [版本说明](docs/RELEASE.md) · [变更记录](CHANGELOG.md)

<a id="get-help"></a>
## 获取帮助与参与贡献

通过仓库的 **Issues** 页面报告可复现的错误、提出功能建议或指出文档问题。请先阅读[常见问题](docs/FAQ.md)，说明版本与环境，并使用包含虚拟数据的小型示例。错误信息中的敏感内容应先遮盖。

敏感安全问题请遵循[安全报告流程](SECURITY.md)。开发环境、相关检查与贡献要求见[贡献指南](CONTRIBUTING.md)。

检查源码仓库：

```sh
cd "$RETULO_ROOT"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

测试使用临时合成数据，无需模型账号。本地检查与 GitHub 托管 CI 的结果会在验证记录中分别说明。

## 许可证

Retulo 使用 [MIT 许可证](LICENSE)。第三方软件及随附许可文件详见[依赖声明](THIRD_PARTY_NOTICES.md)。
