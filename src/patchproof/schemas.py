"""Strict JSON Schemas for MCP tool inputs/outputs and for model output."""

from __future__ import annotations

import jsonschema

from .config import MAX_HYPOTHESES, MAX_TRACEBACK_BYTES

_REPO_ROOT = {
    "type": "string",
    "minLength": 1,
    "maxLength": 4096,
    "description": "Absolute path to the repository root on this machine. Required; never inferred.",
}

ANALYZE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"repository_root": _REPO_ROOT},
    "required": ["repository_root"],
    "additionalProperties": False,
}

DIAGNOSE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "repository_root": _REPO_ROOT,
        "traceback": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_TRACEBACK_BYTES,
            "description": "Full standard Python traceback text. Rejected (not truncated) if over 64 KiB.",
        },
    },
    "required": ["repository_root", "traceback"],
    "additionalProperties": False,
}

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_INT = {"type": ["integer", "null"]}
_CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low"]}
_EVIDENCE_ID = {"type": "string", "pattern": "^E[0-9]{1,3}$"}

_COVERAGE = {
    "type": "object",
    "required": ["partial", "files_indexed", "bytes_indexed", "notes"],
    "properties": {
        "partial": {"type": "boolean"},
        "files_indexed": {"type": "integer"},
        "bytes_indexed": {"type": "integer"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

ANALYZE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["tool", "schema_version", "repository_root", "coverage", "graph", "modules", "tests",
                 "limits", "notices"],
    "additionalProperties": False,
    "properties": {
        "tool": {"const": "analyze_repository"},
        "schema_version": {"type": "string"},
        "repository_root": {"type": "string"},
        "coverage": _COVERAGE,
        "graph": {
            "type": "object",
            "required": ["modules", "classes", "functions", "methods", "edges", "unresolved_calls",
                         "test_files", "test_functions"],
        },
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["module", "file", "symbols", "is_test"],
                "additionalProperties": False,
                "properties": {
                    "module": {"type": "string"},
                    "file": {"type": "string"},
                    "symbols": {"type": "integer"},
                    "is_test": {"type": "boolean"},
                },
            },
        },
        "tests": {
            "type": "object",
            "required": ["discovered", "listed", "items"],
            "additionalProperties": False,
            "properties": {
                "discovered": {"type": "integer"},
                "listed": {"type": "integer"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["symbol", "file", "line"],
                        "additionalProperties": False,
                        "properties": {"symbol": {"type": "string"}, "file": {"type": "string"},
                                       "line": {"type": "integer"}},
                    },
                },
            },
        },
        "limits": {"type": "object"},
        "notices": {"type": "array", "items": {"type": "string"}},
    },
}

_HYPOTHESIS_OUT = {
    "type": "object",
    "required": ["rank", "title", "explanation", "confidence", "evidence_ids", "locations", "source"],
    "additionalProperties": False,
    "properties": {
        "rank": {"type": "integer", "minimum": 1},
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "confidence": _CONFIDENCE,
        "evidence_ids": {"type": "array", "minItems": 1, "items": _EVIDENCE_ID},
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["evidence_id", "file", "symbol", "line_start", "line_end"],
                "additionalProperties": False,
                "properties": {
                    "evidence_id": _EVIDENCE_ID,
                    "file": _NULLABLE_STR,
                    "symbol": _NULLABLE_STR,
                    "line_start": _NULLABLE_INT,
                    "line_end": _NULLABLE_INT,
                },
            },
        },
        "source": {"const": "model_hypothesis"},
    },
}

DIAGNOSE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["tool", "schema_version", "status", "summary", "hypotheses", "facts", "evidence",
                 "related_tests", "missing_information", "verification", "coverage", "privacy", "model",
                 "notices"],
    "additionalProperties": False,
    "properties": {
        "tool": {"const": "diagnose_failure"},
        "schema_version": {"type": "string"},
        "status": {"type": "string", "enum": ["diagnosed", "inconclusive"]},
        "summary": {"type": "string"},
        "hypotheses": {"type": "array", "maxItems": MAX_HYPOTHESES, "items": _HYPOTHESIS_OUT},
        "facts": {
            "type": "object",
            "required": ["source", "exception", "frames"],
            "properties": {
                "source": {"const": "deterministic_static_analysis"},
                "exception": {
                    "type": "object",
                    "required": ["type", "message"],
                    "properties": {"type": {"type": "string"}, "message": {"type": "string"}},
                },
                "frames": {"type": "array", "items": {"type": "object"}},
            },
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "kind", "file", "symbol", "line_start", "line_end", "reason", "excerpt"],
                "additionalProperties": False,
                "properties": {
                    "id": _EVIDENCE_ID,
                    "kind": {"type": "string", "enum": ["exception", "frame", "callee", "caller", "base_class",
                                                        "subclass", "related_test"]},
                    "file": _NULLABLE_STR,
                    "symbol": _NULLABLE_STR,
                    "line_start": _NULLABLE_INT,
                    "line_end": _NULLABLE_INT,
                    "reason": {"type": "string"},
                    "excerpt": {"type": "string"},
                },
            },
        },
        "related_tests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["symbol", "file", "line", "executed"],
                "additionalProperties": False,
                "properties": {"symbol": {"type": "string"}, "file": {"type": "string"},
                               "line": {"type": "integer"}, "executed": {"const": False}},
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "verification": {
            "type": "object",
            "required": ["code_executed", "failure_reproduced", "fix_verified", "note"],
            "additionalProperties": False,
            "properties": {
                "code_executed": {"const": False},
                "failure_reproduced": {"const": False},
                "fix_verified": {"const": False},
                "note": {"type": "string"},
            },
        },
        "coverage": _COVERAGE,
        "privacy": {
            "type": "object",
            "required": ["sent_to_model", "redactions_applied", "evidence_items_sent", "full_repository_uploaded"],
            "additionalProperties": False,
            "properties": {
                "sent_to_model": {"type": "boolean"},
                "redactions_applied": {"type": "integer"},
                "evidence_items_sent": {"type": "integer"},
                "full_repository_uploaded": {"const": False},
            },
        },
        "model": {
            "type": "object",
            "required": ["provider", "model", "called", "repair_attempted"],
            "additionalProperties": False,
            "properties": {
                "provider": {"const": "nebius-token-factory"},
                "model": {"type": "string"},
                "called": {"type": "boolean"},
                "repair_attempted": {"type": "boolean"},
                "usage": {"type": ["object", "null"]},
            },
        },
        "notices": {"type": "array", "items": {"type": "string"}},
    },
}

ERROR_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["error"],
    "additionalProperties": False,
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "additionalProperties": False,
            "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
        }
    },
}

# What the model must return. The server adds ranks and locations itself.
MODEL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "summary", "hypotheses", "missing_information"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["diagnosed", "inconclusive"]},
        "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
        "hypotheses": {
            "type": "array",
            "maxItems": MAX_HYPOTHESES,
            "items": {
                "type": "object",
                "required": ["title", "explanation", "confidence", "evidence_ids"],
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "explanation": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "confidence": _CONFIDENCE,
                    "evidence_ids": {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True,
                                     "items": _EVIDENCE_ID},
                },
            },
        },
        "missing_information": {"type": "array", "maxItems": 10,
                                "items": {"type": "string", "maxLength": 500}},
    },
}


def schema_errors(instance: object, schema: dict, limit: int = 5) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    out = []
    for e in errors[:limit]:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"{path}: {e.validator} constraint failed")
    return out


def model_output_errors(output: object, valid_ids: set[str]) -> list[str]:
    """Schema validation plus semantic checks (known evidence IDs, status consistency)."""
    errors = schema_errors(output, MODEL_OUTPUT_SCHEMA)
    if errors:
        return errors
    assert isinstance(output, dict)
    if output["status"] == "diagnosed" and not output["hypotheses"]:
        errors.append("status 'diagnosed' requires at least one hypothesis")
    for i, hyp in enumerate(output["hypotheses"]):
        unknown = [e for e in hyp["evidence_ids"] if e not in valid_ids]
        if unknown:
            errors.append(f"hypotheses/{i}/evidence_ids: unknown evidence id(s) {', '.join(unknown[:5])}")
    return errors
