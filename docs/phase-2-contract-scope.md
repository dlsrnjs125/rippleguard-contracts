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
- Governance Agent Result validated/rejected audit events
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

Agent Runtime does not publish the Phase 2 Loan Proposal or Decision Envelope directly to Audit Replay. Audit Replay receives Governance-owned validation Events only after Governance validates or rejects the Agent result.

## Version Strategy

The Phase 1 `decision-envelope.v1.0.0` and `agent.evaluation.completed.v1` contracts remain published mock-evaluation contracts. Phase 2 adds separate contracts instead of repurposing the Phase 1 mock envelope:

- `loan-proposal.v1.0.0`
- `loan-decision-agent-result.v1.0.0`
- `phase-2-decision-envelope.v1.0.0`
- `governance.agent-result.validated.v1.0.0`
- `governance.agent-result.validated.v2.0.0`

This avoids changing mock result meaning or requiring Phase 1 consumers to understand model provenance, SHAP, feature schema and artifact digest fields.

`governance.agent-result.validated.v1.0.0` remains published for existing consumers. It must not be interpreted as provenance-complete because it does not carry the full immutable Snapshot and Model baseline required by the Audit Agent Run projection.

`governance.agent-result.validated.v2.0.0` is the provenance-complete Phase 2 event. It requires `snapshotDigest`, `featureSchemaVersion`, `preprocessingVersion`, `modelVersion`, `modelArtifactDigest` and `thresholdVersion` in the validation payload. Audit consumers that need complete Agent Run provenance should consume v2 rather than inferring missing values from v1 or querying another service.

## Idempotency

Governance owns `evaluationRunId`, `agentRunId` and the request idempotency mapping. `requestIdempotencyKey` does not include `agentRunId`; it represents the fixed logical request inputs:

- `decisionCaseId`
- `evaluationRunId`
- `agentType`
- Snapshot digest or immutable reference
- `featureSchemaVersion`
- `preprocessingVersion`
- `modelVersion`
- `modelArtifactDigest`
- `thresholdVersion`

Agent Runtime owns `attemptId` and runtime attempt metadata. Retrying the same logical request reuses `agentRunId` and creates a new `attemptId`.

Duplicate identical requests are handled by Governance idempotency lookup and should return or acknowledge the existing run/result instead of creating a new failed Agent Result. If the same idempotency key is reused with different immutable inputs, the request is blocked as `AGENT_RUN_INPUT_CONFLICT`.

`requestedAt` must be earlier than `deadlineAt`, and the referenced Snapshot must already exist by request time.

## Failure Classification

Top-level classifications are:

- `RETRYABLE`
- `NON_RETRYABLE`
- `VALIDATION_REQUIRED`
- `BLOCKED`

The validator fixes reason-code mappings for Phase 2. Unknown failure codes must not be interpreted as success.

## Model Provenance

`tabular-model-manifest.v1.0.0` requires framework, feature schema, preprocessing, dataset, training commit, random seed, threshold, artifact digest, SHAP explainer, runtime and dependency metadata. Model binary artifacts are referenced by immutable URI and digest; binaries are not stored in this repository.

`tabular-model-manifest-template.v1.0.0` is a separate source-artifact contract for Agent Runtime repositories. It is intentionally not the same `$id` as the published manifest contract, so strict v1 consumers do not see new properties under an unchanged schema identity.

`runtimeImageDigest` means the OCI image manifest digest for the exact runtime image. A local mutable tag, Docker image ID or archive hash is not accepted as the published runtime baseline.

The committed source manifest can use the template contract because the runtime image digest is not knowable until after the Infra image build. Template manifests do not require `runtimeImageDigest`; if an existing source artifact still carries a placeholder digest, it remains template-only metadata. The Infra release materialization produces the published `tabular-model-manifest.v1.0.0` by injecting the exact immutable OCI digest. The committed template is not release evidence.

`runtimeImageDigest` is not:

- the Agent Runtime source commit SHA
- the model artifact digest
- a placeholder such as all `f`, all `0` or repeated hex
- a candidate value copied into release evidence before image publication

Contracts validation permits placeholders only in `tabular-model-manifest-template.v1.0.0` fixtures and rejects placeholder or training-code-commit-derived values in published `tabular-model-manifest.v1.0.0` fixtures. Runtime source commit and OCI revision cross-checking belongs to Infra release materialization because Contracts alone does not know the service image build context.

## Audit Result Reference

Governance audit events use two provenance links:

- `causationId` references the nearest persisted event cause, currently `agent.evaluation.requested.v1`.
- `agentResultReference` and `agentResultDigest` reference the directly validated Agent Result payload.

For `governance.agent-result.validated.v2`, `payload.requestEventId` must equal the envelope `causationId`. Both fields identify the persisted `agent.evaluation.requested.v1` Event that caused the validation. `causationId` must not be populated with `agentRunId`. `agentRunId` remains the Agent execution domain identity carried in the validation payload and Agent Result reference. Consumers, including Audit Replay, must reject a validation Event that reuses an Agent Run identity as Event causation.

In v2, `VALIDATED` always requires `agentResultReference` and `agentResultDigest`. `REJECTED` distinguishes two paths: when an Agent Result was produced and failed Governance validation, those fields remain required; when the Agent failed before producing a result, `AGENT_FAILURE_RECORDED` can be emitted without synthetic Agent Result identifiers.

`agentResultReference` is fixed as `agent-result://{decisionCaseId}/{agentRunId}/attempt-{attemptId}`.

`agentResultDigest` is SHA-256 over the full Agent Result payload using RFC 8785 JSON Canonicalization Scheme (JCS):

- UTF-8
- object keys sorted lexicographically by code point
- no insignificant whitespace
- JSON number representation follows RFC 8785
- Unicode string serialization follows RFC 8785
- `NaN` and `Infinity` are forbidden
- duplicate object keys are forbidden

If a future transport introduces a persisted Agent Result event, the audit event `causationId` may move to that result event in a new contract version.

`examples/digest-vectors/agent-result-v1` provides a cross-language golden vector:

- `agent-result-input.json`
- `canonical-agent-result.json`
- `expected-sha256.txt`

Python Agent Runtime and Java Governance implementations must match this vector before publishing or validating `agentResultDigest`.

## Feature Digest

`featurePayloadDigest` is producer-declared in v1.0.0 and identifies the materialized feature payload used for the request. Runtime producers and consumers must recompute it consistently, but a shared canonical Feature Payload digest profile is deferred to a later compatible schema revision. Until then, `make validate` guards the published Feature Schema and executable Feature Payload schema against type/range/required-field drift.

## Known Limitations

- Agent transport remains payload-level and transport-neutral. Kafka or REST wrapping is a downstream implementation decision.
- Loan Service Snapshot compatibility is validated through the Snapshot Reference contract here; service code changes, if required, are handled in a later repository PR.
- Existing Evaluation Run v1/v2 contracts still include Prompt component provenance for Phase 1 compatibility. Phase 2 does not add Local LLM or Prompt contracts.
- `trainingCodeCommit` is currently a Git SHA-1 commit reference. Non-Git or SHA-256 source provenance can be introduced in a later schema version if needed.

## Follow-up Repository

`rippleguard-agent-runtime` implements synthetic dataset preparation, preprocessing, model comparison, `TabularModelPort`, SHAP generation and reproducible Loan Proposal output against these contracts.
