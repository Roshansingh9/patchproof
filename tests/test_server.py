"""MCP contract tests: in-process dispatch plus a real stdio server subprocess."""

import json
import os
import sys

import anyio
import pytest
from conftest import SAMPLE_TB

from patchproof.errors import ErrorCode, PatchProofError
from patchproof.server import TOOLS, handle_call


def test_exactly_two_tools():
    assert [t.name for t in TOOLS] == ["analyze_repository", "diagnose_failure"]
    for t in TOOLS:
        assert t.inputSchema["additionalProperties"] is False
        assert t.outputSchema["additionalProperties"] is False
        assert t.inputSchema["required"][0] == "repository_root"


@pytest.mark.parametrize("name,args,code", [
    ("analyze_repository", {}, ErrorCode.INVALID_ARGUMENT),
    ("analyze_repository", {"repository_root": "/x", "api_key": "k"}, ErrorCode.INVALID_ARGUMENT),
    ("analyze_repository", {"repository_root": 5}, ErrorCode.INVALID_ARGUMENT),
    ("diagnose_failure", {"repository_root": "/x"}, ErrorCode.INVALID_ARGUMENT),
    ("diagnose_failure", {"repository_root": "/x", "traceback": "x" * 70000}, ErrorCode.TRACEBACK_TOO_LARGE),
    ("delete_everything", {}, ErrorCode.INVALID_ARGUMENT),
])
def test_dispatch_errors(name, args, code):
    with pytest.raises(PatchProofError) as ei:
        handle_call(name, args)
    assert ei.value.code == code


def test_api_key_is_not_an_argument(sample_root):
    with pytest.raises(PatchProofError):
        handle_call("diagnose_failure", {"repository_root": sample_root, "traceback": SAMPLE_TB,
                                         "NEBIUS_API_KEY": "abc"})


async def _stdio_session(sample_root: str, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {k: v for k, v in os.environ.items() if k != "NEBIUS_API_KEY"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "patchproof"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]

            ok = await session.call_tool("analyze_repository", {"repository_root": sample_root})
            bad_root = await session.call_tool("analyze_repository", {"repository_root": "relative"})
            outside = await session.call_tool("analyze_repository",
                                              {"repository_root": str(tmp_path / "does-not-exist")})
            no_key = await session.call_tool("diagnose_failure",
                                             {"repository_root": sample_root, "traceback": SAMPLE_TB})
            empty_tb = await session.call_tool("diagnose_failure",
                                               {"repository_root": sample_root, "traceback": "   "})
            return names, ok, bad_root, outside, no_key, empty_tb


def test_stdio_server_end_to_end(sample_root, tmp_path):
    names, ok, bad_root, outside, no_key, empty_tb = anyio.run(_stdio_session, sample_root, tmp_path)
    assert names == ["analyze_repository", "diagnose_failure"]
    assert ok.isError is False
    assert ok.structuredContent["tool"] == "analyze_repository"
    assert ok.structuredContent["graph"]["test_functions"] == 4
    assert json.loads(ok.content[0].text)["tool"] == "analyze_repository"
    assert bad_root.isError and bad_root.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    assert outside.isError and outside.structuredContent["error"]["code"] == "REPOSITORY_NOT_FOUND"
    assert no_key.isError and no_key.structuredContent["error"]["code"] == "MISSING_API_KEY"
    assert empty_tb.isError and empty_tb.structuredContent["error"]["code"] == "TRACEBACK_EMPTY"
