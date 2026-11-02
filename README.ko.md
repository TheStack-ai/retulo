[English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/readme-header-dark.svg">
  <img src="assets/brand/readme-header-light.svg" alt="Retulo — AI 에이전트를 위한 로컬 작업공간." width="800">
</picture>

# Retulo

**AI 에이전트를 위한 로컬 작업공간.**

Retulo는 AI 에이전트의 파일 작업에 독립된 로컬 작업공간을 제공합니다. 원본 자료와 이전 결과 버전, 결정 사항, 남은 작업, 검증·승인 기록을 한곳에 보관해 에이전트가 진행 상황을 파악하도록 돕습니다. Codex 연동을 설정하고 신뢰하면, 매번 Retulo를 지정하지 않아도 에이전트가 일반 파일 요청에 이 절차를 적용할 수 있습니다. 다른 대화에서 이어갈 때는 같은 작업이나 작업공간을 지정해 기록된 상태에서 계속 진행합니다.

[![미리보기 버전 0.4.0rc1](assets/badges/preview.svg)](https://github.com/TheStack-ai/retulo/releases/tag/v0.4.0rc1)
[![릴리스 검사](https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml)
[![Python 3.11 이상](assets/badges/python.svg)](#support)
[![MIT 라이선스](assets/badges/license.svg)](LICENSE)

**macOS 로컬 미리보기** · CLI·MCP · English, 简体中文, 한국어

[문서 사이트](https://thestack-ai.github.io/retulo/site/) · [빠른 시작](#quick-start) · [실행 예제](#demo) · [문서](#documentation) · [도움받기](#get-help)

![원본 자료와 결과 버전, 기록된 진행 상황을 보존하며 같은 작업을 다른 대화에서 이어가는 흐름.](assets/demo/continuity.svg)

*이 그림은 작업 흐름을 설명합니다. 에이전트가 기존 작업을 확인한 뒤 이어가며, Retulo가 모든 대화를 자동으로 기억한다는 뜻은 아닙니다.*

## 이렇게 쓸 수 있습니다

| 하려는 일 | Retulo가 제공하는 것 |
|---|---|
| 평소처럼 파일 작업 요청하기 | 매번 Retulo를 부르지 않아도 연결된 에이전트가 사용할 작업공간 도구와 지침 |
| 다른 대화에서 이어 하기 | 같은 작업 ID와 작업공간, 현재 상태와 상세 기록 |
| 기존 결과 수정하기 | 미리 정한 저장 경로, 번호가 붙은 결과 버전, 파일 변경 감지 |
| 어디까지 끝났는지 확인하기 | 파일 저장·내용 검사·사용자 승인·외부 전달을 구분한 기록 |
| 여러 단계의 작업 갱신하기 | 명시한 입력이 바뀌었을 때 다시 작업할 단계 식별 |
| 다른 자료로 같은 절차 반복하기 | 새 작업과 새로운 검사로 시작하는 버전별 작업 레시피 |

요청을 해석하고 도구를 실행하는 주체는 에이전트입니다. Retulo는 작업공간과 기록을 관리합니다. 질문이나 계획만 요청하면 작업공간을 만들지 않습니다.

<a id="quick-start"></a>
## 빠른 시작

macOS에서 **Python 3.11 이상**을 사용하세요. 소스 압축을 풀거나 소스 저장소를 연 뒤, 프로젝트 폴더에서 실행합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/retulo --help
```

마지막 명령에서 Retulo의 명령 도움말이 나오면 됩니다. 이 미리보기는 소스 설치를 사용합니다. wheel에는 핵심 CLI/MCP가 들어 있고, Codex 설치 스크립트는 소스에 포함되어 있습니다.

먼저 Codex 연동의 변경 내용을 미리 보고, 경로와 작업을 확인한 뒤 적용합니다.

```sh
.venv/bin/python scripts/install_codex.py
.venv/bin/python scripts/install_codex.py --apply
```

설치기는 `retulo` MCP 서버, 스킬 1개, 관리 지침 블록 1개, 컨텍스트 훅 3개를 추가합니다. 관련 없는 기존 설정은 보존하며 사용자가 수정한 내용과 충돌하면 이를 보고합니다. Codex에서 새 훅 정의를 검토하고 신뢰하도록 설정한 뒤 연동을 연결하세요. 설치기가 기존 MCP 연결을 자동으로 다시 시작하지는 않으므로, 설치 완료만으로 클라이언트가 새 연동을 적용했다고 간주할 수는 없습니다.

| 항목 | 기본 위치 |
|---|---|
| 설정 | `~/.config/retulo/config.json` |
| 작업공간 | `~/Documents/RetuloWorkspaces` |
| 스킬 | `~/.agents/skills/retulo/SKILL.md` |
| 설치 스냅샷 | `~/.local/state/retulo/install` |

연동을 사용하는 동안 소스 폴더를 그대로 보관하세요. 다른 위치는 설치기의 경로 옵션으로 지정합니다. 기존 개발 저장소에서 작업하려면 `--source-root PATH`로 해당 폴더만 추가할 수 있습니다. 이 옵션은 여러 번 사용할 수 있으며, 코드는 원래 저장소에 그대로 둡니다.

## 시작하고 수정하고 이어가기

연동을 연결하고 신뢰하도록 설정했다면 평소처럼 요청합니다.

> 방문자 안내문을 Markdown 파일로 작성해줘. 원본 자료는 보존해줘.

> 이 프로젝트의 합계 계산 오류를 고치고 기존 테스트로 확인해줘.

에이전트가 파일을 쓰기 전에 기존 작업을 확인하거나 새 작업을 시작합니다. 정해진 출력 경로에 파일을 저장하고, 실제 저장된 파일과 관련 검사 결과를 기록합니다.

다른 대화에서 이어가려면 작업 ID, 작업공간 경로 또는 이전 결과물을 알려주세요.

> 이 작업공간의 안내문을 이어서 수정해줘. 문의처를 추가하고 이전 버전도 보관해줘.

Retulo는 현재 상태와 남은 일을 짧게 보여주고, 상세 기록은 필요할 때 다시 읽을 수 있게 보존합니다. 관련 없는 대화를 임의로 추측하거나 멈춘 작업을 저절로 다시 시작하지 않습니다.

<a id="demo"></a>
## 실행 예제

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
.venv/bin/retulo --config CONFIG_PATH call capabilities --arguments '{}'
.venv/bin/retulo --config CONFIG_PATH get-context --task-id TASK_UUID --json
.venv/bin/retulo --config CONFIG_PATH verify --task-id TASK_UUID --json
```

`RETULO_CONFIG`로 설정 경로를 지정할 수도 있습니다. 설정은 명시적인 `init-config` 또는 설치 작업에서만 생성됩니다. JSON이 상태의 기준이고, `WORKSPACE.md`는 현재 상태를 읽기 쉽게 보여주는 안내서입니다. 요청 ID로 같은 요청의 재시도를 구분하고, 변경 시 마지막으로 읽은 revision을 확인합니다.

Main Brain은 선택적으로 연결하는 근거·기억 시스템입니다. 핵심 기능에 Main Brain이나 OmO·OmO Native 같은 별도 에이전트 실행 관리 도구가 필요하지 않습니다.

<a id="support"></a>
## 지원 범위

| 항목 | 현재 범위 |
|---|---|
| 버전 | `0.4.0rc1` 미리보기 |
| 실행 환경 | Python 3.11 이상. Python 3.11·3.14에서 소스와 정상 설치한 wheel 검사 |
| 검증 환경 | macOS, 로컬 파일시스템 |
| 인터페이스 | CLI·MCP, 소스에 포함된 Codex 연동 |
| 그 밖의 환경 | Linux·Windows·NAS·여러 호스트·다른 에이전트 클라이언트는 별도 검증 필요 |

Retulo는 절차를 통해 등록한 파일과 작업을 관리합니다. 다른 호스트 도구가 수행하는 모든 쓰기 작업을 가로채지는 않습니다. 파일 무결성 확인은 내용 품질이나 사용자 승인을 뜻하지 않습니다. 코어는 모델 호출, 에이전트 실행, 폴더 감시, 외부 게시를 수행하지 않습니다.

## 업데이트와 제거

설치기가 반환한 설치 스냅샷을 보관하세요. 업데이트는 사용자가 바꾸지 않은 관리 기본본을 갱신하고, 사용자가 수정한 파일은 보존합니다. [복구 안내](docs/RESTORE.md)에 작업공간과 결과물을 지우지 않고 설치 단계 하나를 되돌리는 방법이 있습니다.

공개본의 이름·경로·MCP 키는 초기 개발 원형과 다릅니다. 기존 개발본 설치는 자동 이전되지 않습니다. 두 버전을 같은 환경에 설치하기 전에는 자동 파일 작업 지침이 겹치는지 확인하세요.

<a id="documentation"></a>
## 문서

- [로컬 문서 사이트](docs/SITE.md)
- [자주 묻는 질문](docs/FAQ.md)
- [한국어 사용 안내](docs/USER_GUIDE.ko.md)
- [작업과 레시피](docs/WORKFLOWS.md)
- [에이전트·선택적 기억 시스템 연동](docs/INTEGRATIONS.md)
- [작업 기록과 관리 기본본 갱신](docs/SUSTAINABILITY.md)
- [파일 형식과 안전 계약](docs/FORMAT_AND_SAFETY.md) · [구조](docs/ARCHITECTURE.md)
- [검증 기록](docs/QUALIFICATION.md) · [릴리스 안내](docs/RELEASE.md) · [변경 기록](CHANGELOG.md)

<a id="get-help"></a>
## 도움받기와 기여

재현 가능한 오류, 기능 제안, 문서 문제는 저장소의 **Issues** 탭에서 알려주세요. 먼저 [자주 묻는 질문](docs/FAQ.md)을 읽고, 버전·환경과 가상 자료를 사용한 작은 재현 예제를 적어주세요. 오류 내용의 민감정보는 가린 뒤 공유합니다.

민감한 보안 문제는 [보안 신고 안내](SECURITY.md)를 따르세요. 개발 환경과 관련 검사, 기여 기준은 [기여 안내](CONTRIBUTING.md)에 있습니다.

소스 저장소를 검사하려면 다음을 실행합니다.

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

테스트는 임시 합성 자료를 사용하며 모델 계정이 필요하지 않습니다. 로컬 검사와 GitHub에서 실행한 CI 결과는 검증 기록에서 구분합니다.

## 라이선스

Retulo는 [MIT 라이선스](LICENSE)를 사용합니다. 제3자 소프트웨어와 포함된 라이선스 파일은 [의존성 고지](THIRD_PARTY_NOTICES.md)에 정리되어 있습니다.
