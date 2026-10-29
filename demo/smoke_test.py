"""Headless smoke test for the demo (no network). Run with the demo's environment:

    python demo/smoke_test.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

import patchproof.core as core  # noqa: E402
from patchproof.nebius import Completion  # noqa: E402

APP = str(PROJECT / "demo" / "app.py")
FAKE_OUTPUT = (
    '{"status": "diagnosed", "summary": "Unknown coupon codes yield None.", "missing_information": [],'
    ' "hypotheses": [{"title": "parse_coupon returns None", "explanation": "COUPONS.get returns None.",'
    ' "confidence": "high", "evidence_ids": ["E2", "E3"]}]}'
)


class FakeClient:
    model = "fake/nemotron"

    def complete(self, messages, max_tokens):
        return Completion(content=FAKE_OUTPUT, finish_reason="stop", usage=None)


def run_without_key() -> None:
    os.environ.pop("NEBIUS_API_KEY", None)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("no Nebius API key" in e.value for e in at.error)
    assert at.button[0].disabled
    print("no-key path: OK")


def run_with_mocked_model() -> None:
    os.environ["NEBIUS_API_KEY"] = "demo-smoke-test-placeholder"
    real = core.diagnose_failure
    core.diagnose_failure = lambda root, tb: real(root, tb, client=FakeClient())
    try:
        at = AppTest.from_file(APP, default_timeout=60).run()
        at.button[0].click().run()
        assert not at.exception, at.exception
        text = " ".join(m.value for m in at.markdown)
        assert "Status: diagnosed" in " ".join(h.value for h in at.subheader)
        assert "model hypothesis, not a verified fact" in text
        assert "demo-smoke-test-placeholder" not in str(at)
        for _ in range(3):
            at.button[0].click().run()
        assert at.button[0].disabled  # per-session limit
        print("mocked diagnosis path: OK")
    finally:
        core.diagnose_failure = real
        os.environ.pop("NEBIUS_API_KEY", None)


if __name__ == "__main__":
    run_without_key()
    run_with_mocked_model()
