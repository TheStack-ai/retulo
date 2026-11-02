# 하네스 구조와 토큰 비용: 외부 자료 검토

확인일: 2026-11-02. 공식 원문 5개를 검토했다. 아래 자료는 설계 근거이며 이 프로젝트의 성능을 대신 증명하지 않는다. 새 프레임워크나 별도 조정용 모델은 추가하지 않았다.

| 자료 | 확인한 내용 | 이번 적용 |
|---|---|---|
| [Anthropic, Advanced tool use](https://www.anthropic.com/research/advanced-tool-use), 2025-11-24 | 도구 정의도 컨텍스트를 소비한다. 필요한 정의만 늦게 읽는 방법은 검색 단계와 지연도 추가한다. | 기존 호스트의 도구 지연 로딩을 이용한다. 도구 응답과 스키마 크기를 따로 제한하며 자체 도구 검색 계층은 만들지 않는다. |
| [OpenAI, Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), 게시일 미표시 | 동일한 입력 접두부의 재사용과 실제 입력량 감소는 별개다. 도구 정의·순서·설정 변경은 캐시 재사용에 영향을 줄 수 있다. | 안내는 짧고 안정적으로 유지한다. 실제 호스트 사용량을 확인하지 않은 캐시 절감률, 예열 호출, 모델 변경은 도입하지 않는다. |
| [AWS Builders’ Library, Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/), 공식 PDF ©2020 | 요청 ID와 인수를 연결하고, 같은 ID의 다른 인수를 거절하며 변경과 요청 기록을 원자적으로 다룬다. | 기존 operation receipt·revision·재전송 검사와 일치한다. 별도 workflow 실행기를 추가하지 않는다. 분산 API 사례가 로컬 파일 원자성을 증명하는 것은 아니므로 기존 장애 테스트를 유지한다. |
| [Anthropic, Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents), 2025-11-26 | 대화 압축만으로 재개 문제를 해결할 수 없으며, 진행 기록과 구체적 요구사항·실제 검증이 필요하다. | JSON 상태·현재 안내서·버전별 검증을 유지한다. 웹앱 사례를 모든 문서 작업의 브라우저 검사 의무로 확대하지 않는다. |
| [Anthropic, Building effective agents](https://www.anthropic.com/research/building-effective-agents), 2024-12-19 | 단순하게 시작하고 측정 가능한 이득이 있을 때 복잡성을 늘린다. 병렬화는 독립 작업과 여러 관점이 필요한 경우에 적합하다. | Codex가 직접 판단하고 결정론적 엔진이 상태를 관리한다. 작은 작업의 별도 라우터·상시 coordinator·중복 검토 모델을 추가하지 않는다. |

## 반영한 판단

1. **유지:** 짧은 기본 조회, 필요한 기록만 읽기, JSON 단일 기준, 멱등 영수증, 검증·사용자 승인 구분.
2. **보완:** 이벤트별 훅 출력 상한과 MCP 스키마 총량 검사를 분리했다. 반복 공통 안내를 줄였고 실제 사람 검토가 없는 작업의 불필요한 승인 대기를 수정했다.
3. **보류:** 자체 도구 검색, 캐시 예열, 별도 조정용 모델, 자동 실험·감시 서비스. 실제 필요와 이득이 확인되기 전에는 추가하지 않는다.

자료에 나온 Claude 도구 사례의 절감률이나 특정 웹앱 모델의 결과를 이 시스템의 절감률·성공률로 사용하지 않았다. 이번 측정은 실제 실행 결과, 파일 해시, 직렬화된 바이트 수를 각각 구분한다.
