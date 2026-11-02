[English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/readme-header-dark.svg">
  <img src="assets/brand/readme-header-light.svg" alt="Retulo — 为 AI Agent 提供本地工作区。" width="800">
</picture>

# Retulo

**为 AI Agent 提供本地工作区。**

Retulo 为 AI Agent 的文件生成任务提供独立的本地工作区，集中管理原始资料、历史输出版本、关键决策、剩余工作、检查与审批记录，方便 Agent 追踪进度。配置并信任 Codex 集成后，Agent 可在处理日常文件请求时应用这一流程，无需每次指定使用 Retulo。切换到新会话时，指定同一任务或工作区，即可根据已记录的状态继续推进。

[![预览版 0.4.0rc1](assets/badges/preview.svg)](docs/RELEASE.md)
[![本地检查；详见验证记录](assets/badges/checks.svg)](docs/QUALIFICATION.md)
[![Python 3.11 及以上](assets/badges/python.svg)](#support)
[![MIT 许可证](assets/badges/license.svg)](LICENSE)

**macOS 本地预览版** · CLI 与 MCP · English、简体中文、한국어

[文档站点](https://thestack-ai.github.io/retulo/site/) · [快速开始](#quick-start) · [运行示例](#demo) · [文档](#documentation) · [获取帮助](#get-help)

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
## 快速开始

在 macOS 上使用 **Python 3.11 或更高版本**。解压源码包或打开源码仓库，然后在项目目录中执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/retulo --help
```

最后一条命令应显示 Retulo 的命令帮助。本预览版采用源码安装；wheel 包含核心 CLI/MCP，Codex 安装脚本位于源码目录中。

先预览 Codex 集成的变更，核对列出的内容，再执行安装：

```sh
.venv/bin/python scripts/install_codex.py
.venv/bin/python scripts/install_codex.py --apply
```

安装脚本会添加 `retulo` MCP 服务、一个 skill、受管理的指令区块与三个上下文钩子。它会保留无关配置，并报告与用户修改冲突的内容。请在 Codex 中审阅并信任这些钩子的确切定义，再连接集成。安装脚本不会重启已有 MCP 连接；安装成功也不代表客户端已经采用新集成。

| 项目 | 默认位置 |
|---|---|
| 配置 | `~/.config/retulo/config.json` |
| 工作区 | `~/Documents/RetuloWorkspaces` |
| Skill | `~/.agents/skills/retulo/SKILL.md` |
| 安装快照 | `~/.local/state/retulo/install` |

使用集成期间，请保留源码目录。需要其他存储位置时，使用安装脚本的路径参数。要处理已有代码仓库，可用 `--source-root PATH` 添加那个具体目录；该参数可重复使用。代码仍保留在原仓库中。

## 开始、修改与继续

连接并信任集成后，像平常一样提出请求：

> 请把访客指南写成 Markdown 文件，并保留原始资料。

> 请修复这个项目的合计计算错误，并运行现有测试。

Agent 会在写入前识别或创建任务，使用预留的输出路径，登记已保存文件，并记录相关检查。

要在另一个会话中继续，请提供任务 ID、工作区路径或之前的产物：

> 请在这个工作区继续修改指南，添加联系方式，并保留上一版。

Retulo 提供简短的当前状态与待办摘要，详细记录仍可按需查阅。它不会猜测你指的是哪个无关会话，也不会自行重启已停止的任务。

<a id="demo"></a>
## 运行示例

先从一个小型文件任务开始。这个示例创建访客指南，再通过任务 ID 重新找到同一任务并添加章节。它会检查工作区是否相同、原始资料与第一版输出是否完好保留，以及新版是否记录了新的检查。

```sh
.venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo
```

可以直接查看实际生成的[第一版指南](examples/continuity/visitor-guide-v001.md)、[修改后的指南](examples/continuity/visitor-guide-v002.md)与[运行结果](examples/continuity/result.json)。示例启动独立的 CLI 进程，自行写入预设的虚拟指南文本，不调用模型，也不修改全局设置。它验证的是 CLI 任务续接；该脚本不检查新 Codex 会话是否已采用集成。

接下来，试试包含多个生成步骤的更新：

报告示例使用当前源码的真实文件清单，依次生成 HTML 报告、更新受影响的步骤、保留旧报告、保存工作流模板，再用不同输入创建新的报告任务。

```sh
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
.venv/bin/retulo --config CONFIG_PATH call capabilities --arguments '{}'
.venv/bin/retulo --config CONFIG_PATH get-context --task-id TASK_UUID --json
.venv/bin/retulo --config CONFIG_PATH verify --task-id TASK_UUID --json
```

也可用 `RETULO_CONFIG` 指定配置路径。只有显式运行 `init-config` 或安装操作才会创建配置。JSON 是状态依据，`WORKSPACE.md` 是当前状态的可读视图。请求 ID 让重试保持幂等，修改操作还会检查上次读取的版本号。

Main Brain 是可选的证据与记忆集成。核心功能无需 Main Brain，也不依赖 OmO/OmO Native 等额外 Agent 编排器。

<a id="support"></a>
## 支持范围

| 方面 | 当前范围 |
|---|---|
| 版本 | `0.4.0rc1` 预览版 |
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
- [Agent 与可选记忆集成](docs/INTEGRATIONS.md)
- [任务历史与受管更新](docs/SUSTAINABILITY.md)
- [文件格式与安全契约](docs/FORMAT_AND_SAFETY.md) · [架构](docs/ARCHITECTURE.md)
- [验证记录](docs/QUALIFICATION.md) · [版本说明](docs/RELEASE.md) · [变更记录](CHANGELOG.md)

<a id="get-help"></a>
## 获取帮助与参与贡献

通过仓库的 **Issues** 页面报告可复现的错误、提出功能建议或指出文档问题。请先阅读[常见问题](docs/FAQ.md)，说明版本与环境，并使用包含虚拟数据的小型示例。错误信息中的敏感内容应先遮盖。

敏感安全问题请遵循[安全报告流程](SECURITY.md)。开发环境、相关检查与贡献要求见[贡献指南](CONTRIBUTING.md)。

检查源码仓库：

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

测试使用临时合成数据，无需模型账号。本地检查与 GitHub 托管 CI 的结果会在验证记录中分别说明。

## 许可证

Retulo 使用 [MIT 许可证](LICENSE)。第三方软件及随附许可文件详见[依赖声明](THIRD_PARTY_NOTICES.md)。
