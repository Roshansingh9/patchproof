"""Minimal Nebius Token Factory client (OpenAI-compatible chat completions).

No automatic retries: network and provider failures surface as stable error codes.
The API key is read from the environment only and is never logged or returned.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import HTTP_TIMEOUT_SECONDS, NEBIUS_API_KEY_ENV, NEBIUS_BASE_URL
from .errors import ErrorCode, PatchProofError


@dataclass
class Completion:
    content: str
    finish_reason: str | None
    usage: dict | None


class ChatClient(Protocol):
    model: str

    def complete(self, messages: list[dict], max_tokens: int) -> Completion: ...


def api_key_from_env() -> str:
    key = os.environ.get(NEBIUS_API_KEY_ENV, "").strip()
    if not key:
        raise PatchProofError(
            ErrorCode.MISSING_API_KEY,
            f"Set the {NEBIUS_API_KEY_ENV} environment variable in the MCP server's environment.",
        )
    return key


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    code = resp.status_code
    if code in (401, 403):
        raise PatchProofError(ErrorCode.PROVIDER_AUTH_ERROR, f"Nebius rejected the credentials (HTTP {code}).")
    if code == 404:
        raise PatchProofError(ErrorCode.PROVIDER_MODEL_NOT_FOUND,
                              "Nebius returned 404; check PATCHPROOF_MODEL against the model catalog.")
    if code == 429:
        raise PatchProofError(ErrorCode.PROVIDER_RATE_LIMITED, "Nebius rate limit or quota exceeded (HTTP 429).")
    if code == 400 and "model" in resp.text.lower():
        raise PatchProofError(ErrorCode.PROVIDER_MODEL_NOT_FOUND,
                              "Nebius rejected the request (HTTP 400); the model ID may be unavailable.")
    raise PatchProofError(ErrorCode.PROVIDER_ERROR, f"Nebius returned HTTP {code}.")


class NebiusClient:
    def __init__(self, model: str, api_key: str | None = None, base_url: str = NEBIUS_BASE_URL,
                 timeout: float = HTTP_TIMEOUT_SECONDS, transport: httpx.BaseTransport | None = None) -> None:
        self.model = model
        self._key = api_key or api_key_from_env()
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                    headers={"Authorization": f"Bearer {self._key}"})

    def __repr__(self) -> str:  # never expose the key
        return f"NebiusClient(model={self.model!r})"

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise PatchProofError(ErrorCode.NETWORK_ERROR, "Request to Nebius timed out.") from None
        except httpx.HTTPError:
            raise PatchProofError(ErrorCode.NETWORK_ERROR, "Could not reach Nebius Token Factory.") from None
        _raise_for_status(resp)
        return resp

    def complete(self, messages: list[dict], max_tokens: int) -> Completion:
        resp = self._request("POST", "chat/completions", json={
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        })
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise PatchProofError(ErrorCode.PROVIDER_ERROR, "Nebius returned an unexpected response shape.") from None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if usage:
            usage = {k: v for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens")
                     and isinstance(v, int)}
        return Completion(content=content, finish_reason=choice.get("finish_reason"), usage=usage)

    def list_models(self) -> list[str]:
        resp = self._request("GET", "models")
        try:
            return sorted(str(m["id"]) for m in resp.json()["data"])
        except (ValueError, KeyError, TypeError):
            raise PatchProofError(ErrorCode.PROVIDER_ERROR, "Unexpected model catalog response shape.") from None

    def close(self) -> None:
        self._client.close()


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def extract_json(text: str) -> object:
    """Parse a JSON object from model text, tolerating think blocks and code fences."""
    cleaned = _THINK.sub("", text).strip()
    m = _FENCE.match(cleaned)
    if m:
        cleaned = m.group(1)
    try:
        return json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start: end + 1])
        raise
