# Compatibility Policy

A change is backward compatible only when an existing consumer can continue validating and interpreting previously valid messages without changed meaning. Optional additions may be compatible only after checking consumers that reject unknown fields.

Every change must run `make validate`, retain valid fixtures for supported versions, and add an invalid fixture when introducing a meaningful constraint. `$id` values are unique and stable; local Schema composition uses resolvable relative `$ref` paths. Event filenames, `eventType`, and `schemaVersion` must agree, and older same-major payloads must remain valid after selecting a newer minor version.

Consumers validate before processing, reject unsupported major versions, and preserve the original message for audit. Producers do not silently repurpose fields or Enum values. Event delivery guarantees do not replace application-level idempotency based on `eventId`.

Unknown failure reason codes are not backward-compatible success. Consumers must route unknown failure codes to a validation-required or blocked path unless a versioned contract explicitly defines a safe mapping.

JSON Schema validation is supplemented by deterministic semantic invariants for cross-field identifiers, reference existence, Evaluation-to-Decision-to-Command consistency, Proposal-to-final mapping, Run supersession, component identity, root causation, risk-signal lifetime, and mutually exclusive purpose restrictions. The validation context rejects duplicate Event, Decision, Evaluation Run, Command, Risk Signal, and Evidence Request identifiers instead of selecting a fixture by file order.

Repository semantic validation operates on the fixture set and protects the published contract baseline. It is not a runtime registry. Governance Service and other consumers must enforce the same referential integrity against their authoritative database state before processing or emitting messages.

Independent Schema fixtures are validated in isolation. Cross-reference, causation, identifier uniqueness, and supersession graph rules run only within an explicit Scenario, preventing unrelated fixtures from sharing a global synthetic database. Each Scenario declares its legal `causationEdges`; this keeps direct evaluation and evidence reassessment as separate valid routes instead of forcing all Cases through one global sequence.

`examples/scenarios/valid/**` is always validated and cannot opt out. `examples/scenarios/invalid/**` must declare a non-empty `expectedSemanticErrors` list, which must exactly match the errors produced by that Scenario. The legacy `validate: false` escape hatch is prohibited. Individual invalid fixture expectations and their optional Scenario context remain in `examples/invalid/manifest.json`.

`causationId` is the source of truth for causal order. `occurredAt` is a secondary guard that detects only a strict time reversal; equal timestamps are legal because independently produced Events can share the same clock precision.

Scenario domain objects are post-event state snapshots. Therefore, when an `agent.evaluation.completed.v1` Event is present, its referenced Evaluation Run must already have `status: COMPLETED` in the same Scenario context.

The Schema `producer` field expresses event ownership, not authentication. Runtime consumers must compare it with authenticated Kafka credentials or workload identity; a matching JSON string alone is not trusted service identity.

For Phase 1 mock evaluation, Governance Service is the publishing `producer` and `mock-evaluator` is the logical `evaluatorId`. Later Agent execution uses `agent-runtime` as the service producer and a named Agent as evaluator. Decision `generatorRef` values must match the Agent, Model, and Prompt components recorded by the referenced Evaluation Run.
