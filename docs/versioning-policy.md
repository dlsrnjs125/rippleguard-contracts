# Versioning Policy

Contracts use semantic Schema versions. The Event name in `eventType` carries its major contract generation (for example, `loan.application.submitted.v1`), while `schemaVersion` records the full Schema version (for example, `1.0.0`). They are intentionally separate.

- Patch: descriptions, examples, or validation tooling changes that do not alter accepted data.
- Minor: backward-compatible additions such as optional fields or new schemas.
- Major: removed/renamed fields, new required fields, narrowed values, changed meaning, or any other incompatible change.

Published incompatible contracts are immutable. Add a new major-version file and Event name instead of overwriting the existing Schema. Producers and consumers declare supported versions independently and migrate with an overlap window.
