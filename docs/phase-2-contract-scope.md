# Phase 2 Contract Scope

Phase 2 defines executable contracts for the Loan Decision Agent before producer and consumer implementation.

## Scope

- Versioned Feature Schema and materialized Feature Payload
- immutable Snapshot Reference
- Loan Proposal
- Phase 2 Decision Envelope
- Tabular Model Manifest
- Agent Run Metadata
- Loan Decision Agent Request
- Loan Decision Agent Result
- Governance Agent Result validated/rejected audit event
- Failure classification and reason code mapping
- valid, invalid and scenario fixtures

## Exclusions

- Local LLM, Ollama, Prompt Template, Prompt Version and `StructuredLlmPort`
- RippleGuard Consequence Agent and Evidence Agent contracts
- OPA policy input
- final Loan approval/rejection command
- interest-rate optimization
- Phase 7 Replay, Hash Chain and Graph DTOs
- Chain-of-Thought or internal reasoning text

## Event Flow

Phase 2 uses a Governance-validated result flow:

```text
Governance Service -> Agent Runtime
Agent Runtime -> Governance Service
Governance Service -> Audit Replay Service
```

Agent Runtime does not publish the Phase 2 Loan Proposal or Decision Envelope directly to Audit Replay. Audit Replay receives `governance.agent-result.validated.v1` only after Governance validates or rejects the Agent result.

## Version Strategy

The Phase 1 `decision-envelope.v1.0.0` and `agent.evaluation.completed.v1` contracts remain published mock-evaluation contracts. Phase 2 adds separate contracts instead of repurposing the Phase 1 mock envelope:

- `loan-proposal.v1.0.0`
- `loan-decision-agent-result.v1.0.0`
- `phase-2-decision-envelope.v1.0.0`
- `governance.agent-result.validated.v1.0.0`

This avoids changing mock result meaning or requiring Phase 1 consumers to understand model provenance, SHAP, feature schema and artifact digest fields.

## Idempotency

Governance owns `evaluationRunId`, `agentRunId` and the request idempotency mapping. `requestIdempotencyKey` does not include `agentRunId`; it represents the fixed logical request inputs:

- `decisionCaseId`
- `evaluationRunId`
- `agentType`
- Snapshot digest or immutable reference
- `featureSchemaVersion`
- `modelVersion`
- `modelArtifactDigest`
- `thresholdVersion`

Agent Runtime owns `attemptId` and runtime attempt metadata. Retrying the same logical request reuses `agentRunId` and creates a new `attemptId`.

## Failure Classification

Top-level classifications are:

- `RETRYABLE`
- `NON_RETRYABLE`
- `VALIDATION_REQUIRED`
- `BLOCKED`

The validator fixes reason-code mappings for Phase 2. Unknown failure codes must not be interpreted as success.

## Model Provenance

`tabular-model-manifest.v1.0.0` requires framework, feature schema, preprocessing, dataset, training commit, random seed, threshold, artifact digest, SHAP explainer, runtime and dependency metadata. Model binary artifacts are referenced by immutable URI and digest; binaries are not stored in this repository.

## Known Limitations

- Agent transport remains payload-level and transport-neutral. Kafka or REST wrapping is a downstream implementation decision.
- Loan Service Snapshot compatibility is validated through the Snapshot Reference contract here; service code changes, if required, are handled in a later repository PR.
- Existing Evaluation Run v1/v2 contracts still include Prompt component provenance for Phase 1 compatibility. Phase 2 does not add Local LLM or Prompt contracts.

## Follow-up Repository

`rippleguard-agent-runtime` implements synthetic dataset preparation, preprocessing, model comparison, `TabularModelPort`, SHAP generation and reproducible Loan Proposal output against these contracts.
