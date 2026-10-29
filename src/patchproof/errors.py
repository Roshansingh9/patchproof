"""Stable error codes. Messages are fixed strings or contain only safe values."""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    REPOSITORY_NOT_FOUND = "REPOSITORY_NOT_FOUND"
    REPOSITORY_INVALID = "REPOSITORY_INVALID"
    NO_PYTHON_SOURCES = "NO_PYTHON_SOURCES"
    TRACEBACK_EMPTY = "TRACEBACK_EMPTY"
    TRACEBACK_TOO_LARGE = "TRACEBACK_TOO_LARGE"
    TRACEBACK_INVALID = "TRACEBACK_INVALID"
    MISSING_API_KEY = "MISSING_API_KEY"
    PROVIDER_AUTH_ERROR = "PROVIDER_AUTH_ERROR"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_MODEL_NOT_FOUND = "PROVIDER_MODEL_NOT_FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PatchProofError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"error": {"code": self.code.value, "message": self.message}}
