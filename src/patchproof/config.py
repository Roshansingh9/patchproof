"""Central configuration: every resource limit and fixed setting lives here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# --- Repository indexing limits -------------------------------------------
MAX_PYTHON_FILES = 500
MAX_FILE_BYTES = 256 * 1024  # 256 KiB per source file
MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024  # 8 MiB across all processed files

# --- Diagnosis limits -------------------------------------------------------
MAX_TRACEBACK_BYTES = 64 * 1024  # 64 KiB; larger tracebacks are rejected, never truncated
GRAPH_TRAVERSAL_DEPTH = 2  # hops from a traceback frame symbol
MAX_EVIDENCE_ITEMS = 24
MAX_EXCERPT_CHARS = 1_500
MAX_MODEL_INPUT_TOKENS = 12_000
MAX_MODEL_OUTPUT_TOKENS = 4_000
MAX_REPAIR_ATTEMPTS = 1

# Conservative characters-per-token estimate used for budgeting. Lower means
# more conservative (we assume more tokens per character than typical).
CHARS_PER_TOKEN_ESTIMATE = 3.0

# --- Output bounds ----------------------------------------------------------
MAX_LISTED_TESTS = 200
MAX_LISTED_PARSE_ERRORS = 50
MAX_HYPOTHESES = 5

# --- Directory exclusions (matched against each directory name) ------------
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
        "build",
        "dist",
        "site-packages",
        ".idea",
        ".vscode",
    }
)
EXCLUDED_DIR_SUFFIXES = (".egg-info",)

# --- Nebius Token Factory ---------------------------------------------------
NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
NEBIUS_API_KEY_ENV = "NEBIUS_API_KEY"
MODEL_ENV = "PATCHPROOF_MODEL"
DEFAULT_MODEL = "nvidia/Nemotron-3_5-Lightning"
HTTP_TIMEOUT_SECONDS = 90.0

SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class Limits:
    """Snapshot of limits; tests may construct a smaller one."""

    max_python_files: int = MAX_PYTHON_FILES
    max_file_bytes: int = MAX_FILE_BYTES
    max_total_source_bytes: int = MAX_TOTAL_SOURCE_BYTES
    max_traceback_bytes: int = MAX_TRACEBACK_BYTES
    graph_traversal_depth: int = GRAPH_TRAVERSAL_DEPTH
    max_evidence_items: int = MAX_EVIDENCE_ITEMS
    max_excerpt_chars: int = MAX_EXCERPT_CHARS
    max_model_input_tokens: int = MAX_MODEL_INPUT_TOKENS
    max_model_output_tokens: int = MAX_MODEL_OUTPUT_TOKENS
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS
    excluded_dir_names: frozenset[str] = field(default=EXCLUDED_DIR_NAMES)

    def as_dict(self) -> dict[str, int]:
        return {
            "max_python_files": self.max_python_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_source_bytes": self.max_total_source_bytes,
            "max_traceback_bytes": self.max_traceback_bytes,
            "graph_traversal_depth": self.graph_traversal_depth,
            "max_evidence_items": self.max_evidence_items,
            "max_excerpt_chars": self.max_excerpt_chars,
            "max_model_input_tokens": self.max_model_input_tokens,
            "max_model_output_tokens": self.max_model_output_tokens,
            "max_repair_attempts": self.max_repair_attempts,
        }


DEFAULT_LIMITS = Limits()


def model_id() -> str:
    value = os.environ.get(MODEL_ENV, "").strip()
    return value or DEFAULT_MODEL


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN_ESTIMATE) + 1
