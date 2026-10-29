from __future__ import annotations

import json
from pathlib import Path

import pytest

from patchproof.errors import ErrorCode, PatchProofError
from patchproof.nebius import Completion

PROJECT = Path(__file__).resolve().parents[1]
SAMPLE_REPO = PROJECT / "examples" / "sample_repo"
SAMPLE_TB = (PROJECT / "examples" / "tracebacks" / "unknown_coupon.txt").read_text(encoding="utf-8")


class FakeClient:
    """Scripted ChatClient. Each response is a string, a dict (JSON-encoded), an exception,
    or a (content, finish_reason) tuple."""

    def __init__(self, *responses, model: str = "fake/nemotron") -> None:
        self.model = model
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.max_tokens: list[int] = []

    def complete(self, messages, max_tokens):
        self.calls.append(messages)
        self.max_tokens.append(max_tokens)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        finish_reason = "stop"
        if isinstance(resp, tuple):
            resp, finish_reason = resp
        if isinstance(resp, dict):
            resp = json.dumps(resp)
        return Completion(content=resp, finish_reason=finish_reason,
                          usage={"prompt_tokens": 10, "completion_tokens": 5})


def good_output(ids=("E2",), status="diagnosed") -> dict:
    return {
        "status": status,
        "summary": "parse_coupon returns None for unknown codes.",
        "hypotheses": [
            {
                "title": "Unknown coupon yields None percent",
                "explanation": "COUPONS.get returns None, which reaches the comparison in apply_discount.",
                "confidence": "high",
                "evidence_ids": list(ids),
            }
        ]
        if status == "diagnosed"
        else [],
        "missing_information": [],
    }


@pytest.fixture
def sample_root() -> str:
    return str(SAMPLE_REPO)


@pytest.fixture
def sample_tb() -> str:
    return SAMPLE_TB


@pytest.fixture
def no_api_key(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)


def network_error() -> PatchProofError:
    return PatchProofError(ErrorCode.NETWORK_ERROR, "Could not reach Nebius Token Factory.")


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root
