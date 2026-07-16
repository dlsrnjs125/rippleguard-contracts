# RippleGuard Contracts

RippleGuard 서비스와 Agent 사이의 실행 가능한 JSON Schema 계약 원본입니다. Phase 1의 Loan, Governance, Agent Runtime 구현은 이 저장소의 버전된 계약을 기준으로 통합합니다.

## 구조

- `schemas/common`: 공통 Event Envelope
- `schemas/events`: Full SemVer 파일로 분리된 Phase 1 비동기 Event
- `schemas/external-risk-signal`: Full SemVer FDS 위험신호 입력
- `schemas/domain`: 상태와 Full SemVer Evaluation Run
- `schemas/agent-output`: Full SemVer Decision Envelope와 최소 Assurance 결과
- `schemas/rest`, `openapi`: Loan Application과 최소 Case Timeline REST 계약
- `schemas/agent-output`, `schemas/commands`: Mock이 재사용하는 Decision Envelope와 단일 Loan Decision Command payload
- `examples/valid`, `examples/invalid`: 성공·실패 검증 fixture
- `examples/scenarios/valid`, `examples/scenarios/invalid`: 경로별 causation과 cross-reference를 검증하는 격리된 Scenario context
- `docs`: 범위와 버전·호환성 정책

## 검증

Python 3와 개발 의존성이 필요합니다.

```bash
python -m pip install -r requirements-dev.txt
make validate
```

검증 명령은 공식 OpenAPI 3.1 문법, JSON 문법, JSON Schema, 상대 `$ref`, 중복 `$id`, Phase 1 Event Envelope Profile, 파일명·버전, Timeline 정렬·인과관계, Scenario cross-reference, cross-field 의미 제약, minor 호환성, valid fixture 성공과 invalid fixture·Scenario 실패를 확인합니다.

## 버전 원칙

Event 이름(`eventType`)과 Schema 버전(`schemaVersion`)은 별도 필드입니다. 호환되지 않는 변경은 기존 파일을 수정하지 않고 새로운 major 버전 Schema로 추가합니다. 자세한 기준은 [versioning-policy.md](docs/versioning-policy.md)와 [compatibility-policy.md](docs/compatibility-policy.md)를 따릅니다.

## Phase 범위

Phase 0 Event·상태 기반을 보존하면서 Phase 1 Loan REST, Mock Evaluation·Assurance, Decision Command와 최소 Timeline을 추가합니다. 실제 Agent, OPA Policy Input, Replay·Graph DTO와 언어별 DTO는 포함하지 않습니다. 자세한 경계는 [phase-1-contract-scope.md](docs/phase-1-contract-scope.md)에 있습니다.

## Producer와 Consumer 사용법

Producer는 해당 Schema에 맞는 JSON을 발행하고 발행 시 사용한 `schemaVersion`을 보존합니다. Consumer는 처리 전에 정확한 Event 버전 Schema로 검증하고, `eventId`로 멱등 처리하며, `correlationId`와 `causationId`로 흐름을 연결합니다. Consumer가 지원하지 않는 major 버전은 추측해 처리하지 않고 격리하거나 실패로 기록합니다.
