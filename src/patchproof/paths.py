"""Repository-root validation and confinement checks."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ErrorCode, PatchProofError


def is_link_like(path: str | os.PathLike[str]) -> bool:
    """True for symlinks and, on Windows, directory junctions."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def resolve_repository_root(raw: object) -> Path:
    """Validate the explicit repository root supplied by the caller.

    The root must be a non-empty absolute path to an existing directory and
    must not itself be a symlink or junction.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "repository_root must be a non-empty string.")
    if "\x00" in raw:
        raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "repository_root contains a NUL character.")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "repository_root must be an absolute path.")
    if not candidate.exists():
        raise PatchProofError(ErrorCode.REPOSITORY_NOT_FOUND, "repository_root does not exist.")
    if is_link_like(candidate):
        raise PatchProofError(
            ErrorCode.REPOSITORY_INVALID, "repository_root must not be a symlink or junction."
        )
    if not candidate.is_dir():
        raise PatchProofError(ErrorCode.REPOSITORY_INVALID, "repository_root is not a directory.")
    return candidate.resolve(strict=True)


def is_within(root: Path, path: Path) -> bool:
    """True if `path` resolves to a location inside `root` (both resolved)."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents
