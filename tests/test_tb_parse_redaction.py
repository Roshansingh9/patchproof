import pytest
from conftest import SAMPLE_TB

from patchproof.config import Limits
from patchproof.errors import ErrorCode, PatchProofError
from patchproof.redaction import PLACEHOLDER, redact
from patchproof.tb_parse import parse_traceback

CHAINED = """Traceback (most recent call last):
  File "/r/app/db.py", line 4, in load
    return cfg["url"]
KeyError: 'url'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/r/app/main.py", line 9, in <module>
    start()
  File "/r/app/main.py", line 6, in start
    raise RuntimeError("could not start\\nsecond line")
RuntimeError: could not start
  extra detail line
"""


def test_parse_sample():
    tb = parse_traceback(SAMPLE_TB)
    assert len(tb.chain) == 1
    p = tb.primary
    assert p.exc_type == "TypeError"
    assert "NoneType" in p.message
    assert [f.function for f in p.frames] == ["<module>", "checkout_total", "compute_total", "apply_discount"]
    assert p.frames[-1].line == 10
    assert p.frames[-1].code == "if percent < 0 or percent > 100:"  # caret line ignored


def test_parse_chained_and_multiline():
    tb = parse_traceback(CHAINED)
    assert [e.exc_type for e in tb.chain] == ["KeyError", "RuntimeError"]
    assert tb.primary.message == "could not start\nextra detail line"
    assert len(tb.primary.frames) == 2


@pytest.mark.parametrize("text,code", [
    ("", ErrorCode.TRACEBACK_EMPTY),
    ("   \n\t", ErrorCode.TRACEBACK_EMPTY),
    ("just some log text\nValueError: x", ErrorCode.TRACEBACK_INVALID),
    ("Traceback (most recent call last):\nValueError: no frames", ErrorCode.TRACEBACK_INVALID),
    ("Traceback (most recent call last):\n  File \"a.py\", line 1, in f\n", ErrorCode.TRACEBACK_INVALID),
    (SAMPLE_TB + "\x00", ErrorCode.TRACEBACK_INVALID),
    (123, ErrorCode.INVALID_ARGUMENT),
])
def test_rejects_invalid(text, code):
    with pytest.raises(PatchProofError) as ei:
        parse_traceback(text)
    assert ei.value.code == code


def test_rejects_oversized_not_truncated():
    big = SAMPLE_TB + "#" * (64 * 1024)
    with pytest.raises(PatchProofError) as ei:
        parse_traceback(big)
    assert ei.value.code == ErrorCode.TRACEBACK_TOO_LARGE
    with pytest.raises(PatchProofError):
        parse_traceback(SAMPLE_TB, Limits(max_traceback_bytes=100))


# Fake credentials are assembled from fragments so secret scanners do not flag
# this file; the runtime values are complete and exercise the real patterns.
@pytest.mark.parametrize("secret", [
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp_" + "a" * 36,
    "github_pat_" + "A1" * 15,
    "xoxb-" + "1234567890-abcdef",
    "sk-proj-" + "x" * 30,
    "AIza" + "B" * 35,
    "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "." + "c2lnbmF0dXJlLXZhbHVl",
])
def test_redacts_token_formats(secret):
    out, n = redact(f"value = {secret} # end")
    assert secret not in out and n >= 1 and PLACEHOLDER in out


def test_redacts_assignments_urls_and_keys():
    text = (
        'PAYMENT_API_KEY = "sk-' + 'test-FAKE' + "0" * 28 + '"\n'
        + "db_password: 'hunter2hunter2'\n"
        + "DATABASE_URL = postgres://user:" + "pa55word@host:5432/db\n"
        + "headers = {'Authorization': 'Bearer abcdefghijklmnop1234'}\n"
        + "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEow\n-----END RSA " + "PRIVATE KEY-----\n"
        + "limit = 10\nname = 'visible'\n"
    )
    out, n = redact(text)
    for s in ("sk-test-FAKE", "hunter2", "pa55word", "abcdefghijklmnop1234", "MIIEow"):
        assert s not in out
    assert "limit = 10" in out and "name = 'visible'" in out
    assert n >= 5


def test_redacts_configured_api_key(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nb-very-secret-value-123")
    out, n = redact("oops nb-very-secret-value-123 leaked")
    assert "nb-very-secret" not in out and n == 1
