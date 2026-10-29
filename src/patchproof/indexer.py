"""Confined, bounded discovery and parsing of project Python source.

Never executes or imports repository code. Never follows symlinks or junctions.
Every file read is checked to resolve inside the repository root.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import DEFAULT_LIMITS, EXCLUDED_DIR_SUFFIXES, Limits
from .paths import is_link_like, is_within


@dataclass
class SourceFile:
    rel_path: str  # POSIX-style path relative to the repository root
    module: str  # dotted module name
    is_package: bool  # True for __init__.py
    text: str
    tree: ast.Module
    lines: list[str]


@dataclass
class Coverage:
    files_indexed: int = 0
    bytes_indexed: int = 0
    skipped_symlinks: int = 0
    skipped_excluded_dirs: int = 0
    skipped_oversized_files: int = 0
    skipped_unreadable_files: int = 0
    skipped_outside_root: int = 0
    parse_errors: list[dict] = field(default_factory=list)
    file_limit_reached: bool = False
    total_bytes_limit_reached: bool = False

    @property
    def partial(self) -> bool:
        return bool(
            self.file_limit_reached
            or self.total_bytes_limit_reached
            or self.skipped_oversized_files
            or self.skipped_unreadable_files
            or self.skipped_outside_root
            or self.parse_errors
        )

    def to_dict(self, max_parse_errors: int) -> dict:
        notes: list[str] = []
        if self.file_limit_reached:
            notes.append("Python file limit reached; remaining files were not indexed.")
        if self.total_bytes_limit_reached:
            notes.append("Total source size limit reached; remaining files were not indexed.")
        if self.skipped_oversized_files:
            notes.append(f"{self.skipped_oversized_files} file(s) exceeded the per-file size limit.")
        if self.skipped_unreadable_files:
            notes.append(f"{self.skipped_unreadable_files} file(s) were unreadable or not UTF-8.")
        if self.skipped_outside_root:
            notes.append(f"{self.skipped_outside_root} path(s) resolved outside the repository root.")
        if self.parse_errors:
            notes.append(f"{len(self.parse_errors)} file(s) had syntax errors and were not graphed.")
        if self.skipped_symlinks:
            notes.append(f"{self.skipped_symlinks} symlink(s)/junction(s) were not followed.")
        return {
            "partial": self.partial,
            "files_indexed": self.files_indexed,
            "bytes_indexed": self.bytes_indexed,
            "skipped_symlinks": self.skipped_symlinks,
            "skipped_excluded_dirs": self.skipped_excluded_dirs,
            "skipped_oversized_files": self.skipped_oversized_files,
            "skipped_unreadable_files": self.skipped_unreadable_files,
            "skipped_outside_root": self.skipped_outside_root,
            "parse_error_count": len(self.parse_errors),
            "parse_errors": self.parse_errors[:max_parse_errors],
            "file_limit_reached": self.file_limit_reached,
            "total_bytes_limit_reached": self.total_bytes_limit_reached,
            "notes": notes,
        }


@dataclass
class IndexResult:
    root: Path
    files: list[SourceFile]
    coverage: Coverage


def _is_excluded_dir(name: str, limits: Limits) -> bool:
    return name in limits.excluded_dir_names or name.endswith(EXCLUDED_DIR_SUFFIXES)


def module_name_for(rel_path: str) -> tuple[str, bool]:
    """Map 'src/pkg/mod.py' -> ('pkg.mod', False); 'pkg/__init__.py' -> ('pkg', True)."""
    parts = list(PurePosixPath(rel_path).parts)
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    stem = parts[-1][:-3]  # strip ".py"
    is_package = stem == "__init__"
    parts = parts[:-1] if is_package else parts[:-1] + [stem]
    return (".".join(parts) if parts else "__init__"), is_package


def _discover(root: Path, limits: Limits, coverage: Coverage) -> list[Path]:
    """Deterministic (sorted) walk that never descends through links."""
    found: list[Path] = []
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            coverage.skipped_unreadable_files += 1
            continue
        subdirs: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink() or is_link_like(path):
                coverage.skipped_symlinks += 1
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if _is_excluded_dir(entry.name, limits):
                        coverage.skipped_excluded_dirs += 1
                    else:
                        subdirs.append(path)
                elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".py"):
                    found.append(path)
            except OSError:
                coverage.skipped_unreadable_files += 1
        # Push in reverse so directories are visited in sorted order.
        stack.extend(reversed(subdirs))
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def index_repository(root: Path, limits: Limits = DEFAULT_LIMITS) -> IndexResult:
    """Index Python files under an already-validated root."""
    coverage = Coverage()
    files: list[SourceFile] = []
    total = 0
    for path in _discover(root, limits, coverage):
        if len(files) >= limits.max_python_files:
            coverage.file_limit_reached = True
            break
        if not is_within(root, path):
            coverage.skipped_outside_root += 1
            continue
        try:
            size = path.stat(follow_symlinks=False).st_size
        except OSError:
            coverage.skipped_unreadable_files += 1
            continue
        if size > limits.max_file_bytes:
            coverage.skipped_oversized_files += 1
            continue
        if total + size > limits.max_total_source_bytes:
            coverage.total_bytes_limit_reached = True
            break
        rel = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError:
            coverage.skipped_unreadable_files += 1
            continue
        if len(raw) > limits.max_file_bytes:  # file grew after stat
            coverage.skipped_oversized_files += 1
            continue
        total += len(raw)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            coverage.skipped_unreadable_files += 1
            continue
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError) as exc:
            coverage.parse_errors.append(
                {"file": rel, "line": getattr(exc, "lineno", None), "message": type(exc).__name__}
            )
            continue
        module, is_package = module_name_for(rel)
        files.append(
            SourceFile(
                rel_path=rel,
                module=module,
                is_package=is_package,
                text=text,
                tree=tree,
                lines=text.splitlines(),
            )
        )
    coverage.files_indexed = len(files)
    coverage.bytes_indexed = total
    return IndexResult(root=root, files=files, coverage=coverage)
