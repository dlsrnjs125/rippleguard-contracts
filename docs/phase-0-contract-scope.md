# Phase 0 Contract Scope

## Included

- Common Event Envelope and eight Phase 1 Core MSA events
- External Risk Signal with purpose, subject scope, inference status, confidence, lifetime, and use restrictions
- Loan Application and Decision Case status Enums
- Immutable/recalculable Evaluation Run
- Implementation-neutral Decision Envelope for mock and later Agent evaluation
- Minimal Phase 1 Assurance result
- Local and CI contract validation

Phase 1 final Decision Commands require a completed Evaluation Run and complete Assurance. Conditional approval remains a proposal only; executable conditional approval is deferred until condition references and agreement versioning are defined.

Phase 1 also requires referenced Runs, Decisions, and Commands to exist, requires direct Proposal-to-final mapping without an unexplained policy override, and fixes Event producer ownership. Future policy transformations require a separate versioned contract and ADR.

## Deferred

Phase 0 does not establish final Consequence Envelope, Evidence & Control Findings, Assurance Profile, OPA Policy Input, REST/OpenAPI, Registry Server, or language-specific DTO contracts. Those contracts require later-phase evidence and policy decisions and must not be inferred from these minimum schemas.
