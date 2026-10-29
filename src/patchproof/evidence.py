"""Deterministic mapping of traceback frames to indexed source and bounded evidence selection.

Evidence comes only from already-indexed (confined) source text; nothing here
reads from disk. Every excerpt is redacted before it can reach the model.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .config import DEFAULT_LIMITS, Limits
from .graph import CALLS, IMPORTS, INHERITS, CodeGraph, Symbol
from .redaction import redact
from .tb_parse import Frame, ParsedTraceback

_EXTERNAL_MARKERS = ("site-packages/", "dist-packages/", "/lib/python", "/lib64/python")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass
class MappedFrame:
    index: int  # position in the final exception's frame list (0 = outermost)
    chain_index: int  # which exception in the chain (last = final exception)
    reported_file: str
    line: int
    function: str
    file: str | None = None
    symbol: str | None = None
    mapping: str = "not_in_index"
    symbol_matches: bool | None = None
    source_matches: bool | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "chain_index": self.chain_index,
            "reported_file": redact(self.reported_file)[0],
            "line": self.line,
            "function": self.function,
            "file": self.file,
            "symbol": self.symbol,
            "mapping": self.mapping,
            "symbol_matches": self.symbol_matches,
            "source_matches": self.source_matches,
        }


@dataclass
class EvidenceItem:
    kind: str  # exception | frame | callee | caller | base_class | subclass | related_test
    reason: str
    file: str | None = None
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str = ""
    id: str = ""
    redactions: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "symbol": self.symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "reason": self.reason,
            "excerpt": self.excerpt,
        }


@dataclass
class EvidenceBundle:
    frames: list[MappedFrame]
    items: list[EvidenceItem]
    related_tests: list[Symbol]
    dropped_for_budget: int = 0
    candidates_considered: int = 0
    redactions: int = 0
    notes: list[str] = field(default_factory=list)


def _norm_parts(reported: str) -> list[str]:
    if _WIN_DRIVE.match(reported) or "\\" in reported:
        parts = list(PureWindowsPath(reported).parts)
        if parts and _WIN_DRIVE.match(parts[0] + "\\"):
            parts = parts[1:]
        return [p for p in parts if p not in ("\\", "/")]
    return [p for p in PurePosixPath(reported).parts if p != "/"]


def _map_path(reported: str, cg: CodeGraph, root: Path) -> tuple[str | None, str]:
    if reported.startswith("<") or not reported.endswith(".py"):
        return None, "outside_repository"
    posix = reported.replace("\\", "/")
    if any(m in posix.lower() for m in _EXTERNAL_MARKERS):
        return None, "outside_repository"
    root_posix = root.as_posix().rstrip("/")
    lower = posix.lower() if _WIN_DRIVE.match(posix) else posix
    root_cmp = root_posix.lower() if _WIN_DRIVE.match(root_posix) else root_posix
    if lower.startswith(root_cmp + "/"):
        rel = posix[len(root_posix) + 1:]
        return (rel, "resolved") if rel in cg.files else (None, "not_in_index")
    parts = _norm_parts(reported)
    is_absolute = posix.startswith("/") or bool(_WIN_DRIVE.match(posix))
    for k in range(len(parts), 0, -1):
        suffix = "/".join(parts[-k:])
        matches = sorted(f for f in cg.files if f == suffix or f.endswith("/" + suffix))
        if not matches:
            continue
        if len(matches) > 1:
            return None, "ambiguous"
        if k == 1 and is_absolute and "/" in matches[0]:
            return matches[0], "resolved_filename_only"
        return matches[0], "resolved"
    return None, "not_in_index"


def map_frames(tb: ParsedTraceback, cg: CodeGraph, root: Path) -> list[MappedFrame]:
    mapped: list[MappedFrame] = []
    for ci, exc in enumerate(tb.chain):
        for i, frame in enumerate(exc.frames):
            mapped.append(_map_frame(frame, i, ci, cg, root))
    return mapped


def _map_frame(frame: Frame, index: int, chain_index: int, cg: CodeGraph, root: Path) -> MappedFrame:
    mf = MappedFrame(index=index, chain_index=chain_index, reported_file=frame.file,
                     line=frame.line, function=frame.function)
    rel, status = _map_path(frame.file, cg, root)
    mf.mapping = status
    if rel is None:
        return mf
    mf.file = rel
    sf = cg.files[rel]
    if frame.line < 1 or frame.line > len(sf.lines):
        mf.mapping = "line_out_of_range"
        return mf
    if frame.code is not None:
        mf.source_matches = sf.lines[frame.line - 1].strip() == frame.code.strip()
    sym = cg.innermost_symbol(rel, frame.line)
    if sym is not None:
        mf.symbol = sym.qname
    if frame.function == "<module>":
        mf.symbol_matches = sym is None
    else:
        mf.symbol_matches = sym is not None and sym.name == frame.function
    return mf


def _numbered(lines: list[str], start: int, end: int, mark: int | None) -> str:
    out = []
    for n in range(start, end + 1):
        prefix = ">>" if n == mark else "  "
        out.append(f"{prefix}{n:5d} | {lines[n - 1]}")
    return "\n".join(out)


def _window(lines: list[str], lo: int, hi: int, focus: int | None, cap: int) -> tuple[int, int, str]:
    """Largest window inside [lo, hi] around focus (or from lo) that fits `cap` chars."""
    lo, hi = max(1, lo), min(len(lines), hi)
    if focus is None:
        start = end = lo
        while end + 1 <= hi and len(_numbered(lines, start, end + 1, None)) <= cap:
            end += 1
    else:
        start = end = min(max(focus, lo), hi)
        grow_up = True
        while True:
            moved = False
            for direction in ((True, False) if grow_up else (False, True)):
                if direction and start - 1 >= lo and len(_numbered(lines, start - 1, end, focus)) <= cap:
                    start -= 1
                    moved = True
                    break
                if not direction and end + 1 <= hi and len(_numbered(lines, start, end + 1, focus)) <= cap:
                    end += 1
                    moved = True
                    break
            grow_up = not grow_up
            if not moved:
                break
    text = _numbered(lines, start, end, focus)
    if len(text) > cap:
        text = text[: cap - 3] + "..."
    return start, end, text


def _symbol_item(cg: CodeGraph, sym: Symbol, kind: str, reason: str, focus: int | None,
                 limits: Limits) -> EvidenceItem:
    lines = cg.files[sym.file].lines
    start, end, text = _window(lines, sym.lineno, sym.end_lineno, focus, limits.max_excerpt_chars)
    text, n = redact(text)
    return EvidenceItem(kind=kind, reason=reason, file=sym.file, symbol=sym.qname,
                        line_start=start, line_end=end, excerpt=text, redactions=n)


def _module_frame_item(cg: CodeGraph, mf: MappedFrame, limits: Limits) -> EvidenceItem:
    lines = cg.files[mf.file].lines
    start, end, text = _window(lines, mf.line - 15, mf.line + 15, mf.line, limits.max_excerpt_chars)
    text, n = redact(text)
    return EvidenceItem(kind="frame", reason=f"Module-level code at traceback frame line {mf.line}.",
                        file=mf.file, line_start=start, line_end=end, excerpt=text, redactions=n)


_RELATION_ORDER = {"callee": 0, "caller": 1, "base_class": 2, "subclass": 3}


def _neighbors(cg: CodeGraph, qname: str) -> list[tuple[str, str]]:
    g = cg.graph
    out: list[tuple[str, str]] = []
    for _, v, k in g.out_edges(qname, keys=True):
        if k == CALLS:
            out.append(("callee", v))
        elif k == INHERITS:
            out.append(("base_class", v))
    for u, _, k in g.in_edges(qname, keys=True):
        if k == CALLS:
            out.append(("caller", u))
        elif k == INHERITS:
            out.append(("subclass", u))
    # Methods are reachable through their class's inheritance too.
    sym = cg.symbols.get(qname)
    if sym and sym.parent_class:
        for _, v, k in g.out_edges(sym.parent_class, keys=True):
            if k == INHERITS and f"{v}.{sym.name}" in cg.symbols:
                out.append(("base_class", f"{v}.{sym.name}"))
    return sorted(set(out), key=lambda t: (_RELATION_ORDER[t[0]], t[1]))


def related_tests(cg: CodeGraph, seeds: list[str], depth: int) -> list[Symbol]:
    """Statically discovered tests that call into the seed symbols within `depth` hops,
    or that live in a module importing a seed's module."""
    g = cg.graph
    found: dict[str, int] = {}
    queue = deque((s, 0) for s in seeds)
    seen = set(seeds)
    while queue:
        node, d = queue.popleft()
        if d >= depth:
            continue
        for u, _, k in g.in_edges(node, keys=True):
            if k != CALLS or u in seen:
                continue
            seen.add(u)
            if cg.symbols.get(u) and cg.symbols[u].is_test:
                found[u] = min(found.get(u, d + 1), d + 1)
            queue.append((u, d + 1))
    # Test modules importing a seed's module, or any symbol defined in it.
    seed_modules = {cg.symbols[s].module for s in seeds if s in cg.symbols}
    targets = seed_modules | {q for q, s in cg.symbols.items() if s.module in seed_modules}
    importers = {u for t in targets for u, _, k in g.in_edges(t, keys=True)
                 if k == IMPORTS and g.nodes[u].get("is_test")}
    for sym in cg.symbols.values():
        if sym.module in importers and sym.is_test and sym.qname not in found:
            found[sym.qname] = depth + 1
    return sorted((cg.symbols[q] for q in found), key=lambda s: (found[s.qname], s.file, s.lineno))


def select_evidence(tb: ParsedTraceback, frames: list[MappedFrame], cg: CodeGraph,
                    limits: Limits = DEFAULT_LIMITS) -> EvidenceBundle:
    primary = tb.primary
    exc_text, exc_red = redact(f"{primary.exc_type}: {primary.message}".rstrip(": "))
    exc_text = exc_text[: limits.max_excerpt_chars]
    items: list[EvidenceItem] = [EvidenceItem(kind="exception", reason="Final exception reported in the traceback.",
                                              excerpt=exc_text, redactions=exc_red)]
    used_symbols: set[str] = set()
    used_module_lines: set[tuple[str, int]] = set()

    # Frames, innermost first; final exception before earlier chained exceptions.
    ordered = sorted((f for f in frames if f.file is not None and f.mapping in ("resolved", "resolved_filename_only")),
                     key=lambda f: (-f.chain_index, -f.index))
    frame_symbols: list[str] = []
    for mf in ordered:
        if mf.symbol and mf.symbol_matches is not False:
            if mf.symbol in used_symbols:
                continue
            used_symbols.add(mf.symbol)
            frame_symbols.append(mf.symbol)
            items.append(_symbol_item(cg, cg.symbols[mf.symbol], "frame",
                                      f"Traceback frame: {mf.function} at line {mf.line}.", mf.line, limits))
        elif (mf.file, mf.line) not in used_module_lines:
            used_module_lines.add((mf.file, mf.line))
            items.append(_module_frame_item(cg, mf, limits))

    # Graph neighbors up to the configured depth, nearest and innermost first.
    depth1: list[EvidenceItem] = []
    deeper: list[EvidenceItem] = []
    frontier = list(frame_symbols)
    for d in range(1, limits.graph_traversal_depth + 1):
        nxt: list[str] = []
        for src in frontier:
            for relation, target in _neighbors(cg, src):
                if target in used_symbols or target not in cg.symbols or cg.symbols[target].is_test:
                    continue
                used_symbols.add(target)
                nxt.append(target)
                item = _symbol_item(cg, cg.symbols[target], relation,
                                    f"{relation.replace('_', ' ')} of {src} ({d} hop{'s' if d > 1 else ''} from a frame).",
                                    None, limits)
                (depth1 if d == 1 else deeper).append(item)
        frontier = nxt

    tests = related_tests(cg, frame_symbols, limits.graph_traversal_depth)
    test_items = [
        _symbol_item(cg, t, "related_test", "Statically discovered test related to a frame symbol (not executed).",
                     None, limits)
        for t in tests
    ]

    candidates = items + depth1 + test_items + deeper
    selected = candidates[: limits.max_evidence_items]
    bundle = EvidenceBundle(frames=frames, items=selected, related_tests=tests,
                            candidates_considered=len(candidates))
    if len(candidates) > len(selected):
        bundle.notes.append(f"{len(candidates) - len(selected)} evidence candidate(s) exceeded the item limit.")
    renumber(bundle)
    return bundle


def renumber(bundle: EvidenceBundle) -> None:
    for i, item in enumerate(bundle.items, start=1):
        item.id = f"E{i}"
    bundle.redactions = sum(i.redactions for i in bundle.items)
