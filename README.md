# RippleGuard Contracts

RippleGuard 서비스와 Agent 사이의 실행 가능한 JSON Schema 계약 원본입니다. Phase 1의 Loan, Governance, Agent Runtime 구현은 이 저장소의 버전된 계약을 기준으로 통합합니다.

## 구조

- `schemas/common`: 공통 Event Envelope
- `schemas/events`: Phase 1 비동기 Event
- `schemas/external-risk-signal`: FDS 위험신호 입력
- `schemas/domain`: 상태와 Evaluation Run
- `schemas/agent-output`: Decision Envelope와 최소 Assurance 결과
- `examples/valid`, `examples/invalid`: 성공·실패 검증 fixture
- `docs`: 범위와 버전·호환성 정책

## 검증

Python 3와 `jsonschema` 패키지가 필요합니다.

```bash
python -m pip install -r requirements-dev.txt
make validate
```

검증 명령은 JSON 문법, Schema 자체, `$ref`, 중복 `$id`, valid fixture의 성공과 invalid fixture의 예상 실패를 확인합니다.

## 버전 원칙

Event 이름(`eventType`)과 Schema 버전(`schemaVersion`)은 별도 필드입니다. 호환되지 않는 변경은 기존 파일을 수정하지 않고 새로운 major 버전 Schema로 추가합니다. 자세한 기준은 [versioning-policy.md](docs/versioning-policy.md)와 [compatibility-policy.md](docs/compatibility-policy.md)를 따릅니다.

## Phase 0 범위

Phase 1 Core MSA에 필요한 Event, 상태, External Risk Signal, Evaluation Run, 최소 Decision/Assurance 계약만 포함합니다. Consequence, Evidence & Control, OPA Policy Input의 최종 계약과 언어별 DTO는 포함하지 않습니다. 자세한 경계는 [phase-0-contract-scope.md](docs/phase-0-contract-scope.md)에 있습니다.

## Producer와 Consumer 사용법

Producer는 해당 Schema에 맞는 JSON을 발행하고 발행 시 사용한 `schemaVersion`을 보존합니다. Consumer는 처리 전에 정확한 Event 버전 Schema로 검증하고, `eventId`로 멱등 처리하며, `correlationId`와 `causationId`로 흐름을 연결합니다. Consumer가 지원하지 않는 major 버전은 추측해 처리하지 않고 격리하거나 실패로 기록합니다.
