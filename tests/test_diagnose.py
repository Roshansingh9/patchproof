"""Mocked end-to-end diagnosis tests (no network)."""

import json

import httpx
import pytest
from conftest import FakeClient, good_output, network_error, write_repo

from patchproof.config import (
    DEFAULT_LIMITS,
    MAX_MODEL_INPUT_TOKENS,
    MAX_MODEL_OUTPUT_TOKENS,
    MAX_REPAIR_ATTEMPTS,
)
from patchproof.core import analyze_repository, diagnose_failure
from patchproof.errors import ErrorCode, PatchProofError
from patchproof.nebius import NebiusClient, extract_json
from patchproof.schemas import DIAGNOSE_OUTPUT_SCHEMA, schema_errors


def callee_id(result, symbol="shop.coupons.parse_coupon"):
    return next(e["id"] for e in result["evidence"] if e["symbol"] == symbol)


def test_analyze_repository_contract(sample_root):
    result = analyze_repository(sample_root)
    assert result["tool"] == "analyze_repository"
    assert result["graph"]["edges"]["calls"] > 0
    assert result["tests"]["discovered"] == 4
    assert result["coverage"]["partial"] is False


def test_diagnosed_end_to_end(sample_root, sample_tb):
    client = FakeClient(good_output(ids=("E2", "E3")))
    result = diagnose_failure(sample_root, sample_tb, client=client)
    assert not schema_errors(result, DIAGNOSE_OUTPUT_SCHEMA)
    assert result["status"] == "diagnosed"
    hyp = result["hypotheses"][0]
    assert hyp["rank"] == 1 and hyp["source"] == "model_hypothesis"
    assert hyp["locations"][0]["file"] == "shop/pricing.py"
    assert result["verification"] == {
        "code_executed": False, "failure_reproduced": False, "fix_verified": False,
        "note": result["verification"]["note"],
    }
    assert result["privacy"]["full_repository_uploaded"] is False
    assert result["model"] == {"provider": "nebius-token-factory", "model": "fake/nemotron", "called": True,
                               "repair_attempted": False,
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    assert result["facts"]["exception"]["type"] == "TypeError"
    assert len(client.calls) == 1
    # Prompt contains selected evidence, not the whole repository.
    user_payload = json.loads(client.calls[0][1]["content"])
    files_sent = {e.get("file") for e in user_payload["evidence"]}
    assert "shop/settings.py" not in files_sent
    assert any(e.get("symbol") == "shop.coupons.parse_coupon" for e in user_payload["evidence"])


def test_budget_configuration():
    assert MAX_MODEL_OUTPUT_TOKENS == 4_000
    assert MAX_MODEL_INPUT_TOKENS == 12_000
    assert MAX_REPAIR_ATTEMPTS == 1
    assert DEFAULT_LIMITS.max_model_output_tokens == MAX_MODEL_OUTPUT_TOKENS


def test_output_budget_passed_to_every_call(sample_root, sample_tb):
    client = FakeClient("not json", good_output())
    diagnose_failure(sample_root, sample_tb, client=client)
    assert client.max_tokens == [MAX_MODEL_OUTPUT_TOKENS, MAX_MODEL_OUTPUT_TOKENS]


def test_inconclusive_allowed(sample_root, sample_tb):
    result = diagnose_failure(sample_root, sample_tb, client=FakeClient(good_output(status="inconclusive")))
    assert result["status"] == "inconclusive" and result["hypotheses"] == []


def test_repair_after_invalid_json(sample_root, sample_tb):
    client = FakeClient("Sure! Here is my analysis: not json", good_output())
    result = diagnose_failure(sample_root, sample_tb, client=client)
    assert result["model"]["repair_attempted"] is True
    assert len(client.calls) == 2
    assert "rejected by validation" in client.calls[1][-1]["content"]


def test_repair_after_unknown_evidence_id(sample_root, sample_tb):
    client = FakeClient(good_output(ids=("E999",)), good_output(ids=("E2",)))
    result = diagnose_failure(sample_root, sample_tb, client=client)
    assert result["model"]["repair_attempted"] is True
    assert "E999" in client.calls[1][-1]["content"]


@pytest.mark.parametrize("bad", [
    {"status": "diagnosed", "summary": "x", "hypotheses": [], "missing_information": []},
    {**good_output(), "extra": 1},
    {**good_output(), "status": "certain"},
    {"status": "diagnosed", "summary": "x", "missing_information": [],
     "hypotheses": [{"title": "t", "explanation": "e", "confidence": "0.9", "evidence_ids": ["E2"]}]},
])
def test_invalid_twice_fails_with_stable_code(sample_root, sample_tb, bad):
    client = FakeClient(bad, bad)
    with pytest.raises(PatchProofError) as ei:
        diagnose_failure(sample_root, sample_tb, client=client)
    assert ei.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    assert len(client.calls) == 2  # exactly one repair attempt


TRUNCATED = (json.dumps(good_output())[:60], "length")  # output cut off at the token cap


def test_truncated_output_repaired_once(sample_root, sample_tb):
    client = FakeClient(TRUNCATED, good_output())
    result = diagnose_failure(sample_root, sample_tb, client=client)
    assert result["model"]["repair_attempted"] is True
    assert len(client.calls) == 2
    assert "cut off at the token limit" in client.calls[1][-1]["content"]


def test_truncated_twice_fails_after_one_repair(sample_root, sample_tb):
    client = FakeClient(TRUNCATED, TRUNCATED)
    with pytest.raises(PatchProofError) as ei:
        diagnose_failure(sample_root, sample_tb, client=client)
    assert ei.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    assert len(client.calls) == 2


def test_provider_error_during_repair_not_retried(sample_root, sample_tb):
    client = FakeClient(TRUNCATED, PatchProofError(ErrorCode.PROVIDER_ERROR, "Nebius returned HTTP 503."))
    with pytest.raises(PatchProofError) as ei:
        diagnose_failure(sample_root, sample_tb, client=client)
    assert ei.value.code == ErrorCode.PROVIDER_ERROR
    assert len(client.calls) == 2


@pytest.mark.parametrize("code", [ErrorCode.PROVIDER_RATE_LIMITED, ErrorCode.PROVIDER_AUTH_ERROR,
                                  ErrorCode.PROVIDER_ERROR, ErrorCode.PROVIDER_MODEL_NOT_FOUND])
def test_provider_errors_not_retried(sample_root, sample_tb, code):
    client = FakeClient(PatchProofError(code, "provider failure"), good_output())
    with pytest.raises(PatchProofError) as ei:
        diagnose_failure(sample_root, sample_tb, client=client)
    assert ei.value.code == code
    assert len(client.calls) == 1


def test_network_error_not_retried(sample_root, sample_tb):
    client = FakeClient(network_error())
    with pytest.raises(PatchProofError) as ei:
        diagnose_failure(sample_root, sample_tb, client=client)
    assert ei.value.code == ErrorCode.NETWORK_ERROR
    assert len(client.calls) == 1


def test_no_mapped_frames_skips_model(sample_root):
    tb = 'Traceback (most recent call last):\n  File "/usr/lib/python3.12/json/decoder.py", line 3, in decode\nValueError: x\n'
    client = FakeClient()
    result = diagnose_failure(sample_root, tb, client=client)
    assert result["status"] == "inconclusive"
    assert result["model"]["called"] is False and not client.calls
    assert result["privacy"]["sent_to_model"] is False


def test_invalid_traceback_rejected_before_model(sample_root):
    client = FakeClient()
    for tb, code in (("", ErrorCode.TRACEBACK_EMPTY), ("hello", ErrorCode.TRACEBACK_INVALID),
                     ("x" * (64 * 1024 + 1), ErrorCode.TRACEBACK_TOO_LARGE)):
        with pytest.raises(PatchProofError) as ei:
            diagnose_failure(sample_root, tb, client=client)
        assert ei.value.code == code
    assert not client.calls


def test_missing_api_key(sample_root, sample_tb, no_api_key):
    with pytest.raises(PatchProofError) as ei:
        diagnose_failure(sample_root, sample_tb)
    assert ei.value.code == ErrorCode.MISSING_API_KEY


def test_secrets_redacted_before_inference(tmp_path):
    write_repo(tmp_path, {"app.py": (
        "def connect():\n"
        "    api_key = 'sk-" + "live-ABCDEFGHIJKLMNOPQRSTUVWX'\n"  # fragments: avoid scanner false positives
        "    raise RuntimeError('auth failed for token ghp_" + "Z" * 36 + "')\n"
    )})
    tb = (f'Traceback (most recent call last):\n  File "{tmp_path / "app.py"}", line 3, in connect\n'
          "RuntimeError: auth failed for token ghp_" + "Z" * 36 + "\n")
    client = FakeClient(good_output(ids=("E2",)))
    result = diagnose_failure(str(tmp_path), tb, client=client)
    sent = json.dumps(client.calls[0])
    assert "sk-live-ABCDEF" not in sent and "ghp_ZZZZ" not in sent
    assert result["privacy"]["redactions_applied"] >= 3
    assert "ghp_ZZZZ" not in json.dumps(result)


def test_stale_traceback_notice(tmp_path):
    write_repo(tmp_path, {"m.py": "def f():\n    return 1\n"})
    tb = 'Traceback (most recent call last):\n  File "m.py", line 2, in f\n    return 2\nValueError: x\n'
    result = diagnose_failure(str(tmp_path), tb, client=FakeClient(good_output(ids=("E2",))))
    assert any("different revision" in n for n in result["notices"])


def _client_with(handler) -> NebiusClient:
    return NebiusClient("nvidia/test", api_key="k", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status,code", [
    (401, ErrorCode.PROVIDER_AUTH_ERROR), (404, ErrorCode.PROVIDER_MODEL_NOT_FOUND),
    (429, ErrorCode.PROVIDER_RATE_LIMITED), (503, ErrorCode.PROVIDER_ERROR),
])
def test_http_errors_map_to_codes(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret upstream detail")

    with pytest.raises(PatchProofError) as ei:
        _client_with(handler).complete([{"role": "user", "content": "x"}], 10)
    assert ei.value.code == code and "secret upstream detail" not in ei.value.message
    assert len(calls) == 1


def test_http_request_shape_and_connect_error():
    seen = {}

    def ok(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    out = _client_with(ok).complete([{"role": "user", "content": "x"}], MAX_MODEL_OUTPUT_TOKENS)
    assert seen["url"] == "https://api.tokenfactory.nebius.com/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "nvidia/test" and seen["body"]["max_tokens"] == MAX_MODEL_OUTPUT_TOKENS
    assert out.content == "{}"

    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(PatchProofError) as ei:
        _client_with(boom).complete([], 10)
    assert ei.value.code == ErrorCode.NETWORK_ERROR
    assert "k" not in repr(_client_with(ok)).replace("NebiusClient", "").replace("nvidia/test", "")


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('<think>hmm {"no": 1}</think>{"a": 2}') == {"a": 2}
    assert extract_json('Result: {"a": 3} done') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("nothing here")
