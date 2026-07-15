# Compatibility Policy

A change is backward compatible only when an existing consumer can continue validating and interpreting previously valid messages without changed meaning. Optional additions may be compatible only after checking consumers that reject unknown fields.

Every change must run `make validate`, retain valid fixtures for supported versions, and add an invalid fixture when introducing a meaningful constraint. `$id` values are unique and stable; local Schema composition uses resolvable relative `$ref` paths. Event filenames, `eventType`, and `schemaVersion` must agree, and older same-major payloads must remain valid after selecting a newer minor version.

Consumers validate before processing, reject unsupported major versions, and preserve the original message for audit. Producers do not silently repurpose fields or Enum values. Event delivery guarantees do not replace application-level idempotency based on `eventId`.

JSON Schema validation is supplemented by deterministic semantic invariants for cross-field identifiers, Evaluation-to-Decision references, root causation, risk-signal lifetime, and mutually exclusive purpose restrictions.
