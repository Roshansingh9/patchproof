"""The two PatchProof operations, independent of MCP transport.

Both rebuild the index and graph from disk on every call (no caching), so
results always reflect the current working tree.
"""

from __future__ import annotations

import logging
from collections import Counter

from .config import DEFAULT_LIMITS, MAX_LISTED_PARSE_ERRORS, MAX_LISTED_TESTS, SCHEMA_VERSION, Limits, model_id
from .errors import ErrorCode, PatchProofError
from .evidence import map_frames, select_evidence
from .graph import CodeGraph, build_graph, is_test_file
from .indexer import index_repository
from .nebius import ChatClient, NebiusClient, api_key_from_env, extract_json
from .paths import resolve_repository_root
from .prompt import fit_to_budget, repair_messages
from .redaction import redact
from .schemas import (
    ANALYZE_OUTPUT_SCHEMA,
    DIAGNOSE_OUTPUT_SCHEMA,
    model_output_errors,
    schema_errors,
)
from .tb_parse import parse_traceback, validate_traceback_text

log = logging.getLogger("patchproof")

VERIFICATION_NOTE = (
    "PatchProof performs static analysis only. No repository code or tests were executed, the failure was "
    "not reproduced, and no fix was generated or verified. Hypotheses are model output, not facts."
)


def _build(repository_root: object, limits: Limits):
    root = resolve_repository_root(repository_root)
    index = index_repository(root, limits)
    if not index.files and not index.coverage.parse_errors:
        raise PatchProofError(ErrorCode.NO_PYTHON_SOURCES, "No indexable Python source files were found.")
    return root, index, build_graph(index)


def _check_output(result: dict, schema: dict) -> dict:
    errors = schema_errors(result, schema)
    if errors:
        log.error("output schema violation: %s", errors)
        raise PatchProofError(ErrorCode.INTERNAL_ERROR, "PatchProof produced an invalid response.")
    return result


def analyze_repository(repository_root: object, limits: Limits = DEFAULT_LIMITS) -> dict:
    root, index, cg = _build(repository_root, limits)
    symbol_counts = Counter(s.module for s in cg.symbols.values())
    tests = cg.tests()
    result = {
        "tool": "analyze_repository",
        "schema_version": SCHEMA_VERSION,
        "repository_root": str(root),
        "coverage": index.coverage.to_dict(MAX_LISTED_PARSE_ERRORS),
        "graph": cg.stats,
        "modules": [
            {"module": m, "file": sf.rel_path, "symbols": symbol_counts.get(m, 0), "is_test": is_test_file(sf.rel_path)}
            for m, sf in sorted(cg.modules.items())
        ],
        "tests": {
            "discovered": len(tests),
            "listed": min(len(tests), MAX_LISTED_TESTS),
            "items": [{"symbol": t.qname, "file": t.file, "line": t.lineno} for t in tests[:MAX_LISTED_TESTS]],
        },
        "limits": limits.as_dict(),
        "notices": [
            "Static analysis only: no repository code was imported or executed.",
            "Tests were discovered statically by naming convention and were not run.",
            "Dynamic calls, reflection, monkeypatching and generated code are not resolved; "
            "see graph.unresolved_calls.",
        ],
    }
    return _check_output(result, ANALYZE_OUTPUT_SCHEMA)


def _facts(tb, frames) -> dict:
    primary = tb.primary
    return {
        "source": "deterministic_static_analysis",
        "exception": {"type": primary.exc_type, "message": redact(primary.message)[0][:2000]},
        "chained_exceptions": [
            {"type": e.exc_type, "message": redact(e.message)[0][:500]} for e in tb.chain[:-1]
        ],
        "frames": [f.to_dict() for f in frames],
    }


def _locations(hyp_ids: list[str], items_by_id: dict) -> list[dict]:
    locs = []
    for eid in hyp_ids:
        item = items_by_id[eid]
        locs.append({"evidence_id": eid, "file": item.file, "symbol": item.symbol,
                     "line_start": item.line_start, "line_end": item.line_end})
    return locs


def diagnose_failure(repository_root: object, traceback: object, *, client: ChatClient | None = None,
                     limits: Limits = DEFAULT_LIMITS) -> dict:
    # 1. Cheap input validation before any indexing or model call.
    root = resolve_repository_root(repository_root)
    text = validate_traceback_text(traceback, limits)
    tb = parse_traceback(text, limits)
    model = client.model if client is not None else model_id()
    if client is None:
        api_key_from_env()  # fail fast, before indexing

    # 2. Fresh index and graph.
    root, index, cg = _build(str(root), limits)
    coverage = index.coverage.to_dict(MAX_LISTED_PARSE_ERRORS)
    frames = map_frames(tb, cg, root)
    bundle = select_evidence(tb, frames, cg, limits)
    facts = _facts(tb, frames)
    notices = list(bundle.notes)

    stale = [f for f in frames if f.source_matches is False or f.mapping == "line_out_of_range"
             or f.symbol_matches is False]
    if stale:
        notices.append(
            f"{len(stale)} frame(s) do not match the current source (line content, range or function name). "
            "The traceback may come from a different revision than the working tree."
        )
    if coverage["partial"]:
        notices.append("Repository coverage is partial; relevant code may be missing from the evidence.")

    base = {
        "tool": "diagnose_failure",
        "schema_version": SCHEMA_VERSION,
        "facts": facts,
        "related_tests": [{"symbol": t.qname, "file": t.file, "line": t.lineno, "executed": False}
                          for t in bundle.related_tests[:MAX_LISTED_TESTS]],
        "verification": {"code_executed": False, "failure_reproduced": False, "fix_verified": False,
                         "note": VERIFICATION_NOTE},
        "coverage": coverage,
    }

    mapped = [f for f in frames if f.file is not None]
    if not mapped:
        result = {
            **base,
            "status": "inconclusive",
            "summary": "None of the traceback frames map to indexed project source, so no evidence-grounded "
                       "diagnosis is possible. The model was not called.",
            "hypotheses": [],
            "evidence": [i.to_dict() for i in bundle.items],
            "missing_information": ["A traceback whose frames point into this repository's Python source."],
            "privacy": {"sent_to_model": False, "redactions_applied": bundle.redactions,
                        "evidence_items_sent": 0, "full_repository_uploaded": False},
            "model": {"provider": "nebius-token-factory", "model": model, "called": False,
                      "repair_attempted": False, "usage": None},
            "notices": notices,
        }
        return _check_output(result, DIAGNOSE_OUTPUT_SCHEMA)

    # 3. Prompt within budget (deterministic reduction; never truncates the traceback).
    tb_redacted, tb_redactions = redact(text)
    messages = fit_to_budget(tb_redacted, facts, bundle, coverage["notes"], limits)
    if bundle.dropped_for_budget:
        notices.append(f"{bundle.dropped_for_budget} lower-priority evidence item(s) dropped to fit the model budget.")
    valid_ids = {i.id for i in bundle.items}
    items_by_id = {i.id: i for i in bundle.items}

    # 4. Model call plus at most `max_repair_attempts` repair calls for invalid output.
    owns_client = client is None
    if client is None:
        client = NebiusClient(model)
    usage_total: dict[str, int] = {}
    repair_attempted = False
    try:
        attempt = 0
        current = messages
        while True:
            completion = client.complete(current, limits.max_model_output_tokens)
            for k, v in (completion.usage or {}).items():
                usage_total[k] = usage_total.get(k, 0) + v
            try:
                parsed = extract_json(completion.content)
                errors = model_output_errors(parsed, valid_ids)
            except ValueError:
                parsed, errors = None, ["output is not a valid JSON object"]
            if completion.finish_reason == "length" and errors:
                errors.append("output was cut off at the token limit; be more concise")
            if not errors:
                break
            if attempt >= limits.max_repair_attempts:
                raise PatchProofError(ErrorCode.MODEL_OUTPUT_INVALID,
                                      "The model returned invalid output after the allowed repair attempt.")
            attempt += 1
            repair_attempted = True
            # Errors are schema paths/constraint names only; never model content.
            log.info("model output invalid (finish_reason=%s): %s; attempting repair",
                     completion.finish_reason, "; ".join(errors))
            current = repair_messages(messages, completion.content, errors)
    finally:
        if owns_client:
            client.close()

    assert isinstance(parsed, dict)
    hypotheses = [
        {
            "rank": rank,
            "title": h["title"],
            "explanation": h["explanation"],
            "confidence": h["confidence"],
            "evidence_ids": h["evidence_ids"],
            "locations": _locations(h["evidence_ids"], items_by_id),
            "source": "model_hypothesis",
        }
        for rank, h in enumerate(parsed["hypotheses"], start=1)
    ]
    result = {
        **base,
        "status": parsed["status"],
        "summary": parsed["summary"],
        "hypotheses": hypotheses,
        "evidence": [i.to_dict() for i in bundle.items],
        "missing_information": parsed["missing_information"],
        "privacy": {"sent_to_model": True, "redactions_applied": bundle.redactions + tb_redactions,
                    "evidence_items_sent": len(bundle.items), "full_repository_uploaded": False},
        "model": {"provider": "nebius-token-factory", "model": model, "called": True,
                  "repair_attempted": repair_attempted, "usage": usage_total or None},
        "notices": notices,
    }
    return _check_output(result, DIAGNOSE_OUTPUT_SCHEMA)
