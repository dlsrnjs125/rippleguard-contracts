# RippleGuard Contracts

RippleGuard 서비스와 Agent 사이에서 공유하는 계약을 관리하는 저장소입니다.

## 주요 역할

- 서비스 간 API 명세
- 비동기 이벤트 스키마
- Loan·RippleGuard·Evidence Agent 출력 계약
- 계약 버전 관리와 호환성 규칙

각 서비스는 이 저장소의 계약을 기준으로 요청, 응답, 이벤트를 구현합니다.
