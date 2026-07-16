# Phase 1 Core MSA Contract Scope

## Included contracts

Phase 1 uses OpenAPI and JSON Schema as its language-neutral source of truth. The Loan API accepts only synthetic or masked applicant and evidence references. The Audit API exposes a minimal ordered Case Timeline; it is a read model rather than a replay or integrity proof.

| Contract | Producer | Consumer | Version |
| --- | --- | --- | --- |
| Loan Application create/get OpenAPI | Loan Service | Web and integration clients | API/Schema `1.0.0` |
| Case Timeline OpenAPI | Audit & Replay Service | Web and operators | API/Schema `1.0.0` |
| Loan/Decision Case status | Loan/Governance Service | Event and API consumers | `1.0.0` reused |
| Evaluation Run | Governance Service | Governance, Agent Runtime, Audit | `1.1.0`; `completedAt` is an optional compatible extension |
| Eight Phase 1 events | Loan/Governance Service; mock completion is published by Governance | Loan, Governance, Agent Runtime, Audit | existing Event major `v1` reused |
| Mock Evaluation Result | deterministic Governance mock evaluator | Governance verification | `1.0.0` |
| Mock Assurance Result | deterministic Governance assurance step | Governance command routing | `1.0.0` |
| Loan Decision Command | Governance Service | Loan Service | `1.0.0` |

The Event envelope carries identity, time, producer, correlation, causation, and payload. `applicationId` is null only when no Loan Application exists, and `evaluationRunId` is null for events before an Evaluation Run exists. They remain optional in the shared legacy envelope so already-published Phase 0 `v1` payloads continue to validate; Phase 1 producers should emit both fields explicitly. The domain-specific event payload remains authoritative when validating legacy messages.

Evidence supplementation is a supported Phase 1 route. `governance.evidence.requested.v1` and `loan.evidence.updated.v1` remain executable and the evidence-reassessment scenario is mandatory validation coverage. A direct approval or rejection happy path does not manufacture an evidence request when no evidence is missing.

## Mock limitations

`MOCK_*` proposals are deterministic test outputs, not credit decisions. Governance must verify the proposal and assurance result before issuing a Loan Decision Command. Mock rule and input Snapshot versions make the result reproducible but do not represent a model, LLM, real Agent output, or policy evaluation. `REJECT` is a lending outcome; it is not the same as a Governance `BLOCKED` status, which prevents the current run from producing a decision.

The standalone command enum reserves `CONDITIONAL_APPROVE` and `REQUEST_MORE_EVIDENCE`. The Phase 1 direct-finalization event baseline continues to execute `APPROVE` and `REJECT`; executable condition details are deferred, while evidence supplementation uses the dedicated evidence events.

## Compatibility

Phase 0 Schema files and Event major names are retained. New independent REST, Mock, Command, and Timeline contracts begin at `1.0.0`. The Evaluation Run adds `completedAt` in `1.1.0` without changing the meaning or requirements of `1.0.0`. The shared Event envelope adds optional nullable identifiers only, so existing payloads remain valid. No breaking change is introduced.

## Deferred

- Actual Loan Agent output and production Agent provenance
- Consequence Envelope and final Evidence Agent contract
- Full Assurance Profile and OPA Policy Input (Phase 5)
- Executable conditional-loan terms and agreement versioning
- Replay API, execution/architecture Graph DTO, and hash-chain contract
- Java or Python common DTO libraries
