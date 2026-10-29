"""Command line entry point. `patchproof` with no arguments runs the MCP stdio server."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import model_id
from .errors import PatchProofError


def _print(obj: dict) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patchproof", description="PatchProof MCP server and utilities.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the MCP server over stdio (default).")
    sub.add_parser("check-model", help="Check that the configured model is in the Nebius model catalog.")
    p_an = sub.add_parser("analyze", help="Run analyze_repository and print JSON.")
    p_an.add_argument("repository_root")
    p_dx = sub.add_parser("diagnose", help="Run diagnose_failure and print JSON (calls Nebius).")
    p_dx.add_argument("repository_root")
    p_dx.add_argument("traceback_file", help="Path to a text file containing the traceback.")
    args = parser.parse_args(argv)

    # stdout is the MCP channel; all logging goes to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    try:
        if args.command in (None, "serve"):
            from .server import run_stdio

            run_stdio()
            return 0
        if args.command == "check-model":
            from .nebius import NebiusClient

            client = NebiusClient(model_id())
            try:
                models = client.list_models()
            finally:
                client.close()
            nvidia = [m for m in models if "nemotron" in m.lower() or m.lower().startswith("nvidia/")]
            _print({"configured_model": client.model, "available": client.model in models,
                    "nvidia_models_in_catalog": nvidia, "catalog_size": len(models)})
            return 0 if client.model in models else 2
        if args.command == "analyze":
            from .core import analyze_repository

            _print(analyze_repository(args.repository_root))
            return 0
        if args.command == "diagnose":
            from .core import diagnose_failure

            with open(args.traceback_file, encoding="utf-8") as fh:
                text = fh.read()
            _print(diagnose_failure(args.repository_root, text))
            return 0
    except PatchProofError as err:
        _print(err.to_dict())
        return 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
