#!/usr/bin/env python3
"""Validate all RippleGuard schemas and contract examples."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

try:
    from jsonschema import FormatChecker, ValidationError, validators
    from referencing import Registry, Resource
except ImportError:
    print("ERROR: install development dependencies with: python -m pip install -r requirements-dev.txt", file=sys.stderr)
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
VALID = ROOT / "examples" / "valid"
INVALID = ROOT / "examples" / "invalid"

EXPECTED_INVALID = {
    "events/v1.0.0/loan.application.submitted.v1--missing-event-id.json": ("required", "eventId"),
    "events/v1.0.0/loan.application.submitted.v1--bad-date.json": ("pattern", "occurredAt"),
    "events/v1.0.0/loan.application.submitted.v1--non-null-root-causation.json": ("const", "causationId"),
    "events/v1.0.0/governance.review.started.v1--null-causation.json": ("type", "causationId"),
    "events/v1.0.0/governance.review.started.v1--mismatched-envelope-case.json": ("semantic", "EVENT_CASE_DECISION_MISMATCH"),
    "events/v1.0.0/governance.evidence.requested.v1--missing-evaluation-run.json": ("required", "evaluationRunId"),
    "events/v1.0.0/agent.evaluation.completed.v1--mismatched-identifiers.json": ("semantic", "EVALUATION_COMPLETED_ID_MISMATCH"),
    "events/v1.0.0/loan.decision.commanded.v1--approve-assurance-violated.json": ("const", "assuranceResult"),
    "events/v1.0.0/loan.decision.commanded.v1--approve-assurance-incomplete.json": ("const", "assuranceResult"),
    "events/v1.0.0/loan.decision.commanded.v1--reject-assurance-incomplete.json": ("const", "assuranceResult"),
    "events/v1.0.0/loan.decision.commanded.v1--blocked-evaluation-run.json": ("semantic", "COMMAND_EVALUATION_NOT_COMPLETED"),
    "events/v1.0.0/loan.decision.commanded.v1--conditional-approve-without-contract.json": ("enum", "finalDecision"),
    "events/v1.0.0/loan.decision.commanded.v1--mismatched-evaluation-run.json": ("semantic", "COMMAND_DECISION_REFERENCE_MISMATCH"),
    "events/v1.0.0/loan.decision.finalized.v1--mismatched-command-reference.json": ("semantic", "FINALIZED_COMMAND_REFERENCE_MISMATCH"),
    "domain/loan-application-status--bad-enum.json": ("enum", "PENDING"),
    "domain/evaluation-run--missing-provenance.json": ("required", "componentVersions"),
    "agent-output/decision-envelope--bad-proposal.json": ("enum", "APPROVE"),
    "external-risk-signal/external-risk-signal--suspected-customer-scope.json": ("const", "subjectType"),
    "external-risk-signal/external-risk-signal--overlapping-uses.json": ("semantic", "RISK_SIGNAL_USE_OVERLAP"),
    "external-risk-signal/external-risk-signal--expired-before-occurrence.json": ("semantic", "RISK_SIGNAL_INVALID_LIFETIME"),
    "external-risk-signal/external-risk-signal--transaction-customer-use.json": ("semantic", "TRANSACTION_SIGNAL_CUSTOMER_USE"),
}

EVENT_SCHEMA_NAME = re.compile(r"^(?P<base>.+)\.v(?P<major>[1-9][0-9]*)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)\.schema\.json$")


def json_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.json"))


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid JSON in {path.relative_to(ROOT)}: {error}") from error


def nested_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                yield child
            yield from nested_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_refs(child)


def schema_for_example(example: Path, example_root: Path) -> Path:
    relative = example.relative_to(example_root)
    contract_name = relative.name.split("--", 1)[0] if "--" in relative.name else relative.stem
    if len(relative.parts) >= 3 and relative.parts[0] == "events" and re.fullmatch(r"v[1-9][0-9]*\.[0-9]+\.[0-9]+", relative.parts[1]):
        version = relative.parts[1]
        major = version.split(".", 1)[0]
        if not contract_name.endswith(f".{major}"):
            return SCHEMAS / "__invalid_event_fixture_name__"
        event_base = contract_name[: -(len(major) + 1)]
        return SCHEMAS / "events" / f"{event_base}.{version}.schema.json"
    return SCHEMAS / relative.parent / f"{contract_name}.schema.json"


def errors_for(instance: Any, schema: dict[str, Any], registry: Registry) -> list[ValidationError]:
    validator_class = validators.validator_for(schema)
    validator = validator_class(schema, registry=registry, format_checker=FormatChecker())
    return sorted(validator.iter_errors(instance), key=lambda error: (list(error.absolute_path), error.message))


def build_semantic_context(instances: list[Any]) -> dict[str, dict[str, Any]]:
    decisions: dict[str, Any] = {}
    commands: dict[str, Any] = {}
    runs: dict[str, Any] = {}
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        payload = instance.get("payload", {})
        if instance.get("evaluationRunId") and instance.get("status") in {"CREATED", "RUNNING", "COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}:
            runs[instance["evaluationRunId"]] = instance
        if instance.get("eventType") == "agent.evaluation.completed.v1":
            decision = payload.get("decisionEnvelope", {})
            if decision.get("decisionId"):
                decisions[decision["decisionId"]] = decision
        if instance.get("eventType") == "loan.decision.commanded.v1" and payload.get("commandId"):
            commands[payload["commandId"]] = payload
    return {"decisions": decisions, "commands": commands, "runs": runs}


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def semantic_errors(instance: Any, context: dict[str, dict[str, Any]]) -> list[str]:
    if not isinstance(instance, dict):
        return []
    failures: list[str] = []
    event_type = instance.get("eventType")
    payload = instance.get("payload", {})

    if event_type == "loan.application.submitted.v1" and instance.get("caseId") != payload.get("applicationId"):
        failures.append("EVENT_CASE_APPLICATION_MISMATCH")
    if event_type and payload.get("decisionCaseId") and instance.get("caseId") != payload.get("decisionCaseId"):
        failures.append("EVENT_CASE_DECISION_MISMATCH")

    if event_type == "agent.evaluation.completed.v1":
        decision = payload.get("decisionEnvelope", {})
        if payload.get("evaluationRunId") != decision.get("evaluationRunId") or payload.get("decisionCaseId") != decision.get("decisionCaseId"):
            failures.append("EVALUATION_COMPLETED_ID_MISMATCH")

    if event_type == "loan.decision.commanded.v1":
        decision = context["decisions"].get(payload.get("decisionId"))
        if decision and (payload.get("evaluationRunId") != decision.get("evaluationRunId") or payload.get("decisionCaseId") != decision.get("decisionCaseId")):
            failures.append("COMMAND_DECISION_REFERENCE_MISMATCH")
        run = context["runs"].get(payload.get("evaluationRunId"))
        if run and run.get("status") != "COMPLETED":
            failures.append("COMMAND_EVALUATION_NOT_COMPLETED")

    if event_type == "loan.decision.finalized.v1":
        command = context["commands"].get(payload.get("commandId"))
        compared = ("decisionCaseId", "applicationId", "decisionId", "evaluationRunId", "finalDecision")
        if command and any(payload.get(field) != command.get(field) for field in compared):
            failures.append("FINALIZED_COMMAND_REFERENCE_MISMATCH")

    if "riskSignalId" in instance:
        permitted = set(instance.get("permittedUses", []))
        prohibited = set(instance.get("prohibitedUses", []))
        if permitted & prohibited:
            failures.append("RISK_SIGNAL_USE_OVERLAP")
        try:
            if parse_timestamp(instance["validUntil"]) <= parse_timestamp(instance["occurredAt"]):
                failures.append("RISK_SIGNAL_INVALID_LIFETIME")
        except (KeyError, TypeError, ValueError):
            pass
        if instance.get("subjectType") == "TRANSACTION" and "CUSTOMER_CREDIT_RISK_ASSESSMENT" in permitted:
            failures.append("TRANSACTION_SIGNAL_CUSTOMER_USE")
    return failures


def event_schema_metadata(path: Path, schema: dict[str, Any]) -> tuple[str, tuple[int, int, int]] | None:
    match = EVENT_SCHEMA_NAME.match(path.name)
    if path.parent != SCHEMAS / "events" or not match:
        return None
    version = tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
    event_type = f"{match.group('base')}.v{version[0]}"
    properties = schema.get("allOf", [{}, {}])[1].get("properties", {})
    if properties.get("eventType", {}).get("const") != event_type:
        raise ValueError(f"eventType does not match full SemVer filename: {path.relative_to(ROOT)}")
    if properties.get("schemaVersion", {}).get("const") != ".".join(map(str, version)):
        raise ValueError(f"schemaVersion does not match full SemVer filename: {path.relative_to(ROOT)}")
    return event_type, version


def main() -> int:
    failures: list[str] = []
    all_json = json_files(SCHEMAS) + json_files(VALID) + json_files(INVALID)
    loaded: dict[Path, Any] = {}

    for path in all_json:
        try:
            loaded[path] = load_json(path)
        except RuntimeError as error:
            failures.append(str(error))

    schema_paths = json_files(SCHEMAS)
    schema_ids = [loaded[path].get("$id") for path in schema_paths if isinstance(loaded.get(path), dict)]
    duplicates = sorted(value for value, count in Counter(schema_ids).items() if value and count > 1)
    if duplicates:
        failures.append(f"duplicate schema $id values: {', '.join(duplicates)}")
    if any(not value for value in schema_ids):
        failures.append("every schema must define a non-empty $id")

    registry = Registry()
    event_schemas: dict[str, list[tuple[tuple[int, int, int], Path, dict[str, Any]]]] = {}
    for path in schema_paths:
        schema = loaded.get(path)
        if not isinstance(schema, dict):
            continue
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.resolve().as_uri(), resource)
        if schema.get("$id"):
            registry = registry.with_resource(schema["$id"], resource)

        try:
            metadata = event_schema_metadata(path, schema)
            if metadata:
                event_type, version = metadata
                event_schemas.setdefault(event_type, []).append((version, path, schema))
        except ValueError as error:
            failures.append(str(error))

        try:
            validators.validator_for(schema).check_schema(schema)
        except Exception as error:  # jsonschema raises a family of schema errors
            failures.append(f"invalid schema {path.relative_to(ROOT)}: {error}")

        for reference in nested_refs(schema):
            if reference.startswith("#"):
                continue
            if "://" in reference or reference.startswith("urn:"):
                failures.append(f"non-relative $ref in {path.relative_to(ROOT)}: {reference}")
                continue
            target = (path.parent / reference.split("#", 1)[0]).resolve()
            if not target.is_file():
                failures.append(f"unresolved $ref in {path.relative_to(ROOT)}: {reference}")

    valid_paths = json_files(VALID)
    semantic_context = build_semantic_context([loaded[path] for path in valid_paths if path in loaded])
    valid_events: list[tuple[Path, dict[str, Any]]] = []
    for example in valid_paths:
        schema_path = schema_for_example(example, VALID)
        if schema_path not in loaded:
            failures.append(f"missing schema for valid example {example.relative_to(ROOT)}")
            continue
        errors = errors_for(loaded[example], loaded[schema_path], registry)
        if errors:
            failures.append(f"valid example failed {example.relative_to(ROOT)}: {errors[0].message}")
            continue
        semantic = semantic_errors(loaded[example], semantic_context)
        if semantic:
            failures.append(f"valid example failed semantic invariants {example.relative_to(ROOT)}: {', '.join(semantic)}")
        if isinstance(loaded[example], dict) and loaded[example].get("eventType"):
            valid_events.append((example, loaded[example]))

    compatibility_checks = 0
    for example, instance in valid_events:
        event_type = instance["eventType"]
        source_version = tuple(int(part) for part in instance["schemaVersion"].split("."))
        for target_version, target_path, target_schema in event_schemas.get(event_type, []):
            if target_version <= source_version or target_version[0] != source_version[0]:
                continue
            upgraded_instance = deepcopy(instance)
            upgraded_instance["schemaVersion"] = ".".join(map(str, target_version))
            errors = errors_for(upgraded_instance, target_schema, registry)
            compatibility_checks += 1
            if errors:
                failures.append(
                    f"minor compatibility failed: {example.relative_to(ROOT)} payload is not accepted by {target_path.relative_to(ROOT)}: {errors[0].message}"
                )

    actual_invalid = {str(path.relative_to(INVALID)) for path in json_files(INVALID)}
    if actual_invalid != set(EXPECTED_INVALID):
        failures.append("invalid example inventory differs from EXPECTED_INVALID declarations")

    for example in json_files(INVALID):
        relative = str(example.relative_to(INVALID))
        schema_path = schema_for_example(example, INVALID)
        if schema_path not in loaded:
            failures.append(f"missing schema for invalid example {example.relative_to(ROOT)}")
            continue
        errors = errors_for(loaded[example], loaded[schema_path], registry)
        semantic = semantic_errors(loaded[example], semantic_context)
        if not errors and not semantic:
            failures.append(f"invalid example unexpectedly passed {example.relative_to(ROOT)}")
            continue
        expected_validator, expected_text = EXPECTED_INVALID[relative]
        matched_schema = any(error.validator == expected_validator and expected_text in (error.message + "/" + "/".join(map(str, error.absolute_path))) for error in errors)
        matched_semantic = expected_validator == "semantic" and expected_text in semantic
        if not matched_schema and not matched_semantic:
            rendered_schema = "; ".join(f"{error.validator}: {error.message}" for error in errors)
            rendered_semantic = ", ".join(semantic)
            rendered = "; ".join(part for part in (rendered_schema, rendered_semantic) if part)
            failures.append(f"invalid example failed for the wrong reason {example.relative_to(ROOT)}: {rendered}")

    if failures:
        print("Contract validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        f"Validated {len(schema_paths)} schemas, {len(valid_paths)} valid examples, "
        f"{len(json_files(INVALID))} intentional invalid examples, and {compatibility_checks} minor compatibility checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
