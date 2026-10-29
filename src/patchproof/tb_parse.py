"""Parsing of standard CPython traceback text (including chained exceptions)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import DEFAULT_LIMITS, Limits
from .errors import ErrorCode, PatchProofError

HEADER = "Traceback (most recent call last):"
CHAIN_MARKERS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)
FRAME_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S.*?))?\s*$')
EXC_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<msg>.*))?$")


@dataclass
class Frame:
    file: str
    line: int
    function: str
    code: str | None = None


@dataclass
class ParsedException:
    exc_type: str
    message: str
    frames: list[Frame] = field(default_factory=list)


@dataclass
class ParsedTraceback:
    chain: list[ParsedException]  # oldest first; the last element is the final exception

    @property
    def primary(self) -> ParsedException:
        return self.chain[-1]


def validate_traceback_text(text: object, limits: Limits = DEFAULT_LIMITS) -> str:
    if not isinstance(text, str):
        raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "traceback must be a string.")
    if len(text.encode("utf-8", errors="replace")) > limits.max_traceback_bytes:
        raise PatchProofError(
            ErrorCode.TRACEBACK_TOO_LARGE,
            f"traceback exceeds the {limits.max_traceback_bytes}-byte limit; it is rejected, not truncated.",
        )
    if not text.strip():
        raise PatchProofError(ErrorCode.TRACEBACK_EMPTY, "traceback is empty.")
    if "\x00" in text:
        raise PatchProofError(ErrorCode.TRACEBACK_INVALID, "traceback contains NUL characters.")
    return text


def _parse_block(lines: list[str]) -> ParsedException | None:
    frames: list[Frame] = []
    exc_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        m = FRAME_RE.match(line)
        if m:
            frames.append(Frame(file=m["file"], line=int(m["line"]), function=(m["func"] or "<unknown>")))
            exc_lines = []
            continue
        if not line.strip():
            continue
        if line[:1].isspace():
            stripped = line.strip()
            # Source line under a frame (skip caret/tilde markers).
            if frames and frames[-1].code is None and not set(stripped) <= set("^~ "):
                frames[-1].code = stripped[:300]
            if exc_lines:  # indented continuation of a multi-line message
                exc_lines.append(stripped)
            continue
        exc_lines.append(line)
    if not frames or not exc_lines:
        return None
    m = EXC_RE.match(exc_lines[0])
    if not m:
        return None
    message = "\n".join([m["msg"] or "", *exc_lines[1:]]).strip()
    return ParsedException(exc_type=m["type"], message=message, frames=frames)


def parse_traceback(text: str, limits: Limits = DEFAULT_LIMITS) -> ParsedTraceback:
    validate_traceback_text(text, limits)
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == HEADER:
            current = []
            blocks.append(current)
        elif stripped in CHAIN_MARKERS:
            current = None
        elif current is not None:
            current.append(line)
    chain = [p for p in (_parse_block(b) for b in blocks) if p is not None]
    if not chain:
        raise PatchProofError(
            ErrorCode.TRACEBACK_INVALID,
            "No standard Python traceback was found (expected 'Traceback (most recent call last):', "
            "at least one 'File \"...\", line N' frame, and an exception line).",
        )
    return ParsedTraceback(chain=chain)
