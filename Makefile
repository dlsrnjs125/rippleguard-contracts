.PHONY: validate

validate:
	python3 -m openapi_spec_validator openapi/phase-1-core-msa.v1.0.0.openapi.json
	python3 scripts/validate_contracts.py
