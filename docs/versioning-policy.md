# Versioning Policy

Contracts use semantic Schema versions. The Event name in `eventType` carries its major contract generation (for example, `loan.application.submitted.v1`), while `schemaVersion` records the full Schema version (for example, `1.0.0`). They are intentionally separate.

Each published Event Schema has a full SemVer filename, such as `loan.application.submitted.v1.0.0.schema.json`. Multiple minor versions coexist as immutable files while retaining the same major-generation `eventType`. Event fixtures are grouped under `examples/{valid,invalid}/events/v<major>.<minor>.<patch>/` and resolve to the exact Schema version.

Independent object contracts are also full-version files and carry their own `schemaVersion`, for example `decision-envelope.v1.0.0.schema.json`, `evaluation-run.v1.0.0.schema.json`, and `external-risk-signal.v1.0.0.schema.json`. Event `$ref` values always target an immutable full version rather than an unversioned moving file.

Domain payload components embedded in a versioned Event, such as `loan-decision-command.v1.0.0.schema.json`, do not duplicate `schemaVersion` inside the payload. Their exact version is selected by the immutable Event `$ref`. For Phase 1, `loan.decision.commanded.v1.0.0.schema.json` references `phase-1-loan-decision-command-profile.v1.0.0.schema.json` directly, making the executable Kafka payload stricter than the reusable base Command object. Command `idempotencyKey` covers business application while Event `eventId` covers transport deduplication.

Loan Decision Command is not an independent transfer object in Phase 1. It is always embedded in a versioned Event payload or another versioned API Schema. If a standalone Command API is introduced later, its wrapper must carry an explicit version.

Scalar leaf contracts such as Loan Application Status, Decision Case Status, and Assurance Result use full-version filenames and `$id` values but remain scalar JSON values without an embedded `schemaVersion`; the consumer selects their immutable Schema version explicitly.

- Patch: descriptions, examples, or validation tooling changes that do not alter accepted data.
- Minor: backward-compatible additions such as optional fields or new schemas.
- Major: removed/renamed fields, new required fields, narrowed values, changed meaning, or any other incompatible change.

Published contracts are immutable. Add a new full-version file for every release and add a new major-generation Event name for incompatible changes. Producers and consumers declare supported versions independently and migrate with an overlap window. Fixture-backed compatibility validation upgrades older same-major fixture payloads to each newer minor Schema version and applies both JSON Schema and semantic invariants. This detects regressions in covered fixtures but is not a proof over every possible historical input.
