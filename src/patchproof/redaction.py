"""Local, best-effort secret redaction applied to all text before inference.

This is heuristic: it lowers, but does not eliminate, the risk of sending a
secret to the model provider. See README "Privacy".
"""

from __future__ import annotations

import os
import re

from .config import NEBIUS_API_KEY_ENV

PLACEHOLDER = "[REDACTED]"

# (pattern, replacement). Replacements keep surrounding structure where useful.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
     PLACEHOLDER),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), PLACEHOLDER),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), PLACEHOLDER),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), PLACEHOLDER),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), PLACEHOLDER),
    (re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"), PLACEHOLDER),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), PLACEHOLDER),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), PLACEHOLDER),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 " + PLACEHOLDER),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + PLACEHOLDER + "@"),
    # key = "value" / key: 'value' / KEY=value for secret-sounding names.
    (re.compile(
        r"""(?ix)
        \b([A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|credential|private[_-]?key|access[_-]?key|auth)[A-Za-z0-9_.-]*)
        (\s*[:=]\s*)
        (?:"[^"\n]{4,}"|'[^'\n]{4,}'|[^\s"',;)\]}]{8,})
        """),
     r'\1\2"' + PLACEHOLDER + '"'),
]


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions)."""
    count = 0
    env_key = os.environ.get(NEBIUS_API_KEY_ENV, "")
    if len(env_key) >= 8 and env_key in text:
        count += text.count(env_key)
        text = text.replace(env_key, PLACEHOLDER)
    for pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count
