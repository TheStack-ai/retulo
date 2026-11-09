[English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/readme-header-dark.svg">
  <img src="assets/brand/readme-header-light.svg" alt="Retulo — AI 에이전트를 위한 로컬 작업공간." width="800">
</picture>

# Retulo

**AI와 하던 일을, 이어서 완성하세요.**

Retulo는 AI와 진행하는 문서·파일·코드 작업을 정리하는 오픈소스 로컬 도구입니다. 원본 자료와 결과물 버전, 결정 사항, 남은 일을 작업별로 보관해 무엇이 바뀌었는지 확인하고 다음 대화에서도 이어갈 수 있게 돕습니다.

**Codex·Claude Code 또는 CLI에서 사용합니다.** 이어갈 때 **작업 ID나 작업공간 경로**를 알려주면 에이전트가 저장된 상태와 파일을 읽고 작업할 수 있습니다. Retulo는 작업 기록을 내 컴퓨터에 저장하며, MCP로 에이전트와 연결됩니다.

[![미리보기 버전 0.6.0rc1](assets/badges/preview.svg)](https://github.com/TheStack-ai/retulo/releases/tag/v0.6.0rc1)
[![릴리스 검사](https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml)
[![Python 3.11 이상](assets/badges/python.svg)](#support)
[![MIT 라이선스](assets/badges/license.svg)](LICENSE)

**macOS 로컬 미리보기** · CLI·MCP · English, 简体中文, 한국어

[문서 사이트](https://thestack-ai.github.io/retulo/site/ko/) · [이럴 때 사용하세요](#when-to-use) · [설치](#quick-start) · [Codex](#codex) · [Claude Code](#claude-code) · [실행 예제](#demo) · [문서](#documentation) · [도움받기](#get-help)

<a id="when-to-use"></a>
## 이럴 때 사용하세요

### 제안서를 수정하면서 이전 초안도 남기고 싶을 때

> 가격 설명을 수정하고, 이전 버전은 남겨줘.

Retulo는 등록한 결과물 버전을 원본 자료·결정 사항과 함께 보관합니다. 이전 초안을 확인하고, 다음 대화에서도 같은 작업 ID를 지정해 수정을 이어갈 수 있습니다.

### 자료가 바뀌어 정기 보고서를 갱신해야 할 때

> 이번 달 자료로 보고서를 갱신해줘.

입력과 단계를 미리 정의한 작업에서는 바뀐 자료가 어느 단계에 영향을 주는지 찾아줍니다. 에이전트는 이 계획을 보고 필요한 부분을 다시 작업할 수 있습니다. 같은 절차를 반복하려면 레시피로 저장해 새 자료와 새로운 검사로 시작하세요. [작업 흐름과 레시피 보기](docs/WORKFLOWS.md).

### 여러 코드 작업을 별도 폴더에서 진행하고 싶을 때

> 이 기능은 별도 Git worktree에서 작업하고, 변경 내용을 보여줘.

작업에 worktree를 연결하거나 명시적으로 만들고, Git 상태를 확인하며 선택 파일을 스냅샷으로 보존할 수 있습니다. fast-forward 병합과 정리는 각각 따로 준비하고 실행합니다. worktree 생성에는 허용 경로 설정이 필요하며, 실행 중인 프로세스나 데이터베이스까지 분리하지는 않습니다. [Git 작업공간 안내](docs/GIT_WORKSPACES.md).

![원본 자료와 결과 버전, 기록된 진행 상황을 같은 작업으로 연결해 다른 대화에서 이어가는 흐름.](assets/demo/continuity.svg)

*같은 Retulo 설정과 파일 접근 권한을 가진 대화에서 작업 ID나 작업공간 경로를 지정하세요. Retulo는 기록된 작업 상태를 읽으며, 모든 대화를 자동으로 기억하지는 않습니다.*

요청을 해석하고 내용을 만들거나 수정하는 주체는 연결된 에이전트입니다. **Retulo는 작업공간을 정리하고 파일 저장·검사·승인·남은 일을 기록합니다.** 각 상태는 구분되므로 파일이 저장됐다고 내용 검토나 승인이 끝난 것은 아닙니다. 질문이나 계획만 요청하면 작업공간을 만들지 않습니다.

<a id="quick-start"></a>
## Retulo 설치하기

**macOS 또는 Linux** 터미널에서 실행하세요.

```sh
curl -fsSL https://thestack-ai.github.io/retulo/install.sh | sh
retulo --version
```

`retulo 0.6.0rc1`이 나오면 됩니다. 설치기는 릴리스 wheel을 내려받아 `SHA256SUMS`로 검증한 뒤, 계속 사용할 수 있는 uv 도구 환경에 설치합니다. 기존 uv를 사용하거나 전용 uv를 설치하고, 필요하면 Python도 내려받습니다. 설치와 문서 작업에는 Git이 필요 없으며, 소스 폴더나 직접 관리하는 가상환경도 준비하지 않아도 됩니다. 선택 기능인 worktree를 사용하려면 로컬 Git이 필요합니다. 이 프리뷰는 PyPI·npm이 아닌 [GitHub 릴리스](https://github.com/TheStack-ai/retulo/releases/tag/v0.6.0rc1)로 배포합니다.

명령 폴더가 `PATH`에 없다는 안내가 나오면 설치기가 표시한 명령을 실행하세요. 기본 위치라면 다음과 같습니다.

```sh
export PATH="$HOME/.local/bin:$PATH"
```

설치기는 셸 프로필을 수정하지 않습니다. 로컬에서는 macOS의 로컬 파일시스템으로 검증했습니다. 0.6.0rc1은 [macOS·Ubuntu × Python 3.11·3.14 조합의 GitHub CI를 통과했습니다](https://github.com/TheStack-ai/retulo/actions/runs/42574237876). 스크립트 확인, 기존 uv를 이용한 설치, 경로 변경과 개발용 소스 설치는 [상세 설치 안내](docs/INSTALLATION.md)에 있습니다.

에이전트를 연결하려면 **Codex 또는 Claude Code**를 평소처럼 설치하고 로그인해 두세요. Retulo가 두 클라이언트나 모델 계정을 설치해 주지는 않습니다. 아래에서 하나를 골라 `/absolute/path/to/project`를 **이미 있는 프로젝트 폴더의 절대 경로**로 바꾸세요. 기존 코드는 원래 저장소에서 관리합니다.

<a id="codex"></a>
### Codex 연결하기

변경 내용을 미리 본 뒤 같은 옵션으로 적용하세요.

```sh
retulo setup codex --project /absolute/path/to/project
retulo setup codex --project /absolute/path/to/project --apply
retulo doctor codex --project /absolute/path/to/project --connect
```

설정 명령은 Retulo 설정을 만들거나 갱신하고, 선택한 프로젝트를 허용 자료 경로에 추가합니다. 패키지에 포함된 실행 환경으로 사용자 범위 Codex MCP를 연결하고 스킬 1개, 관리하는 `AGENTS.md` 블록, 컨텍스트 훅을 설치합니다. 관련 없는 설정은 보존하고 복구 스냅샷을 남깁니다. 사용할 프로젝트마다 설정 명령을 실행하세요.

Codex를 다시 열고 해당 프로젝트에서 새 대화를 시작하세요. 클라이언트 안내에 따라 설치된 훅을 검토하고 신뢰하도록 설정합니다. Codex CLI의 `codex mcp get retulo`로 실행 경로를, 대화의 `/mcp`로 활성 서버를 확인할 수 있습니다. 에이전트에게 Retulo의 `capabilities`를 호출하게 해 `workspace_home`을 확인한 뒤 [첫 작업](#first-task)을 진행하세요.

<a id="claude-code"></a>
### Claude Code 연결하기

**로컬 Claude Code 클라이언트**와 기존 프로젝트를 사용합니다.

```sh
retulo setup claude --project /absolute/path/to/project
retulo setup claude --project /absolute/path/to/project --apply
retulo doctor claude --project /absolute/path/to/project --connect
```

선택한 프로젝트의 `.mcp.json`에 있는 `retulo` 항목과 `CLAUDE.md`의 Retulo 지침 블록을 관리합니다. 다른 항목과 지침은 보존하고 복구 스냅샷을 남깁니다. 공통 Retulo 설정을 만들거나 갱신하고 해당 프로젝트도 허용합니다. MCP JSON이나 지침을 직접 붙여 넣을 필요는 없습니다. 패키지의 컨텍스트 훅은 Codex용입니다.

해당 프로젝트에서 Claude Code를 시작하고 프로젝트를 신뢰하세요. MCP 서버 승인 안내가 나오면 확인한 뒤 `/mcp`를 살펴보세요. Retulo의 `capabilities` 호출에 이어 [첫 작업](#first-task)을 요청합니다. 연결 대상은 Claude Code이며 claude.ai 웹사이트가 아닙니다.

두 클라이언트 모두 `doctor`로 로컬 설정을 검사합니다. `--connect`를 붙이면 설정된 Retulo 서버를 실행해 실제 MCP 연결과 capabilities 호출도 검사하며, AI·모델은 호출하지 않습니다. 연결 성공과 대화가 파일 작업 절차를 따르는지는 별개이므로 첫 작업으로 확인하세요.

<a id="cli-only"></a>
### CLI 또는 다른 MCP 클라이언트 사용하기

에이전트 설정 명령은 Retulo 설정도 만듭니다. CLI만 사용한다면 한 번 직접 만드세요.

```sh
retulo init-config --workspace-home "$HOME/Documents/RetuloWorkspaces" \
  --allow-source-root /absolute/path/to/project
retulo capabilities --json
```

다른 MCP 클라이언트는 `command -v retulo`로 확인한 실행 파일의 절대 경로에 `--config`, 설정 파일 절대 경로, `mcp`를 인자로 넘겨 **stdio**로 연결합니다. 다른 클라이언트의 연결과 파일 접근은 별도로 확인해야 합니다.

| 로컬 항목 | 기본 위치 |
|---|---|
| Retulo 명령 | `~/.local/bin/retulo` |
| Retulo 설정 | `~/.config/retulo/config.json` |
| 관리 작업공간 | `~/Documents/RetuloWorkspaces` |
| Codex 스킬 | `~/.agents/skills/retulo/SKILL.md` |
| 설치 스냅샷 | `~/.local/state/retulo/install` (Claude: 그 아래 `claude/`) |

기존 설정은 사용하던 작업공간 위치를 유지합니다. setup·doctor·disconnect는 `--config`, `--workspace-home`, `--snapshot-home`을 받으며, Codex에는 `--codex-home`, `--skills-home`도 사용합니다. [경로 변경 안내](docs/INSTALLATION.md#custom-paths)를 참고하세요.

<a id="first-task"></a>
## 첫 작업을 만들고 이어 쓰기

연결한 에이전트에게 요청하세요.

> Retulo로 이 프로젝트의 환영 안내문을 Markdown 파일로 만들어줘. 자료와 결과를 함께 보관하고 내용을 확인해줘. 나중에 이어갈 수 있도록 결과 파일과 작업 ID나 작업공간 경로도 알려줘.

실제 파일, 작업 ID와 작업공간 경로, 등록된 결과물 버전과 검사 결과가 나와야 합니다. 등록 파일의 바이트가 일치하면 Retulo의 `verify`는 `ok: true`와 빈 불일치 목록을 반환합니다. 내용도 직접 확인하세요.

다음으로 수정을 요청합니다.

> 이 안내문에 문의처를 추가해줘. 이전 버전을 보관하고 새 검사 결과도 기록해줘.

**같은 Retulo 설정과 파일 접근 권한을 사용하는** 다른 대화를 열고 실제 작업 ID나 작업공간 경로를 알려주세요.

> WORKSPACE_PATH의 Retulo 작업 TASK_UUID를 이어서 해줘. 기록된 상태부터 읽고 운영 시간 절을 추가하되 이전 버전은 보관해줘.

작업 ID와 작업공간은 유지되고 결과물 버전이 추가되어야 합니다. 이 절차를 확인한 뒤에는 Codex에 설치된 지침이나 Claude 프로젝트 지침이 평소 파일 요청에도 적용할 수 있으므로, 매번 Retulo를 부를 필요는 없습니다. Retulo는 작업 기록을 보관하며 어떤 별개 대화를 뜻했는지 임의로 추측하지 않습니다.

<a id="troubleshooting"></a>
## 설치 중 막힐 때

| 증상 | 확인할 내용 |
|---|---|
| `retulo: command not found` | 설치기가 표시한 `PATH` 명령을 실행하세요. 기본값은 `export PATH="$HOME/.local/bin:$PATH"`입니다. |
| 다운로드·체크섬 검증 실패 | 연결 상태를 확인하고 설치기를 다시 실행하세요. 체크섬 불일치를 무시하지 말고 릴리스 파일명과 오류를 알려주세요. |
| 설정 파일이 없음 | 프로젝트를 지정해 `retulo setup codex` 또는 `retulo setup claude`를 `--apply`로 적용하세요. CLI만 사용하면 `init-config`를 실행합니다. |
| 프로젝트가 허용되지 않음 | 해당 프로젝트의 정확한 경로로 설정을 미리 본 뒤 적용하세요. 기존 허용 경로는 보존합니다. |
| 설정 중 충돌이 보고됨 | 표시된 파일과 옵션을 확인하고 미리보기·적용에 같은 경로를 쓰세요. 사용자가 수정한 내용을 몰래 덮어쓰지 않습니다. |
| MCP가 없거나 연결되지 않음 | `retulo doctor codex --project /absolute/path/to/project --connect` 또는 `claude`로 검사하고, 클라이언트를 다시 열어 서버 승인을 확인하세요. |
| 도구는 있지만 일반 요청에 적용되지 않음 | Codex 훅의 신뢰 설정·스킬, 또는 Claude가 읽은 `CLAUDE.md`를 확인하세요. 첫 작업을 명시적으로 요청하고 실제 기록을 살펴보세요. |
| 에이전트가 작업공간에 쓰지 못함 | 클라이언트의 파일 접근 안내에서 의도한 폴더를 확인하세요. Retulo의 허용 경로가 클라이언트 권한을 대신하지는 않습니다. |
| 프로젝트 경로가 거절됨 | 이미 있는 구체적인 폴더를 선택하세요. 홈 전체, 파일 시스템 루트와 심볼릭 링크 경로는 허용하지 않습니다. |

[상세 설치·진단](docs/INSTALLATION.md), [연동 안내](docs/INTEGRATIONS.md), [미리보기부터 시작하는 복구 안내](docs/RESTORE.md)를 참고하세요.

클라이언트 근거: [OpenAI Docs: Codex MCP](https://developers.openai.com/codex/mcp) · [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Claude 프로젝트 지침](https://code.claude.com/docs/en/memory).

<a id="demo"></a>
## 실행 예제

아래는 선택적으로 준비한 [개발용 소스 폴더](docs/INSTALLATION.md#development-only-source-checkout)에서 실행하는 예제입니다. 일반 설치나 에이전트 연결에 소스 폴더는 필요하지 않습니다.

먼저 작은 파일 작업으로 확인해보세요. 이 예제는 방문자 안내문을 만든 뒤, 작업 ID로 같은 작업을 다시 찾아 내용을 추가합니다. 작업공간이 같고 원본과 첫 결과가 보존되는지, 새 결과에 검사를 새로 기록했는지 확인합니다.

```sh
.venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo
```

실제로 생성한 [첫 안내문](examples/continuity/visitor-guide-v001.md), [수정한 안내문](examples/continuity/visitor-guide-v002.md), [실행 결과](examples/continuity/result.json)를 읽어볼 수 있습니다. 예제는 각각 별도의 CLI 프로세스를 실행하고, 미리 정한 가상 안내문을 직접 씁니다. 모델을 호출하거나 전역 설정을 바꾸지 않습니다. CLI에서 같은 작업을 이어가는지 확인하는 예제이며, 새 Codex 대화가 연동을 실제로 채택했는지는 이 스크립트로 검사하지 않습니다.

다음으로 여러 생성 단계의 갱신을 확인해보세요.

보고서 예제는 현재 소스의 실제 파일 목록을 사용합니다. HTML 보고서를 생성한 뒤 영향받는 단계를 갱신하고, 이전 보고서를 보존합니다. 이어서 레시피를 저장하고 다른 입력으로 새 보고서 작업을 시작합니다.

```sh
.venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo
```

각 예제에 **새로운 빈 출력 폴더**를 지정하세요. 보고서 예제는 CLI와 생성 도구를 각각 별도 프로세스로 실행합니다. 모델 계정이나 네트워크 서비스가 필요 없고, 전역 설치 설정도 바꾸지 않습니다. JSON 결과에서 생성된 보고서 경로와 다시 실행하거나 재사용한 단계를 확인할 수 있습니다.

![명시한 입력과 생성 단계, 출력 버전이 연결되고 영향받은 단계만 다시 실행되며 독립된 단계는 재사용되는 흐름.](assets/demo/workflow.svg)

검증한 예제에서는 입력을 바꾼 뒤 **영향받은 다섯 단계를 다시 실행하고 독립된 한 단계를 재사용**했습니다. 해당 예제에서 관찰한 결과이며, 모든 작업의 비용이나 실행량을 보장하는 수치는 아닙니다. [검증 기록](docs/QUALIFICATION.md)을 참고하세요.

## 바뀐 자료 반영하기와 같은 작업 반복하기

> 데이터가 바뀌었어. 보고서를 갱신하고 이전 버전은 보관해줘.

단계를 명시한 작업에서는 입력 자료, 생성 단계, 정확한 출력 버전을 서로 연결합니다. Retulo는 다시 작업할 단계를 식별하고 고정된 입력 스냅샷과 저장 경로를 제공합니다. 실제 생성 도구는 현재 에이전트가 실행하며, 갱신 중에도 이전에 완료한 결과를 보존합니다.

> 같은 절차로 이번 달 자료도 처리해줘.

작업 레시피는 작업 절차와 입력값의 타입, 완료 조건을 특정 버전으로 고정합니다. 새 자료를 넣으면 **새 작업과 새로운 검사**로 시작하며, 이전 작업의 검사 통과나 승인을 물려받지 않습니다. Retulo가 레시피 안의 명령을 직접 실행하지도 않습니다. 지원하는 파일·단계 단위와 재사용 규칙은 [작업과 레시피 계약](docs/WORKFLOWS.md)에 있습니다.

## CLI·MCP

```sh
retulo --config "$HOME/.config/retulo/config.json" call capabilities --arguments '{}'
retulo --config "$HOME/.config/retulo/config.json" get-context --task-id TASK_UUID --json
retulo --config "$HOME/.config/retulo/config.json" verify --task-id TASK_UUID --json
```

`RETULO_CONFIG`로 설정 경로를 지정할 수도 있습니다. 설정은 명시적인 `init-config` 또는 설치 작업에서만 생성됩니다. JSON이 상태의 기준이고, `WORKSPACE.md`는 현재 상태를 읽기 쉽게 보여주는 안내서입니다. 요청 ID로 같은 요청의 재시도를 구분하고, 변경 시 마지막으로 읽은 revision을 확인합니다.

Retulo는 작업과 파일 기록을 로컬에 보관합니다. 기존 에이전트나 CLI 클라이언트로 코어를 사용하며 별도 서비스는 필요하지 않습니다.

<a id="support"></a>
## 지원 범위

| 항목 | 현재 범위 |
|---|---|
| 버전 | `0.6.0rc1` 미리보기 |
| 실행 환경 | Python 3.11 이상; 설치기가 필요할 때 Python을 준비 |
| 로컬 검증 | macOS, 로컬 파일시스템 |
| 릴리스 CI | 0.6.0rc1의 macOS·Ubuntu × Python 3.11·3.14 조합 통과 |
| 인터페이스 | CLI·MCP, 패키지에 포함된 Codex·Claude Code 연결·진단·해제 |
| 그 밖의 환경 | Windows 수동 Python 설치·NAS·여러 호스트·다른 클라이언트는 별도 검증 필요 |

Retulo는 절차를 통해 등록한 파일과 작업을 관리합니다. 다른 호스트 도구가 수행하는 모든 쓰기 작업을 가로채지는 않습니다. 파일 무결성 확인은 내용 품질이나 사용자 승인을 뜻하지 않습니다. 코어는 모델 호출, 에이전트 실행, 폴더 감시, 외부 게시를 수행하지 않습니다.

## 업데이트 또는 연결 해제

설치 명령을 다시 실행하면 이 릴리스를 설치합니다. 실행 환경이나 연동 버전이 바뀌면 같은 프로젝트·경로 옵션으로 설정을 다시 미리 보고 적용한 뒤 클라이언트를 다시 여세요.

연결을 해제할 때도 먼저 미리 보고 적용합니다.

```sh
retulo disconnect codex --project /absolute/path/to/project
retulo disconnect codex --project /absolute/path/to/project --apply
```

Claude Code는 `codex` 대신 `claude`를 사용하세요. 연결 해제는 작업공간·결과물·핵심 Retulo 설정을 보존합니다. 복구 스냅샷은 보관하세요. [설치·해제](docs/INSTALLATION.md#disconnect-and-update)와 [복구 안내](docs/RESTORE.md)에 상세 절차가 있습니다.

공개 버전은 초기 개발용 프로토타입과 이름·경로·MCP 키가 다릅니다. 프로토타입 설치를 자동으로 이전하지 않으므로 함께 설치하기 전 자동 파일 작업 지침이 겹치는지 확인하세요.

<a id="documentation"></a>
## 문서

- [설치·연결·진단](docs/INSTALLATION.md)
- [로컬 문서 사이트](docs/SITE.md)
- [자주 묻는 질문](docs/FAQ.md)
- [한국어 사용 안내](docs/USER_GUIDE.ko.md)
- [작업과 레시피](docs/WORKFLOWS.md)
- [Git 작업공간·스냅샷·병합](docs/GIT_WORKSPACES.md)
- [에이전트 연동](docs/INTEGRATIONS.md)
- [작업 기록과 관리 기본본 갱신](docs/SUSTAINABILITY.md)
- [파일 형식과 안전 계약](docs/FORMAT_AND_SAFETY.md) · [구조](docs/ARCHITECTURE.md)
- [검증 기록](docs/QUALIFICATION.md) · [릴리스 안내](docs/RELEASE.md) · [변경 기록](CHANGELOG.md)

<a id="get-help"></a>
## 도움받기와 기여

재현 가능한 오류, 기능 제안, 문서 문제는 저장소의 **Issues** 탭에서 알려주세요. 먼저 [자주 묻는 질문](docs/FAQ.md)을 읽고, 버전·환경과 가상 자료를 사용한 작은 재현 예제를 적어주세요. 오류 내용의 민감정보는 가린 뒤 공유합니다.

민감한 보안 문제는 [보안 신고 안내](SECURITY.md)를 따르세요. 개발 환경과 관련 검사, 기여 기준은 [기여 안내](CONTRIBUTING.md)에 있습니다.

선택적인 [개발용 소스 환경](docs/INSTALLATION.md#development-only-source-checkout)을 준비한 폴더에서 검사합니다.

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

테스트는 임시 합성 자료를 사용하며 모델 계정이 필요하지 않습니다. 로컬 검사와 GitHub에서 실행한 CI 결과는 검증 기록에서 구분합니다.

## 라이선스

Retulo는 [MIT 라이선스](LICENSE)를 사용합니다. 제3자 소프트웨어와 포함된 라이선스 파일은 [의존성 고지](THIRD_PARTY_NOTICES.md)에 정리되어 있습니다.
