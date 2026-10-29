"""MCP stdio server exposing exactly two tools: analyze_repository and diagnose_failure."""

from __future__ import annotations

import json
import logging

import anyio
import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from . import __version__
from .config import DEFAULT_LIMITS
from .core import analyze_repository, diagnose_failure
from .errors import ErrorCode, PatchProofError
from .schemas import (
    ANALYZE_INPUT_SCHEMA,
    ANALYZE_OUTPUT_SCHEMA,
    DIAGNOSE_INPUT_SCHEMA,
    DIAGNOSE_OUTPUT_SCHEMA,
    schema_errors,
)
from .tb_parse import validate_traceback_text

log = logging.getLogger("patchproof")

TOOLS = [
    types.Tool(
        name="analyze_repository",
        description=(
            "Statically index the Python source under an explicit absolute repository_root and summarize the "
            "symbol graph (imports, calls, inheritance, statically discovered tests). Never executes code. "
            "The graph is rebuilt on every call."
        ),
        inputSchema=ANALYZE_INPUT_SCHEMA,
        outputSchema=ANALYZE_OUTPUT_SCHEMA,
        annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                                          openWorldHint=False),
    ),
    types.Tool(
        name="diagnose_failure",
        description=(
            "Map a Python traceback onto a freshly built graph of the repository at repository_root, select "
            "bounded, redacted source evidence, and ask NVIDIA Nemotron (Nebius Token Factory) to rank root-cause "
            "hypotheses that cite evidence IDs. Returns 'diagnosed' or 'inconclusive'. Does not run code or tests, "
            "does not generate patches, and never claims reproduction or verification. Sends selected excerpts "
            "(not the repository) to Nebius."
        ),
        inputSchema=DIAGNOSE_INPUT_SCHEMA,
        outputSchema=DIAGNOSE_OUTPUT_SCHEMA,
        annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False,
                                          openWorldHint=True),
    ),
]


def _error_result(err: PatchProofError) -> types.CallToolResult:
    payload = err.to_dict()
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


def handle_call(name: str, arguments: dict | None) -> dict:
    """Validate and dispatch a tool call. Raises PatchProofError on failure."""
    args = arguments if isinstance(arguments, dict) else {}
    if name == "analyze_repository":
        errors = schema_errors(args, ANALYZE_INPUT_SCHEMA)
        if errors:
            raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "Invalid arguments: " + "; ".join(errors))
        return analyze_repository(args["repository_root"])
    if name == "diagnose_failure":
        # Size check first so an oversized traceback gets its specific error code.
        if "traceback" in args:
            validate_traceback_text(args["traceback"], DEFAULT_LIMITS)
        errors = schema_errors(args, DIAGNOSE_INPUT_SCHEMA)
        if errors:
            raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "Invalid arguments: " + "; ".join(errors))
        return diagnose_failure(args["repository_root"], args["traceback"])
    raise PatchProofError(ErrorCode.INVALID_ARGUMENT, "Unknown tool.")


def build_server() -> Server:
    server: Server = Server("patchproof", version=__version__)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return TOOLS

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict | None):
        try:
            # Blocking work (file I/O, HTTP) runs off the event loop.
            return await anyio.to_thread.run_sync(handle_call, name, arguments)
        except PatchProofError as err:
            log.info("tool %s failed: %s", name, err.code.value)
            return _error_result(err)
        except Exception:  # never leak internals or untrusted content in errors
            log.exception("unexpected error in tool %s", name)
            return _error_result(PatchProofError(ErrorCode.INTERNAL_ERROR, "Unexpected internal error."))

    return server


async def _run() -> None:
    server = build_server()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="patchproof",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(), experimental_capabilities={}
                ),
            ),
        )


def run_stdio() -> None:
    anyio.run(_run)
