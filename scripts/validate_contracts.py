#!/usr/bin/env python3
"""Validate RippleGuard schemas, fixtures, scenarios, and compatibility."""

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
    print("ERROR: install dependencies with: python -m pip install -r requirements-dev.txt", file=sys.stderr)
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
VALID = ROOT / "examples" / "valid"
INVALID = ROOT / "examples" / "invalid"
SCENARIOS = ROOT / "examples" / "scenarios"
INVALID_MANIFEST = INVALID / "manifest.json"
VERSIONED_SCHEMA_NAME = re.compile(
    r"^(?P<base>.+)\.v(?P<major>[1-9][0-9]*)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)\.schema\.json$"
)
VERSION_DIR = re.compile(r"v[1-9][0-9]*\.[0-9]+\.[0-9]+")


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


def property_consts(value: Any, property_name: str) -> set[Any]:
    found: set[Any] = set()
    if isinstance(value, dict):
        candidate = value.get("properties", {}).get(property_name)
        if isinstance(candidate, dict) and "const" in candidate:
            found.add(candidate["const"])
        for child in value.values():
            found.update(property_consts(child, property_name))
    elif isinstance(value, list):
        for child in value:
            found.update(property_consts(child, property_name))
    return found


def schema_for_example(example: Path, example_root: Path, instance: Any) -> Path:
    relative = example.relative_to(example_root)
    contract_name = relative.name.split("--", 1)[0] if "--" in relative.name else relative.stem
    if len(relative.parts) >= 3 and relative.parts[0] == "events" and VERSION_DIR.fullmatch(relative.parts[1]):
        version = relative.parts[1]
        major = version.split(".", 1)[0]
        if not contract_name.endswith(f".{major}"):
            return SCHEMAS / "__invalid_event_fixture_name__"
        event_base = contract_name[: -(len(major) + 1)]
        return SCHEMAS / "events" / f"{event_base}.{version}.schema.json"
    if len(relative.parts) >= 3 and VERSION_DIR.fullmatch(relative.parts[-2]):
        version = relative.parts[-2]
        return SCHEMAS.joinpath(*relative.parts[:-2]) / f"{contract_name}.{version}.schema.json"
    if isinstance(instance, dict) and re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+", str(instance.get("schemaVersion", ""))):
        versioned = SCHEMAS / relative.parent / f"{contract_name}.v{instance['schemaVersion']}.schema.json"
        if versioned.is_file():
            return versioned
    return SCHEMAS / relative.parent / f"{contract_name}.schema.json"


def errors_for(instance: Any, schema: dict[str, Any], registry: Registry) -> list[ValidationError]:
    validator_class = validators.validator_for(schema)
    validator = validator_class(schema, registry=registry, format_checker=FormatChecker())
    return sorted(validator.iter_errors(instance), key=lambda error: (list(error.absolute_path), error.message))


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def register_unique(index: dict[str, Any], key: Any, value: Any, code: str, failures: list[str]) -> None:
    if not key:
        return
    if key in index:
        failures.append(f"{code}: {key}")
    else:
        index[key] = value


def build_semantic_context(
    instances: list[Any], causation_edges: set[tuple[str, str]] | None = None
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    context: dict[str, Any] = {
        "events": {}, "decisions": {}, "commands": {}, "runs": {},
        "risk_signals": {}, "evidence_requests": {},
        "causation_edges": causation_edges or set(),
    }
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        payload = instance.get("payload", {})
        register_unique(context["events"], instance.get("eventId"), instance, "DUPLICATE_EVENT_ID", failures)
        register_unique(context["risk_signals"], instance.get("riskSignalId"), instance, "DUPLICATE_RISK_SIGNAL_ID", failures)
        if instance.get("evaluationRunId") and instance.get("componentVersions"):
            register_unique(context["runs"], instance["evaluationRunId"], instance, "DUPLICATE_EVALUATION_RUN_ID", failures)
        if instance.get("decisionId") and instance.get("proposal"):
            register_unique(context["decisions"], instance["decisionId"], instance, "DUPLICATE_DECISION_ID", failures)
        if instance.get("eventType") == "agent.evaluation.completed.v1":
            decision = payload.get("decisionEnvelope", {})
            register_unique(context["decisions"], decision.get("decisionId"), decision, "DUPLICATE_DECISION_ID", failures)
        if instance.get("eventType") == "loan.decision.commanded.v1":
            register_unique(context["commands"], payload.get("commandId"), payload, "DUPLICATE_COMMAND_ID", failures)
        if instance.get("eventType") == "governance.evidence.requested.v1":
            register_unique(context["evidence_requests"], payload.get("requestId"), payload, "DUPLICATE_EVIDENCE_REQUEST_ID", failures)
    return context, failures


def decision_provenance_errors(decision: dict[str, Any], run: dict[str, Any]) -> list[str]:
    generator = decision.get("generatorRef", {})
    failures: list[str] = []
    if decision.get("evaluatorId") != generator.get("agentName"):
        failures.append("DECISION_EVALUATOR_GENERATOR_MISMATCH")
    required = {
        ("AGENT", generator.get("agentName"), generator.get("agentVersion")),
        ("MODEL", generator.get("modelName"), generator.get("modelVersion")),
        ("PROMPT", generator.get("promptName"), generator.get("promptVersion")),
    }
    actual = {
        (component.get("componentType"), component.get("componentName"), component.get("version"))
        for component in run.get("componentVersions", [])
    }
    if not required.issubset(actual):
        failures.append("DECISION_GENERATOR_NOT_IN_RUN")
    return failures


def semantic_errors(instance: Any, context: dict[str, Any] | None = None) -> list[str]:
    if not isinstance(instance, dict):
        return []
    failures: list[str] = []
    event_type = instance.get("eventType")
    payload = instance.get("payload", {})

    if event_type == "loan.application.submitted.v1" and instance.get("caseId") != payload.get("applicationId"):
        failures.append("EVENT_CASE_APPLICATION_MISMATCH")
    if event_type and payload.get("decisionCaseId") and instance.get("caseId") != payload.get("decisionCaseId"):
        failures.append("EVENT_CASE_DECISION_MISMATCH")
    if event_type and payload.get("applicationId") and instance.get("correlationId") != payload.get("applicationId"):
        failures.append("EVENT_APPLICATION_CORRELATION_MISMATCH")

    if event_type == "agent.evaluation.completed.v1":
        decision = payload.get("decisionEnvelope", {})
        if payload.get("evaluationRunId") != decision.get("evaluationRunId") or payload.get("decisionCaseId") != decision.get("decisionCaseId"):
            failures.append("EVALUATION_COMPLETED_ID_MISMATCH")
        if payload.get("evaluatorId") != decision.get("evaluatorId"):
            failures.append("EVALUATION_COMPLETED_EVALUATOR_MISMATCH")
        try:
            if parse_timestamp(instance["occurredAt"]) >= parse_timestamp(decision["validUntil"]):
                failures.append("EVALUATION_COMPLETED_WITH_EXPIRED_DECISION")
        except (KeyError, TypeError, ValueError):
            pass

    if instance.get("evaluationRunId") and instance.get("componentVersions"):
        keys = [(item.get("componentType"), item.get("componentName")) for item in instance["componentVersions"]]
        if len(keys) != len(set(keys)):
            failures.append("EVALUATION_COMPONENT_IDENTITY_DUPLICATE")
        if instance.get("supersedesRunId") == instance.get("evaluationRunId"):
            failures.append("EVALUATION_RUN_SELF_SUPERSEDES")

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

    if context is None:
        return failures

    if event_type and instance.get("causationId") is not None:
        cause = context["events"].get(instance["causationId"])
        if cause is None:
            failures.append("CAUSATION_EVENT_NOT_FOUND")
        else:
            if cause.get("correlationId") != instance.get("correlationId"):
                failures.append("CAUSATION_CORRELATION_MISMATCH")
            try:
                if parse_timestamp(cause["occurredAt"]) > parse_timestamp(instance["occurredAt"]):
                    failures.append("CAUSATION_TIME_ORDER_INVALID")
            except (KeyError, TypeError, ValueError):
                pass
            edge = (cause.get("eventType"), event_type)
            if edge not in context["causation_edges"]:
                failures.append("CAUSATION_EVENT_TYPE_INVALID")
            cause_payload = cause.get("payload", {})
            if event_type == "agent.evaluation.completed.v1" and cause_payload.get("evaluationRunId") != payload.get("evaluationRunId"):
                failures.append("CAUSATION_EVALUATION_RUN_MISMATCH")
            if event_type == "loan.decision.commanded.v1" and cause_payload.get("decisionEnvelope", {}).get("decisionId") != payload.get("decisionId"):
                failures.append("CAUSATION_DECISION_MISMATCH")
            if event_type == "loan.decision.finalized.v1" and cause_payload.get("commandId") != payload.get("commandId"):
                failures.append("CAUSATION_COMMAND_MISMATCH")

    if event_type == "agent.evaluation.requested.v1":
        run = context["runs"].get(payload.get("evaluationRunId"))
        if run is None:
            failures.append("EVALUATION_REQUEST_RUN_NOT_FOUND")
        else:
            if run.get("decisionCaseId") != payload.get("decisionCaseId"):
                failures.append("EVALUATION_REQUEST_RUN_CASE_MISMATCH")
            if run.get("inputSnapshotVersion") != payload.get("inputSnapshotVersion"):
                failures.append("EVALUATION_REQUEST_SNAPSHOT_MISMATCH")
            if run.get("executionPlanVersion") != payload.get("executionPlanVersion"):
                failures.append("EVALUATION_REQUEST_PLAN_MISMATCH")

    if event_type == "agent.evaluation.completed.v1":
        decision = payload.get("decisionEnvelope", {})
        run = context["runs"].get(payload.get("evaluationRunId"))
        if run is None:
            failures.append("EVALUATION_COMPLETED_RUN_NOT_FOUND")
        else:
            if run.get("decisionCaseId") != payload.get("decisionCaseId"):
                failures.append("EVALUATION_COMPLETED_RUN_CASE_MISMATCH")
            if run.get("status") != "COMPLETED":
                failures.append("EVALUATION_COMPLETED_RUN_NOT_COMPLETED")
            failures.extend(decision_provenance_errors(decision, run))

    if event_type == "governance.evidence.requested.v1":
        run = context["runs"].get(payload.get("evaluationRunId"))
        if run is None:
            failures.append("EVIDENCE_REQUEST_RUN_NOT_FOUND")
        else:
            if run.get("decisionCaseId") != payload.get("decisionCaseId"):
                failures.append("EVIDENCE_REQUEST_RUN_CASE_MISMATCH")
            if run.get("inputSnapshotVersion") != payload.get("inputSnapshotVersion"):
                failures.append("EVIDENCE_REQUEST_SNAPSHOT_MISMATCH")

    if event_type == "loan.decision.commanded.v1":
        decision = context["decisions"].get(payload.get("decisionId"))
        if decision is None:
            failures.append("COMMAND_DECISION_NOT_FOUND")
        elif payload.get("evaluationRunId") != decision.get("evaluationRunId") or payload.get("decisionCaseId") != decision.get("decisionCaseId"):
            failures.append("COMMAND_DECISION_REFERENCE_MISMATCH")
        else:
            expected = {"PROPOSE_APPROVE": "APPROVE", "PROPOSE_REJECT": "REJECT"}.get(decision.get("proposal"))
            if expected is None:
                failures.append("COMMAND_NOT_ALLOWED_FOR_PROPOSAL")
            elif payload.get("finalDecision") != expected:
                failures.append("COMMAND_PROPOSAL_DECISION_MISMATCH")
            try:
                if parse_timestamp(instance["occurredAt"]) >= parse_timestamp(decision["validUntil"]):
                    failures.append("COMMAND_DECISION_EXPIRED")
            except (KeyError, TypeError, ValueError):
                pass
        run = context["runs"].get(payload.get("evaluationRunId"))
        if run is None:
            failures.append("COMMAND_EVALUATION_RUN_NOT_FOUND")
        elif run.get("status") != "COMPLETED":
            failures.append("COMMAND_EVALUATION_NOT_COMPLETED")

    if event_type == "loan.decision.finalized.v1":
        command = context["commands"].get(payload.get("commandId"))
        compared = ("decisionCaseId", "applicationId", "decisionId", "evaluationRunId", "finalDecision")
        if command is None:
            failures.append("FINALIZED_COMMAND_NOT_FOUND")
        elif any(payload.get(field) != command.get(field) for field in compared):
            failures.append("FINALIZED_COMMAND_REFERENCE_MISMATCH")

    if instance.get("decisionId") and instance.get("generatorRef"):
        run = context["runs"].get(instance.get("evaluationRunId"))
        if run is None:
            failures.append("DECISION_EVALUATION_RUN_NOT_FOUND")
        else:
            failures.extend(decision_provenance_errors(instance, run))

    if instance.get("evaluationRunId") and instance.get("componentVersions") and instance.get("supersedesRunId") is not None:
        previous = context["runs"].get(instance["supersedesRunId"])
        if previous is None:
            failures.append("EVALUATION_RUN_SUPERSEDES_NOT_FOUND")
        else:
            if previous.get("decisionCaseId") != instance.get("decisionCaseId"):
                failures.append("EVALUATION_RUN_SUPERSEDES_CASE_MISMATCH")
            if previous.get("inputSnapshotVersion") == instance.get("inputSnapshotVersion"):
                failures.append("EVALUATION_RUN_SUPERSEDES_SNAPSHOT_UNCHANGED")
            try:
                if parse_timestamp(instance["createdAt"]) <= parse_timestamp(previous["createdAt"]):
                    failures.append("EVALUATION_RUN_SUPERSEDES_TIME_INVALID")
            except (KeyError, TypeError, ValueError):
                pass
            if previous.get("status") not in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}:
                failures.append("EVALUATION_RUN_SUPERSEDES_NON_TERMINAL")
    return failures


def supersession_graph_errors(context: dict[str, Any]) -> list[str]:
    graph = {key: run.get("supersedesRunId") for key, run in context["runs"].items() if run.get("supersedesRunId")}
    for start in graph:
        seen: set[str] = set()
        current: str | None = start
        while current in graph:
            if current in seen:
                return ["EVALUATION_RUN_SUPERSESSION_CYCLE"]
            seen.add(current)
            current = graph[current]
    return []


def versioned_schema_metadata(path: Path) -> tuple[str, tuple[int, int, int]] | None:
    match = VERSIONED_SCHEMA_NAME.match(path.name)
    if not match:
        return None
    version = tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
    relative_parent = path.parent.relative_to(SCHEMAS).as_posix()
    if path.parent == SCHEMAS / "events":
        key = f"events/{match.group('base')}.v{version[0]}"
    else:
        key = f"{relative_parent}/{match.group('base')}"
    return key, version


def validate_schema_identity(path: Path, schema: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    metadata = versioned_schema_metadata(path)
    if not metadata:
        return failures
    key, version = metadata
    version_text = ".".join(map(str, version))
    if not str(schema.get("$id", "")).endswith(f"/{path.name}"):
        failures.append(f"versioned schema $id does not match filename: {path.relative_to(ROOT)}")
    if path.parent == SCHEMAS / "events":
        expected_event = key.split("/", 1)[1]
        if property_consts(schema, "eventType") != {expected_event}:
            failures.append(f"eventType does not match filename: {path.relative_to(ROOT)}")
        if property_consts(schema, "schemaVersion") != {version_text}:
            failures.append(f"schemaVersion does not match filename: {path.relative_to(ROOT)}")
    elif schema.get("type") == "object" and property_consts(schema, "schemaVersion") != {version_text}:
        failures.append(f"schemaVersion does not match filename: {path.relative_to(ROOT)}")
    return failures


def load_scenarios(
    loaded: dict[Path, Any], failures: list[str]
) -> tuple[dict[str, list[Any]], dict[Path, list[str]], dict[str, dict[str, Any]]]:
    scenarios: dict[str, list[Any]] = {}
    fixture_scenarios: dict[Path, list[str]] = {}
    scenario_specs: dict[str, dict[str, Any]] = {}
    for manifest_path in json_files(SCENARIOS):
        manifest = loaded.get(manifest_path)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("fixtures"), list):
            failures.append(f"invalid scenario manifest: {manifest_path.relative_to(ROOT)}")
            continue
        relative = manifest_path.relative_to(SCENARIOS)
        kind = relative.parts[0] if relative.parts else ""
        name = manifest.get("name")
        if kind not in {"valid", "invalid"} or not isinstance(name, str) or not name:
            failures.append(f"scenario must be under valid/ or invalid/ and declare name: {relative}")
            continue
        if name != manifest_path.parent.name:
            failures.append(f"scenario name must match directory: {relative}")
        if name in scenarios:
            failures.append(f"duplicate scenario name: {name}")
            continue
        if "validate" in manifest:
            failures.append(f"scenario {name}: validate is not allowed")

        expected = manifest.get("expectedSemanticErrors")
        if kind == "valid" and expected is not None:
            failures.append(f"valid scenario {name}: expectedSemanticErrors is not allowed")
        if kind == "invalid" and (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(code, str) and code for code in expected)
        ):
            failures.append(f"invalid scenario {name}: expectedSemanticErrors must be a non-empty string list")

        edges: set[tuple[str, str]] = set()
        raw_edges = manifest.get("causationEdges", [])
        if not isinstance(raw_edges, list):
            failures.append(f"scenario {name}: causationEdges must be a list")
            raw_edges = []
        for edge in raw_edges:
            if not isinstance(edge, dict) or not isinstance(edge.get("from"), str) or not isinstance(edge.get("to"), str):
                failures.append(f"scenario {name}: invalid causation edge")
                continue
            edges.add((edge["from"], edge["to"]))

        forbidden = manifest.get("forbiddenEventTypes", [])
        if not isinstance(forbidden, list) or not all(isinstance(item, str) for item in forbidden):
            failures.append(f"scenario {name}: forbiddenEventTypes must be a string list")
            forbidden = []
        instances: list[Any] = []
        for fixture_name in manifest["fixtures"]:
            fixture = (ROOT / fixture_name).resolve()
            if ROOT not in fixture.parents or fixture not in loaded:
                failures.append(f"scenario {name} references missing fixture: {fixture_name}")
                continue
            instances.append(loaded[fixture])
            fixture_scenarios.setdefault(fixture, []).append(name)
        scenarios[name] = instances
        scenario_specs[name] = {
            "kind": kind,
            "causation_edges": edges,
            "expected_errors": set(expected or []),
            "forbidden_event_types": set(forbidden),
        }
    return scenarios, fixture_scenarios, scenario_specs


def main() -> int:
    failures: list[str] = []
    invalid_paths = [path for path in json_files(INVALID) if path != INVALID_MANIFEST]
    valid_paths = json_files(VALID)
    all_json = json_files(SCHEMAS) + valid_paths + invalid_paths + [INVALID_MANIFEST] + json_files(SCENARIOS)
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
    versioned_schemas: dict[str, list[tuple[tuple[int, int, int], Path, dict[str, Any]]]] = {}
    for path in schema_paths:
        schema = loaded.get(path)
        if not isinstance(schema, dict):
            continue
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.resolve().as_uri(), resource)
        registry = registry.with_resource(schema["$id"], resource)
        failures.extend(validate_schema_identity(path, schema))
        metadata = versioned_schema_metadata(path)
        if metadata:
            key, version = metadata
            versioned_schemas.setdefault(key, []).append((version, path, schema))
        try:
            validators.validator_for(schema).check_schema(schema)
        except Exception as error:
            failures.append(f"invalid schema {path.relative_to(ROOT)}: {error}")
        for reference in nested_refs(schema):
            if reference.startswith("#"):
                continue
            if "://" in reference or reference.startswith("urn:"):
                failures.append(f"non-relative $ref in {path.relative_to(ROOT)}: {reference}")
                continue
            if not (path.parent / reference.split("#", 1)[0]).resolve().is_file():
                failures.append(f"unresolved $ref in {path.relative_to(ROOT)}: {reference}")

    valid_schema_paths: dict[Path, Path] = {}
    for example in valid_paths:
        schema_path = schema_for_example(example, VALID, loaded.get(example))
        valid_schema_paths[example] = schema_path
        if schema_path not in loaded:
            failures.append(f"missing schema for valid example {example.relative_to(ROOT)}")
            continue
        errors = errors_for(loaded[example], loaded[schema_path], registry)
        semantic = semantic_errors(loaded[example])
        if errors or semantic:
            detail = errors[0].message if errors else ", ".join(semantic)
            failures.append(f"valid example failed {example.relative_to(ROOT)}: {detail}")

    scenarios, fixture_scenarios, scenario_specs = load_scenarios(loaded, failures)
    scenario_contexts: dict[str, dict[str, Any]] = {}
    for name, instances in scenarios.items():
        spec = scenario_specs[name]
        context, context_failures = build_semantic_context(instances, spec["causation_edges"])
        scenario_contexts[name] = context
        scenario_errors = context_failures + supersession_graph_errors(context)
        for instance in instances:
            scenario_errors.extend(semantic_errors(instance, context))
            if isinstance(instance, dict) and instance.get("eventType") in spec["forbidden_event_types"]:
                scenario_errors.append(f"FORBIDDEN_EVENT_TYPE_PRESENT:{instance['eventType']}")
        actual_codes = {error.split(":", 1)[0] for error in scenario_errors}
        if spec["kind"] == "valid":
            failures.extend(f"scenario {name}: {failure}" for failure in scenario_errors)
        elif actual_codes != spec["expected_errors"]:
            failures.append(
                f"invalid scenario {name}: expected {sorted(spec['expected_errors'])}, got {sorted(actual_codes)}"
            )

    compatibility_checks = 0
    for example, schema_path in valid_schema_paths.items():
        source = versioned_schema_metadata(schema_path)
        if not source or schema_path not in loaded:
            continue
        key, source_version = source
        for target_version, target_path, target_schema in versioned_schemas.get(key, []):
            if target_version <= source_version or target_version[0] != source_version[0]:
                continue
            upgraded = deepcopy(loaded[example])
            if isinstance(upgraded, dict) and "schemaVersion" in upgraded:
                upgraded["schemaVersion"] = ".".join(map(str, target_version))
            schema_errors = errors_for(upgraded, target_schema, registry)
            semantic = semantic_errors(upgraded)
            for scenario_name in fixture_scenarios.get(example, []):
                scenario_instances = [upgraded if item is loaded[example] else item for item in scenarios[scenario_name]]
                spec = scenario_specs[scenario_name]
                upgraded_context, context_failures = build_semantic_context(
                    scenario_instances, spec["causation_edges"]
                )
                semantic.extend(context_failures + supersession_graph_errors(upgraded_context))
                semantic.extend(semantic_errors(upgraded, upgraded_context))
            compatibility_checks += 1
            if schema_errors or semantic:
                detail = schema_errors[0].message if schema_errors else ", ".join(semantic)
                failures.append(f"minor compatibility failed {example.relative_to(ROOT)} -> {target_path.relative_to(ROOT)}: {detail}")

    invalid_manifest = loaded.get(INVALID_MANIFEST, {})
    declarations = invalid_manifest.get("fixtures", []) if isinstance(invalid_manifest, dict) else []
    declared = {entry.get("fixture"): entry for entry in declarations if isinstance(entry, dict)}
    actual = {str(path.relative_to(INVALID)) for path in invalid_paths}
    if actual != set(declared):
        failures.append("invalid example inventory differs from examples/invalid/manifest.json")

    for example in invalid_paths:
        relative = str(example.relative_to(INVALID))
        declaration = declared.get(relative, {})
        schema_path = schema_for_example(example, INVALID, loaded.get(example))
        if schema_path not in loaded:
            failures.append(f"missing schema for invalid example {example.relative_to(ROOT)}")
            continue
        schema_errors = errors_for(loaded[example], loaded[schema_path], registry)
        scenario_name = declaration.get("scenario")
        context = scenario_contexts.get(scenario_name) if scenario_name else None
        semantic = semantic_errors(loaded[example], context)
        if context is not None:
            semantic.extend(supersession_graph_errors(context))
        if not schema_errors and not semantic:
            failures.append(f"invalid example unexpectedly passed {example.relative_to(ROOT)}")
            continue
        expected_type = declaration.get("expectedType")
        expected_code = declaration.get("expectedCode")
        matched_schema = any(
            error.validator == expected_type
            and expected_code in (error.message + "/" + "/".join(map(str, error.absolute_path)))
            for error in schema_errors
        )
        matched_semantic = expected_type == "semantic" and expected_code in semantic
        if not matched_schema and not matched_semantic:
            rendered = "; ".join(
                part for part in (
                    "; ".join(f"{error.validator}: {error.message}" for error in schema_errors),
                    ", ".join(semantic),
                ) if part
            )
            failures.append(f"invalid example failed for wrong reason {example.relative_to(ROOT)}: {rendered}")

    if failures:
        print("Contract validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        f"Validated {len(schema_paths)} schemas, {len(valid_paths)} valid examples, "
        f"{len(invalid_paths)} intentional invalid examples, {len(scenarios)} scenarios, "
        f"and {compatibility_checks} fixture-backed compatibility checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
