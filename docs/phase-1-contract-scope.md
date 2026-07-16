# Phase 1 Core MSA Contract Scope

## Included contracts

Phase 1 uses OpenAPI and JSON Schema as its language-neutral source of truth. The Loan API accepts only synthetic or masked applicant and evidence references. The Audit API exposes a minimal ordered Case Timeline; it is a read model rather than a replay or integrity proof.

| Contract | Producer | Consumer | Version |
| --- | --- | --- | --- |
| Loan Application create/get OpenAPI | Loan Service | Web and integration clients | API/Schema `1.0.0` |
| Case Timeline OpenAPI | Audit & Replay Service | Web and operators | API/Schema `1.0.0` |
| Loan/Decision Case status | Loan/Governance Service | Event and API consumers | `1.0.0` reused |
| Evaluation Run | Governance Service | Governance, Agent Runtime, Audit | legacy `1.0.0`; lifecycle-enforced `2.0.0` |
| Eight Phase 1 events | Loan/Governance Service; mock completion is published by Governance | Loan, Governance, Agent Runtime, Audit | Event major `v1`; Phase 1 Schema minor `1.1.0` |
| Decision Envelope (Mock reused) | deterministic Governance mock evaluator | `agent.evaluation.completed.v1`, Governance | `1.0.0` reused |
| Assurance Result (Mock reused) | deterministic Governance assurance step | Governance command routing | `1.0.0` reused |
| Loan Decision Command payload + Phase 1 Profile | Governance Service | `loan.decision.commanded.v1`, Loan Service | `1.0.0` single source |

The base Event envelope and Event Schema `1.0.0` files remain compatible with Phase 0 and still accept legacy human-readable application identifiers. Phase 1 producers and consumers use Event Schema `1.1.0` files. Each `1.1.0` Event directly references `phase-1-event-envelope-profile.v1.0.0.schema.json`, which requires explicit `applicationId` and `evaluationRunId` presence. The profile constrains `applicationId` to UUID or null, and run-scoped Events require both `applicationId` and `evaluationRunId` as UUIDs. This keeps REST, Event, Command, and Timeline identifiers aligned for Phase 1 without changing already-published `1.0.0` contracts.

Evidence supplementation is a supported Phase 1 route. `governance.evidence.requested.v1` and `loan.evidence.updated.v1` remain executable and the evidence-reassessment scenario is mandatory validation coverage. A direct approval or rejection happy path does not manufacture an evidence request when no evidence is missing.

## Mock limitations

The deterministic mock evaluator emits the existing Decision Envelope with `evaluatorId: mock-evaluator`, `PROPOSE_*`, and deterministic generator versions. `agent.evaluation.completed.v1` carries that Envelope. Governance evaluates the existing Assurance Result and only then creates the shared Loan Decision Command payload. Scenarios validate the complete Decision-to-Command references and proposal mapping; no parallel Mock result model or implementation-defined adapter exists.

The Command payload allows only `APPROVE` and `REJECT`. Evidence supplementation exclusively uses `governance.evidence.requested.v1`; conditional approval is deferred until an executable condition contract exists. `loan.decision.commanded.v1.1.0.schema.json` references `phase-1-loan-decision-command-profile.v1.0.0.schema.json` directly, so Event Schema validation requires `reasonCodes`, `issuedAt`, and the business `idempotencyKey` without relying on a hidden secondary validator rule. Loan Service also uses Event `eventId` for transport-delivery deduplication. `REJECT` is a lending outcome, not a Governance `BLOCKED` status.

The minimal Case Timeline uses `traceCompleteness` values `COMPLETE`, `PARTIAL`, and `UNKNOWN`. When a trace is incomplete or contains an invalid reference, `warnings` uses the Phase 1 reason codes `EVENT_GAP_DETECTED`, `RETENTION_LIMIT`, `LATE_EVENT_PENDING`, and `INVALID_REFERENCE`. Phase 1 correlation is intentionally application-scoped: every Timeline event uses `correlationId == applicationId`; sub-execution identity is represented by `evaluationRunId` and `causationId`. Changing that correlation policy requires a new Event major or ADR.

## Compatibility

Phase 0 Event major names and accepted `1.0.0` payloads are retained. Phase 1 Event Schema `1.1.0` files directly include the immutable Phase 1 Envelope Profile. The `loan.decision.commanded.v1.0.0` Event still references the base Command Schema; only `loan.decision.commanded.v1.1.0` references the Phase 1 Command Profile. REST, Command, Timeline, and profiles begin at `1.0.0`. Exact Decimal String money replaces binary floating-point amounts before service implementation. Completion-time lifecycle rules intentionally use Evaluation Run `2.0.0` because enforcing them would break `1.x` compatibility.

## Deferred

- Actual Loan Agent output and production Agent provenance
- Consequence Envelope and final Evidence Agent contract
- Full Assurance Profile and OPA Policy Input (Phase 5)
- Executable conditional-loan terms and agreement versioning
- Replay API, execution/architecture Graph DTO, and hash-chain contract
- Java or Python common DTO libraries
