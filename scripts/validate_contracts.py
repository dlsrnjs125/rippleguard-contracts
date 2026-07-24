#!/usr/bin/env python3
"""Validate RippleGuard schemas, fixtures, scenarios, and compatibility."""

from __future__ import annotations

import json
import hashlib
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
OPENAPI = ROOT / "openapi"
VALID = ROOT / "examples" / "valid"
INVALID = ROOT / "examples" / "invalid"
SCENARIOS = ROOT / "examples" / "scenarios"
DIGEST_VECTORS = ROOT / "examples" / "digest-vectors"
INVALID_MANIFEST = INVALID / "manifest.json"
VERSIONED_SCHEMA_NAME = re.compile(
    r"^(?P<base>.+)\.v(?P<major>[1-9][0-9]*)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)\.schema\.json$"
)
VERSION_DIR = re.compile(r"v[1-9][0-9]*\.[0-9]+\.[0-9]+")
OPENAPI_NAME = re.compile(r"^.+\.v(?P<version>[1-9][0-9]*\.[0-9]+\.[0-9]+)\.openapi\.json$")
EVENT_ENVELOPE_FIELDS = {
    "eventId", "eventType", "schemaVersion", "occurredAt", "producer",
    "applicationId", "caseId", "evaluationRunId", "correlationId", "causationId", "payload",
}
PHASE2_FAILURE_CLASSIFICATIONS = {
    "FEATURE_SCHEMA_VERSION_UNSUPPORTED": {"VALIDATION_REQUIRED"},
    "FEATURE_REQUIRED_MISSING": {"VALIDATION_REQUIRED"},
    "FEATURE_UNKNOWN": {"VALIDATION_REQUIRED"},
    "FEATURE_TYPE_INVALID": {"VALIDATION_REQUIRED"},
    "FEATURE_VALUE_OUT_OF_RANGE": {"VALIDATION_REQUIRED"},
    "SNAPSHOT_NOT_FOUND": {"NON_RETRYABLE"},
    "SNAPSHOT_LOOKUP_TEMPORARY_FAILURE": {"RETRYABLE"},
    "SNAPSHOT_DIGEST_MISMATCH": {"BLOCKED"},
    "SNAPSHOT_SCHEMA_UNSUPPORTED": {"VALIDATION_REQUIRED"},
    "MODEL_MANIFEST_NOT_FOUND": {"BLOCKED"},
    "MODEL_ARTIFACT_NOT_FOUND": {"BLOCKED"},
    "MODEL_ARTIFACT_DIGEST_MISMATCH": {"BLOCKED"},
    "MODEL_VERSION_UNSUPPORTED": {"VALIDATION_REQUIRED"},
    "AGENT_TIMEOUT": {"RETRYABLE", "VALIDATION_REQUIRED"},
    "AGENT_RUNTIME_TEMPORARY_FAILURE": {"RETRYABLE"},
    "RETRY_EXHAUSTED": {"VALIDATION_REQUIRED"},
    "DUPLICATE_REQUEST": {"NON_RETRYABLE"},
    "AGENT_RUN_INPUT_CONFLICT": {"BLOCKED"},
    "AGENT_RUN_RESULT_CONFLICT": {"BLOCKED"},
    "SHAP_CALCULATION_FAILED": {"VALIDATION_REQUIRED"},
    "CONTRACT_VALIDATION_FAILED": {"VALIDATION_REQUIRED"},
    "AUDIT_PUBLICATION_FAILED": {"RETRYABLE", "BLOCKED"},
}


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


def validate_openapi(path: Path, document: Any, loaded: dict[Path, Any]) -> list[str]:
    failures: list[str] = []
    relative = path.relative_to(ROOT)
    name_match = OPENAPI_NAME.fullmatch(path.name)
    if not name_match:
        failures.append(f"OpenAPI filename must include full version: {relative}")
    if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3.1."):
        return failures + [f"OpenAPI 3.1 document required: {relative}"]
    if not isinstance(document.get("info"), dict) or not document["info"].get("title") or not document["info"].get("version"):
        failures.append(f"OpenAPI info.title and info.version required: {relative}")
    elif name_match and document["info"]["version"] != name_match.group("version"):
        failures.append(f"OpenAPI info.version does not match filename: {relative}")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return failures + [f"OpenAPI paths object required: {relative}"]
    required_operations = {
        ("/api/v1/loan-applications", "post"),
        ("/api/v1/loan-applications/{applicationId}", "get"),
        ("/api/v1/cases/{caseId}/timeline", "get"),
    }
    for endpoint, method in required_operations:
        operation = paths.get(endpoint, {}).get(method)
        if not isinstance(operation, dict):
            failures.append(f"missing OpenAPI operation {method.upper()} {endpoint}")
        elif not isinstance(operation.get("responses"), dict) or not operation["responses"]:
            failures.append(f"OpenAPI operation has no responses: {method.upper()} {endpoint}")
    for reference in nested_refs(document):
        if reference.startswith("#"):
            current: Any = document
            try:
                for token in reference[2:].split("/") if reference.startswith("#/") else []:
                    current = current[token.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError):
                failures.append(f"unresolved OpenAPI local $ref {reference} in {relative}")
            continue
        target_text = reference.split("#", 1)[0]
        target = (path.parent / target_text).resolve()
        if target not in loaded:
            failures.append(f"unresolved OpenAPI relative $ref {reference} in {relative}")
    return failures


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
    if contract_name == "phase-1-event-envelope-profile":
        return SCHEMAS / "common" / "phase-1-event-envelope-profile.v1.0.0.schema.json"
    if contract_name == "phase-1-loan-decision-command-profile":
        return SCHEMAS / "commands" / "phase-1-loan-decision-command-profile.v1.0.0.schema.json"
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


def phase2_result_fingerprint(result: dict[str, Any]) -> str:
    proposal = result.get("proposal", {})
    canonical_proposal = {
        "proposalOutcome": proposal.get("proposalOutcome"),
        "repaymentLikelihoodScore": proposal.get("repaymentLikelihoodScore"),
        "threshold": proposal.get("threshold"),
        "thresholdVersion": proposal.get("thresholdVersion"),
        "comparisonDirection": proposal.get("comparisonDirection"),
        "reasonCodes": sorted(proposal.get("reasonCodes", [])),
        "modelVersion": proposal.get("modelVersion"),
        "featureSchemaVersion": proposal.get("featureSchemaVersion"),
    } if isinstance(proposal, dict) else None
    canonical = {
        "snapshotDigest": result.get("snapshotReference", {}).get("snapshotDigest"),
        "featureSchemaVersion": result.get("featureSchemaVersion"),
        "preprocessingVersion": result.get("preprocessingVersion"),
        "modelVersion": result.get("modelVersion"),
        "modelArtifactDigest": result.get("modelArtifactDigest"),
        "thresholdVersion": result.get("thresholdVersion"),
        "proposal": canonical_proposal,
        "explanationDigest": result.get("explanationDigest"),
        "evidenceRefs": sorted(result.get("evidenceRefs", [])),
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def canonical_json_digest(value: Any) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def phase2_runtime_image_digest_errors(manifest: dict[str, Any]) -> list[str]:
    state = manifest.get("manifestPublicationState", "PUBLISHED")
    digest = manifest.get("runtimeImageDigest")
    if state == "TEMPLATE":
        return []
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return []
    hex_value = digest.removeprefix("sha256:")
    failures: list[str] = []
    if len(hex_value) == 64 and len(set(hex_value)) == 1:
        failures.append("PHASE2_RUNTIME_IMAGE_DIGEST_PLACEHOLDER")
    source_commit = manifest.get("trainingCodeCommit")
    if isinstance(source_commit, str) and source_commit and source_commit in hex_value:
        failures.append("PHASE2_RUNTIME_IMAGE_DIGEST_SOURCE_COMMIT")
    return failures


def phase2_request_immutable(request: dict[str, Any]) -> tuple[Any, ...]:
    return (
        request.get("decisionCaseId"),
        request.get("evaluationRunId"),
        request.get("agentType"),
        request.get("snapshotReference", {}).get("snapshotDigest"),
        request.get("featureSchemaVersion"),
        request.get("preprocessingVersion"),
        request.get("modelVersion"),
        request.get("modelArtifactDigest"),
        request.get("thresholdVersion"),
    )


def feature_schema_projection_errors(feature_schema: Any, payload_schema: Any) -> list[str]:
    if not isinstance(feature_schema, dict) or not isinstance(payload_schema, dict):
        return []
    payload_features = (
        payload_schema.get("properties", {})
        .get("features", {})
        .get("properties", {})
    )
    payload_required = set(
        payload_schema.get("properties", {})
        .get("features", {})
        .get("required", [])
    )
    if not isinstance(payload_features, dict):
        return ["PHASE2_FEATURE_PAYLOAD_SCHEMA_MISSING_FEATURES"]
    failures: list[str] = []
    for feature in feature_schema.get("features", []):
        if not isinstance(feature, dict):
            continue
        name = feature.get("name")
        payload = payload_features.get(name)
        if not isinstance(payload, dict):
            failures.append(f"PHASE2_FEATURE_PAYLOAD_PROJECTION_MISSING:{name}")
            continue
        expected_type = feature.get("valueType")
        if payload.get("type") != expected_type:
            failures.append(f"PHASE2_FEATURE_PAYLOAD_TYPE_DRIFT:{name}")
        for bound in ("minimum", "maximum"):
            if bound in feature and payload.get(bound) != feature.get(bound):
                failures.append(f"PHASE2_FEATURE_PAYLOAD_{bound.upper()}_DRIFT:{name}")
        if feature.get("required") is True and name not in payload_required:
            failures.append(f"PHASE2_FEATURE_PAYLOAD_REQUIRED_DRIFT:{name}")
    ordered = feature_schema.get("featureOrder", [])
    if set(ordered) != set(payload_features):
        failures.append("PHASE2_FEATURE_PAYLOAD_FEATURE_SET_DRIFT")
    return failures


def digest_vector_errors() -> list[str]:
    failures: list[str] = []
    if not DIGEST_VECTORS.exists():
        return failures
    for vector_dir in sorted(path for path in DIGEST_VECTORS.iterdir() if path.is_dir()):
        input_path = vector_dir / "agent-result-input.json"
        canonical_path = vector_dir / "canonical-agent-result.json"
        expected_path = vector_dir / "expected-sha256.txt"
        missing = [path.name for path in (input_path, canonical_path, expected_path) if not path.is_file()]
        if missing:
            failures.append(f"digest vector {vector_dir.name} missing files: {', '.join(missing)}")
            continue
        try:
            value = load_json(input_path)
            canonical = canonical_json(value)
            expected_canonical = canonical_path.read_text(encoding="utf-8").rstrip("\n")
            expected_digest = expected_path.read_text(encoding="utf-8").strip()
        except RuntimeError as error:
            failures.append(str(error))
            continue
        if canonical != expected_canonical:
            failures.append(f"digest vector canonical mismatch: {vector_dir.relative_to(ROOT)}")
        if canonical_json_digest(value) != expected_digest:
            failures.append(f"digest vector sha256 mismatch: {vector_dir.relative_to(ROOT)}")
    return failures


def build_semantic_context(
    instances: list[Any], causation_edges: set[tuple[str, str]] | None = None
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    context: dict[str, Any] = {
        "events": {}, "decisions": {}, "commands": {}, "runs": {},
        "risk_signals": {}, "evidence_requests": {},
        "phase2_requests": {}, "phase2_results": {}, "phase2_manifests": {},
        "phase2_agent_result_audit_events": {}, "phase2_agent_run_ids": set(),
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
        if instance.get("modelType") == "TABULAR" and instance.get("modelVersion"):
            register_unique(context["phase2_manifests"], instance.get("modelVersion"), instance, "DUPLICATE_PHASE2_MODEL_MANIFEST", failures)
        if instance.get("agentType") == "LOAN_DECISION_AGENT" and instance.get("snapshotReference"):
            context["phase2_requests"].setdefault(instance.get("agentRunId"), []).append(instance)
            if instance.get("agentRunId"):
                context["phase2_agent_run_ids"].add(instance.get("agentRunId"))
        if instance.get("resultStatus") in {"COMPLETED", "FAILED"} and isinstance(instance.get("agentRun"), dict):
            context["phase2_results"].setdefault(instance["agentRun"].get("agentRunId"), []).append(instance)
            agent_run_id = instance["agentRun"].get("agentRunId")
            if agent_run_id:
                context["phase2_agent_run_ids"].add(agent_run_id)
        if instance.get("eventType") in {"governance.agent-result.validated.v1", "governance.agent-result.validated.v2"}:
            register_unique(
                context["phase2_agent_result_audit_events"],
                f"{payload.get('agentRunId')}:{payload.get('attemptId')}",
                instance,
                "DUPLICATE_PHASE2_AGENT_RESULT_AUDIT_EVENT",
                failures,
            )
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

    if "applicantReference" in instance and isinstance(instance.get("incomeHistory"), list):
        periods = [item.get("period") for item in instance["incomeHistory"] if isinstance(item, dict)]
        if len(periods) != len(set(periods)):
            failures.append("LOAN_APPLICATION_DUPLICATE_INCOME_PERIOD")

    if {"applicationId", "status", "createdAt", "updatedAt"}.issubset(instance):
        try:
            if parse_timestamp(instance["updatedAt"]) < parse_timestamp(instance["createdAt"]):
                failures.append("LOAN_APPLICATION_UPDATED_BEFORE_CREATED")
        except (TypeError, ValueError):
            pass

    if instance.get("schemaVersion") == "2.0.0" and instance.get("componentVersions"):
        terminal = instance.get("status") in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}
        completed_at = instance.get("completedAt")
        if terminal and not completed_at:
            failures.append("EVALUATION_TERMINAL_WITHOUT_COMPLETED_AT")
        if not terminal and completed_at is not None:
            failures.append("EVALUATION_NON_TERMINAL_WITH_COMPLETED_AT")
        if completed_at:
            try:
                if parse_timestamp(completed_at) < parse_timestamp(instance["createdAt"]):
                    failures.append("EVALUATION_COMPLETED_BEFORE_CREATED")
            except (KeyError, TypeError, ValueError):
                pass

    if isinstance(instance.get("events"), list) and "traceCompleteness" in instance:
        timeline_events = instance["events"]
        identifiers = [item.get("eventId") for item in timeline_events if isinstance(item, dict)]
        if len(identifiers) != len(set(identifiers)):
            failures.append("TIMELINE_DUPLICATE_EVENT_ID")
        positions = {event_id: index for index, event_id in enumerate(identifiers) if event_id}
        previous_time: datetime | None = None
        for index, item in enumerate(timeline_events):
            if not isinstance(item, dict):
                continue
            try:
                occurred_at = parse_timestamp(item["occurredAt"])
                if previous_time is not None and occurred_at < previous_time:
                    failures.append("TIMELINE_TIME_ORDER_INVALID")
                previous_time = occurred_at
            except (KeyError, TypeError, ValueError):
                pass
            cause = item.get("causationId")
            if cause is not None:
                if cause not in positions:
                    failures.append("TIMELINE_CAUSATION_NOT_FOUND")
                elif positions[cause] >= index:
                    failures.append("TIMELINE_CAUSATION_NOT_PRECEDING")
            if item.get("correlationId") != instance.get("applicationId"):
                failures.append("TIMELINE_CORRELATION_MISMATCH")
            if item.get("caseId") != instance.get("caseId"):
                failures.append("TIMELINE_CASE_MISMATCH")
        if instance.get("traceCompleteness") == "COMPLETE" and any(
            item.get("status") == "INVALID_REFERENCE" for item in timeline_events if isinstance(item, dict)
        ):
            failures.append("TIMELINE_COMPLETE_WITH_INVALID_EVENT")
        if instance.get("traceCompleteness") in {"PARTIAL", "UNKNOWN"} and not instance.get("warnings"):
            failures.append("TIMELINE_PARTIAL_WITHOUT_WARNING")

    if event_type == "loan.application.submitted.v1" and instance.get("caseId") != payload.get("applicationId"):
        failures.append("EVENT_CASE_APPLICATION_MISMATCH")
    if event_type and payload.get("decisionCaseId") and instance.get("caseId") != payload.get("decisionCaseId"):
        failures.append("EVENT_CASE_DECISION_MISMATCH")
    if event_type and payload.get("applicationId") and instance.get("correlationId") != payload.get("applicationId"):
        failures.append("EVENT_APPLICATION_CORRELATION_MISMATCH")
    if event_type and payload.get("applicationId") and instance.get("applicationId") is not None and instance.get("applicationId") != payload.get("applicationId"):
        failures.append("EVENT_ENVELOPE_APPLICATION_MISMATCH")
    if event_type and payload.get("evaluationRunId") and instance.get("evaluationRunId") is not None and instance.get("evaluationRunId") != payload.get("evaluationRunId"):
        failures.append("EVENT_ENVELOPE_EVALUATION_RUN_MISMATCH")

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

    if instance.get("schemaVersion") == "1.0.0" and "featureOrder" in instance and "features" in instance:
        ordered = instance.get("featureOrder", [])
        feature_names = [item.get("name") for item in instance.get("features", []) if isinstance(item, dict)]
        if ordered != feature_names:
            failures.append("PHASE2_FEATURE_ORDER_MISMATCH")

    if instance.get("agentType") == "LOAN_DECISION_AGENT" and instance.get("snapshotReference"):
        feature_payload = instance.get("featurePayload")
        try:
            if parse_timestamp(instance["deadlineAt"]) <= parse_timestamp(instance["requestedAt"]):
                failures.append("PHASE2_REQUEST_DEADLINE_NOT_AFTER_REQUEST")
            if parse_timestamp(instance["snapshotReference"]["snapshotCreatedAt"]) > parse_timestamp(instance["requestedAt"]):
                failures.append("PHASE2_REQUEST_SNAPSHOT_CREATED_AFTER_REQUEST")
        except (KeyError, TypeError, ValueError):
            pass
        if isinstance(feature_payload, dict) and instance.get("featureSchemaVersion") != feature_payload.get("featureSchemaVersion"):
            failures.append("PHASE2_REQUEST_FEATURE_SCHEMA_MISMATCH")
        reference_type = instance.get("snapshotReference", {}).get("referenceType")
        if reference_type == "MATERIALIZED_FEATURES" and not isinstance(feature_payload, dict):
            failures.append("PHASE2_REQUEST_MATERIALIZED_FEATURE_PAYLOAD_MISSING")
        if reference_type == "IMMUTABLE_REFERENCE" and isinstance(feature_payload, dict):
            failures.append("PHASE2_REQUEST_IMMUTABLE_REFERENCE_WITH_FEATURE_PAYLOAD")

    if instance.get("resultStatus") in {"COMPLETED", "FAILED"}:
        agent_run = instance.get("agentRun", {})
        if agent_run.get("completedAt") and instance.get("completedAt"):
            try:
                agent_completed = parse_timestamp(agent_run["completedAt"])
                result_completed = parse_timestamp(instance["completedAt"])
                agent_started = parse_timestamp(agent_run["startedAt"])
                if agent_run["completedAt"] != instance["completedAt"]:
                    failures.append("PHASE2_AGENT_RUN_COMPLETED_AT_MISMATCH")
                if agent_started > agent_completed:
                    failures.append("PHASE2_AGENT_RUN_TIME_ORDER_INVALID")
                if agent_completed > result_completed:
                    failures.append("PHASE2_RESULT_COMPLETED_BEFORE_AGENT_COMPLETED")
            except (KeyError, TypeError, ValueError):
                pass
        if agent_run.get("decisionCaseId") and agent_run.get("decisionCaseId") != instance.get("snapshotReference", {}).get("decisionCaseId", agent_run.get("decisionCaseId")):
            pass
        if instance.get("resultStatus") == "COMPLETED":
            proposal = instance.get("proposal", {})
            if isinstance(proposal, dict) and proposal:
                compared = ("modelVersion", "featureSchemaVersion", "thresholdVersion")
                for field in compared:
                    if proposal.get(field) != instance.get(field):
                        failures.append(f"PHASE2_COMPLETED_PROPOSAL_{field.upper()}_MISMATCH")
            if not instance.get("explanationRef") or not instance.get("explanationDigest"):
                failures.append("PHASE2_COMPLETED_RESULT_MISSING_EXPLANATION")
        if instance.get("resultStatus") == "FAILED":
            failure = instance.get("failure", {})
            allowed = PHASE2_FAILURE_CLASSIFICATIONS.get(failure.get("reasonCode"))
            if allowed is None:
                failures.append("PHASE2_FAILURE_REASON_UNKNOWN")
            elif failure.get("classification") not in allowed:
                failures.append("PHASE2_FAILURE_CLASSIFICATION_MISMATCH")

    if instance.get("schemaVersion") == "1.0.0" and "result" in instance and {"agentRunId", "attemptId"}.issubset(instance):
        result = instance.get("result", {})
        agent_run = result.get("agentRun", {}) if isinstance(result, dict) else {}
        for field in ("decisionCaseId", "evaluationRunId", "agentRunId", "attemptId", "requestIdempotencyKey"):
            if instance.get(field) != agent_run.get(field):
                failures.append(f"PHASE2_DECISION_ENVELOPE_{field.upper()}_MISMATCH")

    if event_type in {"governance.agent-result.validated.v1", "governance.agent-result.validated.v2"}:
        if instance.get("producer") != "governance-service":
            failures.append("PHASE2_AUDIT_EVENT_PRODUCER_NOT_GOVERNANCE")
        reason_codes = set(payload.get("validationReasonCodes", []))
        valid_codes = {"SCHEMA_VALID", "MODEL_PROVENANCE_VALID", "SNAPSHOT_MATCHED", "SHAP_PRESENT"}
        rejected_codes = {"SCHEMA_INVALID", "MODEL_PROVENANCE_INVALID", "SNAPSHOT_MISMATCH", "SHAP_MISSING", "AGENT_FAILURE_RECORDED"}
        contradictory_pairs = (
            ("SCHEMA_VALID", "SCHEMA_INVALID"),
            ("MODEL_PROVENANCE_VALID", "MODEL_PROVENANCE_INVALID"),
            ("SNAPSHOT_MATCHED", "SNAPSHOT_MISMATCH"),
            ("SHAP_PRESENT", "SHAP_MISSING"),
        )
        for positive, negative in contradictory_pairs:
            if positive in reason_codes and negative in reason_codes:
                failures.append("PHASE2_AUDIT_REASON_CONTRADICTION")
        if payload.get("validationOutcome") == "VALIDATED":
            missing = valid_codes - reason_codes
            if missing:
                failures.append("PHASE2_AUDIT_VALIDATED_REASON_SET_INCOMPLETE")
            if reason_codes & rejected_codes:
                failures.append("PHASE2_AUDIT_VALIDATED_WITH_REJECTION_REASON")
        if payload.get("validationOutcome") == "REJECTED":
            if not reason_codes & rejected_codes:
                failures.append("PHASE2_AUDIT_REJECTED_WITHOUT_REJECTION_REASON")
            if valid_codes.issubset(reason_codes):
                failures.append("PHASE2_AUDIT_REJECTED_WITH_FULL_VALID_REASON_SET")
        if payload.get("agentResultReference") is not None:
            expected_reference = (
                f"agent-result://{payload.get('decisionCaseId')}/"
                f"{payload.get('agentRunId')}/attempt-{payload.get('attemptId')}"
            )
            if payload.get("agentResultReference") != expected_reference:
                failures.append("PHASE2_AUDIT_RESULT_REFERENCE_MISMATCH")
        if event_type == "governance.agent-result.validated.v2":
            if payload.get("requestEventId") != instance.get("causationId"):
                failures.append("PHASE2_AUDIT_REQUEST_EVENT_CAUSATION_MISMATCH")
            if instance.get("causationId") == payload.get("agentRunId"):
                failures.append("PHASE2_AUDIT_CAUSATION_USES_AGENT_RUN_ID")

    if instance.get("modelType") == "TABULAR":
        failures.extend(phase2_runtime_image_digest_errors(instance))

    if context is None:
        return failures

    if event_type and instance.get("causationId") is not None:
        if instance.get("causationId") == instance.get("eventId"):
            failures.append("CAUSATION_SELF_REFERENCE")
        else:
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
                if event_type in {"governance.agent-result.validated.v1", "governance.agent-result.validated.v2"}:
                    if cause.get("eventType") != "agent.evaluation.requested.v1":
                        failures.append("PHASE2_AUDIT_CAUSATION_EVENT_TYPE_INVALID")
                    if (
                        cause.get("evaluationRunId") != instance.get("evaluationRunId")
                        or cause_payload.get("evaluationRunId") != payload.get("evaluationRunId")
                    ):
                        failures.append("PHASE2_AUDIT_CAUSATION_EVALUATION_RUN_MISMATCH")
                    if (
                        cause.get("caseId") != instance.get("caseId")
                        or cause_payload.get("decisionCaseId") != payload.get("decisionCaseId")
                    ):
                        failures.append("PHASE2_AUDIT_CAUSATION_CASE_MISMATCH")

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

    if instance.get("agentType") == "LOAN_DECISION_AGENT" and instance.get("snapshotReference"):
        manifest = context["phase2_manifests"].get(instance.get("modelVersion"))
        if manifest is not None:
            if manifest.get("featureSchemaVersion") != instance.get("featureSchemaVersion"):
                failures.append("PHASE2_REQUEST_MANIFEST_FEATURE_SCHEMA_MISMATCH")
            if manifest.get("preprocessingVersion") != instance.get("preprocessingVersion"):
                failures.append("PHASE2_REQUEST_MANIFEST_PREPROCESSING_VERSION_MISMATCH")
            if manifest.get("modelBinaryArtifactDigest") != instance.get("modelArtifactDigest"):
                failures.append("PHASE2_REQUEST_MANIFEST_ARTIFACT_DIGEST_MISMATCH")
            if manifest.get("thresholdVersion") != instance.get("thresholdVersion"):
                failures.append("PHASE2_REQUEST_MANIFEST_THRESHOLD_MISMATCH")

    if instance.get("resultStatus") in {"COMPLETED", "FAILED"}:
        agent_run = instance.get("agentRun", {})
        requests = context["phase2_requests"].get(agent_run.get("agentRunId"), [])
        request = requests[0] if requests else None
        if request is not None:
            immutable_fields = ("decisionCaseId", "evaluationRunId", "requestIdempotencyKey", "featureSchemaVersion", "preprocessingVersion", "modelVersion", "modelArtifactDigest", "thresholdVersion")
            for field in immutable_fields:
                expected = request.get(field) if field != "requestIdempotencyKey" else request.get("requestIdempotencyKey")
                actual = agent_run.get(field) if field in {"decisionCaseId", "evaluationRunId", "requestIdempotencyKey"} else instance.get(field)
                if expected != actual:
                    failures.append(f"PHASE2_AGENT_RUN_{field.upper()}_MISMATCH")
            if request.get("snapshotReference", {}).get("snapshotDigest") != instance.get("snapshotReference", {}).get("snapshotDigest"):
                failures.append("PHASE2_AGENT_RUN_SNAPSHOT_DIGEST_MISMATCH")
        manifest = context["phase2_manifests"].get(instance.get("modelVersion"))
        if manifest is not None:
            if manifest.get("modelBinaryArtifactDigest") != instance.get("modelArtifactDigest"):
                failures.append("PHASE2_RESULT_MANIFEST_ARTIFACT_DIGEST_MISMATCH")
            if manifest.get("preprocessingVersion") != instance.get("preprocessingVersion"):
                failures.append("PHASE2_RESULT_MANIFEST_PREPROCESSING_VERSION_MISMATCH")

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


def phase2_context_errors(context: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    requests_by_key: dict[str, Any] = {}
    for agent_run_id, requests in context.get("phase2_requests", {}).items():
        immutable_by_agent_run = {phase2_request_immutable(request) for request in requests}
        if len(immutable_by_agent_run) > 1:
            failures.append("PHASE2_AGENT_RUN_INPUT_CONFLICT")
        for request in requests:
            key = request.get("requestIdempotencyKey")
            if not key:
                continue
            immutable = phase2_request_immutable(request)
            previous = requests_by_key.get(key)
            if previous is None:
                requests_by_key[key] = immutable
            elif previous != immutable:
                failures.append("PHASE2_IDEMPOTENCY_KEY_INPUT_CONFLICT")

    for agent_run_id, results in context.get("phase2_results", {}).items():
        if not agent_run_id or not results:
            continue
        immutable_values = {
            (
                result.get("snapshotReference", {}).get("snapshotDigest"),
                result.get("featureSchemaVersion"),
                result.get("preprocessingVersion"),
                result.get("modelVersion"),
                result.get("modelArtifactDigest"),
                result.get("thresholdVersion"),
            )
            for result in results
        }
        if len(immutable_values) > 1:
            failures.append("PHASE2_AGENT_RUN_INPUT_CONFLICT")
        completed_fingerprints = {
            phase2_result_fingerprint(result)
            for result in results
            if result.get("resultStatus") == "COMPLETED"
        }
        if len(completed_fingerprints) > 1:
            failures.append("PHASE2_AGENT_RUN_RESULT_CONFLICT")

        attempts: list[tuple[int, datetime, datetime]] = []
        seen_attempts: dict[int, str] = {}
        for result in results:
            agent_run = result.get("agentRun", {})
            attempt_id = agent_run.get("attemptId")
            if not isinstance(attempt_id, int):
                continue
            fingerprint = phase2_result_fingerprint(result)
            if attempt_id in seen_attempts and seen_attempts[attempt_id] != fingerprint:
                failures.append("PHASE2_ATTEMPT_ID_DUPLICATE")
            if attempt_id not in seen_attempts:
                seen_attempts[attempt_id] = fingerprint
            else:
                continue
            try:
                attempts.append((attempt_id, parse_timestamp(agent_run["startedAt"]), parse_timestamp(agent_run["completedAt"])))
            except (KeyError, TypeError, ValueError):
                pass
        attempts.sort(key=lambda item: item[1])
        for previous, current in zip(attempts, attempts[1:]):
            if current[0] <= previous[0]:
                failures.append("PHASE2_ATTEMPT_ORDER_INVALID")
            if current[1] < previous[2]:
                failures.append("PHASE2_ATTEMPT_TIME_ORDER_INVALID")

    for event in context.get("phase2_agent_result_audit_events", {}).values():
        payload = event.get("payload", {})
        if event.get("causationId") in context.get("phase2_agent_run_ids", set()):
            failures.append("PHASE2_AUDIT_CAUSATION_USES_AGENT_RUN_ID")
        if payload.get("agentResultReference") is None and payload.get("agentResultDigest") is None:
            continue
        matching_result: dict[str, Any] | None = None
        for result in context.get("phase2_results", {}).get(payload.get("agentRunId"), []):
            if result.get("agentRun", {}).get("attemptId") == payload.get("attemptId"):
                matching_result = result
                break
        if matching_result is None:
            failures.append("PHASE2_AUDIT_RESULT_REFERENCE_NOT_FOUND")
        elif payload.get("agentResultDigest") != canonical_json_digest(matching_result):
            failures.append("PHASE2_AUDIT_RESULT_DIGEST_MISMATCH")
    return failures


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
    elif (
        path.parent != SCHEMAS / "commands"
        and path.name != "phase-1-event-envelope-profile.v1.0.0.schema.json"
        and schema.get("type") == "object"
        and property_consts(schema, "schemaVersion") != {version_text}
    ):
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
    openapi_paths = json_files(OPENAPI)
    all_json = json_files(SCHEMAS) + openapi_paths + valid_paths + invalid_paths + [INVALID_MANIFEST] + json_files(SCENARIOS)
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

    envelope_path = SCHEMAS / "common" / "event-envelope.schema.json"
    envelope = loaded.get(envelope_path, {})
    envelope_properties = set(envelope.get("properties", {})) if isinstance(envelope, dict) else set()
    if not EVENT_ENVELOPE_FIELDS.issubset(envelope_properties):
        failures.append(
            "event envelope is missing fields: "
            + ", ".join(sorted(EVENT_ENVELOPE_FIELDS - envelope_properties))
        )

    for path in openapi_paths:
        failures.extend(validate_openapi(path, loaded.get(path), loaded))

    failures.extend(digest_vector_errors())

    failures.extend(feature_schema_projection_errors(
        loaded.get(VALID / "domain" / "v1.0.0" / "feature-schema.json"),
        loaded.get(SCHEMAS / "domain" / "feature-payload.v1.0.0.schema.json"),
    ))

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
    command_profile_path = SCHEMAS / "commands" / "phase-1-loan-decision-command-profile.v1.0.0.schema.json"
    for example in valid_paths:
        schema_path = schema_for_example(example, VALID, loaded.get(example))
        valid_schema_paths[example] = schema_path
        if schema_path not in loaded:
            failures.append(f"missing schema for valid example {example.relative_to(ROOT)}")
            continue
        errors = errors_for(loaded[example], loaded[schema_path], registry)
        if schema_path.name in {
            "loan-decision-command.v1.0.0.schema.json",
            "phase-1-loan-decision-command-profile.v1.0.0.schema.json",
        }:
            errors.extend(errors_for(loaded[example], loaded[command_profile_path], registry))
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
        scenario_errors = context_failures + supersession_graph_errors(context) + phase2_context_errors(context)
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
                semantic.extend(context_failures + supersession_graph_errors(upgraded_context) + phase2_context_errors(upgraded_context))
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
            semantic.extend(supersession_graph_errors(context) + phase2_context_errors(context))
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
        f"Validated {len(schema_paths)} schemas, {len(openapi_paths)} OpenAPI documents, {len(valid_paths)} valid examples, "
        f"{len(invalid_paths)} intentional invalid examples, {len(scenarios)} scenarios, "
        f"and {compatibility_checks} fixture-backed compatibility checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
