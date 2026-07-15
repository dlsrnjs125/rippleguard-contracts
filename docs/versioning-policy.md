# Versioning Policy

Contracts use semantic Schema versions. The Event name in `eventType` carries its major contract generation (for example, `loan.application.submitted.v1`), while `schemaVersion` records the full Schema version (for example, `1.0.0`). They are intentionally separate.

Each published Event Schema has a full SemVer filename, such as `loan.application.submitted.v1.0.0.schema.json`. Multiple minor versions coexist as immutable files while retaining the same major-generation `eventType`. Event fixtures are grouped under `examples/{valid,invalid}/events/v<major>.<minor>.<patch>/` and resolve to the exact Schema version.

Independent object contracts are also full-version files and carry their own `schemaVersion`, for example `decision-envelope.v1.0.0.schema.json`, `evaluation-run.v1.0.0.schema.json`, and `external-risk-signal.v1.0.0.schema.json`. Event `$ref` values always target an immutable full version rather than an unversioned moving file.

- Patch: descriptions, examples, or validation tooling changes that do not alter accepted data.
- Minor: backward-compatible additions such as optional fields or new schemas.
- Major: removed/renamed fields, new required fields, narrowed values, changed meaning, or any other incompatible change.

Published contracts are immutable. Add a new full-version file for every release and add a new major-generation Event name for incompatible changes. Producers and consumers declare supported versions independently and migrate with an overlap window. Fixture-backed compatibility validation upgrades older same-major fixture payloads to each newer minor Schema version and applies both JSON Schema and semantic invariants. This detects regressions in covered fixtures but is not a proof over every possible historical input.
