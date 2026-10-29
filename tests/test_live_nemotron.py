"""Opt-in live tests against Nebius Token Factory.

Run with:  PATCHPROOF_LIVE_TESTS=1 NEBIUS_API_KEY=... pytest -m live -s
These make real, billable API calls.
"""

import os

import pytest

from patchproof.config import model_id
from patchproof.core import diagnose_failure
from patchproof.nebius import NebiusClient
from patchproof.schemas import DIAGNOSE_OUTPUT_SCHEMA, schema_errors

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("PATCHPROOF_LIVE_TESTS") != "1" or not os.environ.get("NEBIUS_API_KEY"),
        reason="live tests require PATCHPROOF_LIVE_TESTS=1 and NEBIUS_API_KEY",
    ),
]


def test_model_in_catalog():
    client = NebiusClient(model_id())
    try:
        models = client.list_models()
    finally:
        client.close()
    assert model_id() in models, f"{model_id()} not in catalog; Nemotron IDs: {[m for m in models if 'emotron' in m]}"


class RecordingClient:
    """Real NebiusClient that also records each call's finish_reason (no mocking)."""

    def __init__(self) -> None:
        self._inner = NebiusClient(model_id())
        self.model = self._inner.model
        self.finish_reasons: list[str | None] = []

    def complete(self, messages, max_tokens):
        completion = self._inner.complete(messages, max_tokens)
        self.finish_reasons.append(completion.finish_reason)
        return completion


def test_live_diagnosis(sample_root, sample_tb):
    client = RecordingClient()
    try:
        result = diagnose_failure(sample_root, sample_tb, client=client)
    finally:
        client._inner.close()
    print("\nfinish_reason per call:", client.finish_reasons)
    assert not schema_errors(result, DIAGNOSE_OUTPUT_SCHEMA)
    assert result["model"]["called"] is True
    evidence_ids = {e["id"] for e in result["evidence"]}
    for h in result["hypotheses"]:
        assert h["evidence_ids"] and set(h["evidence_ids"]) <= evidence_ids
        assert [loc["evidence_id"] for loc in h["locations"]] == h["evidence_ids"]
    assert result["verification"]["fix_verified"] is False
    print("\nstatus:", result["status"], "| repair:", result["model"]["repair_attempted"],
          "| usage:", result["model"]["usage"])
    for h in result["hypotheses"]:
        print(f"  #{h['rank']} [{h['confidence']}] {h['title']} -> {h['evidence_ids']}")
