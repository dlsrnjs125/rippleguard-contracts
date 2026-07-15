#!/usr/bin/env python3
"""Validate all RippleGuard schemas and contract examples."""

from __future__ import annotations

import json
import sys
from collections import Counter
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
    "events/loan.application.submitted.v1--missing-event-id.json": ("required", "eventId"),
    "events/loan.application.submitted.v1--bad-date.json": ("pattern", "occurredAt"),
    "domain/loan-application-status--bad-enum.json": ("enum", "PENDING"),
    "agent-output/decision-envelope--bad-proposal.json": ("enum", "APPROVE"),
    "external-risk-signal/external-risk-signal--suspected-customer-scope.json": ("const", "subjectType"),
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


def schema_for_example(example: Path, example_root: Path) -> Path:
    relative = example.relative_to(example_root)
    contract_name = relative.name.split("--", 1)[0] if "--" in relative.name else relative.stem
    return SCHEMAS / relative.parent / f"{contract_name}.schema.json"


def errors_for(instance: Any, schema: dict[str, Any], registry: Registry) -> list[ValidationError]:
    validator_class = validators.validator_for(schema)
    validator = validator_class(schema, registry=registry, format_checker=FormatChecker())
    return sorted(validator.iter_errors(instance), key=lambda error: (list(error.absolute_path), error.message))


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
    for path in schema_paths:
        schema = loaded.get(path)
        if not isinstance(schema, dict):
            continue
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.resolve().as_uri(), resource)
        if schema.get("$id"):
            registry = registry.with_resource(schema["$id"], resource)

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

    for example in json_files(VALID):
        schema_path = schema_for_example(example, VALID)
        if schema_path not in loaded:
            failures.append(f"missing schema for valid example {example.relative_to(ROOT)}")
            continue
        errors = errors_for(loaded[example], loaded[schema_path], registry)
        if errors:
            failures.append(f"valid example failed {example.relative_to(ROOT)}: {errors[0].message}")

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
        if not errors:
            failures.append(f"invalid example unexpectedly passed {example.relative_to(ROOT)}")
            continue
        expected_validator, expected_text = EXPECTED_INVALID[relative]
        if not any(error.validator == expected_validator and expected_text in (error.message + "/" + "/".join(map(str, error.absolute_path))) for error in errors):
            rendered = "; ".join(f"{error.validator}: {error.message}" for error in errors)
            failures.append(f"invalid example failed for the wrong reason {example.relative_to(ROOT)}: {rendered}")

    if failures:
        print("Contract validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"Validated {len(schema_paths)} schemas, {len(json_files(VALID))} valid examples, and {len(json_files(INVALID))} intentional invalid examples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
