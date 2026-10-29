# PatchProof

**Static, evidence-grounded root-cause ranking for Python failures — as an MCP server for coding agents.**

PatchProof gives Claude Code, Codex and other MCP clients two tools:

| Tool | What it does |
|---|---|
| `analyze_repository` | Statically indexes the Python source under an explicit repository root and builds a NetworkX symbol graph (imports, direct calls, inheritance, statically resolvable method calls, statically discovered tests). |
| `diagnose_failure` | Maps a Python traceback onto a freshly built graph, deterministically selects bounded, redacted source evidence (frames, callers/callees up to 2 hops, related tests), and asks **NVIDIA Nemotron on Nebius Token Factory** to rank root-cause hypotheses. Every hypothesis must cite local evidence IDs, which PatchProof validates before returning. |

### What PatchProof does **not** do

- It does **not** execute repository code, import it, or run tests.
- It does **not** generate or apply patches. Your coding agent stays responsible for changes.
- It **never** claims a failure was reproduced or a fix was verified. Every response contains
  `verification: {code_executed: false, failure_reproduced: false, fix_verified: false}`.
- It does **not** upload your repository. Only selected, redacted excerpts are sent to the model.

## Installation

Requires Python 3.11+ (the pinned `networkx==3.5` requires 3.11). Tested on 3.12 and 3.13.

```bash
pip install "git+https://github.com/<your-account>/patchproof.git"   # placeholder until the repo is published
# or, from a local checkout
pip install .
```

This installs a `patchproof` command. Running it with no arguments starts the MCP server over stdio.

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `NEBIUS_API_KEY` | for `diagnose_failure` | Your Nebius Token Factory API key. Read from the environment only. It is never accepted as a tool argument and never logged. |
| `PATCHPROOF_MODEL` | no | Model ID. Default: `nvidia/Nemotron-3_5-Lightning`. |

API base URL (fixed): `https://api.tokenfactory.nebius.com/v1/` (OpenAI-compatible chat completions).

Check that the configured model exists in your account's catalog:

```bash
patchproof check-model
```

> The default model ID came from a third-party catalog listing. Run `check-model` and set
> `PATCHPROOF_MODEL` to an NVIDIA Nemotron ID from the output if the default is not listed.

## MCP client setup

PatchProof never reads a `.env` file. Your MCP client must start the server with `NEBIUS_API_KEY` in
its environment. If `patchproof` is not on `PATH`, use the absolute path to the executable in your
virtual environment (for example `.venv/Scripts/patchproof.exe` on Windows or `.venv/bin/patchproof`
elsewhere), or `python -m patchproof`.

### Claude Code

**Recommended: keep the key out of the config file.** Reference the variable instead of the value. Claude
Code expands `${VAR}` in a server's `command`, `args` and `env` values when it loads the config.

1. Set the key in the environment Claude Code is launched from:

   ```bash
   export NEBIUS_API_KEY=...            # bash/zsh
   ```

   ```powershell
   $env:NEBIUS_API_KEY = "..."          # PowerShell (current session only)
   ```

2. Create a JSON config, for example `.mcp.json` in your project root (project scope) or any file passed
   with `--mcp-config`:

   ```json
   {
     "mcpServers": {
       "patchproof": {
         "type": "stdio",
         "command": "patchproof",
         "args": [],
         "env": { "NEBIUS_API_KEY": "${NEBIUS_API_KEY}" }
       }
     }
   }
   ```

   The file contains only the placeholder, so it is safe to commit. Claude Code asks for approval before it
   uses project-scoped servers from `.mcp.json` in interactive sessions.

3. Check the server with `claude mcp list`. If the variable is not set, Claude Code reports a
   missing-variable warning and passes the literal text `${NEBIUS_API_KEY}`. Nebius should then reject it,
   and PatchProof would report `PROVIDER_AUTH_ERROR` (inferred from the error mapping; not tested here).

Verification status:

- **Tested** with Claude Code 2.1.312 on Windows: a config file loaded with `--mcp-config` (plus
  `--strict-mcp-config`). The key was expanded from the environment, both tools were discovered, and
  `diagnose_failure` completed against real Nebius inference.
- **Documented by Claude Code, not separately tested here:** the same expansion in a project-root
  `.mcp.json` ([Claude Code MCP docs](https://code.claude.com/docs/en/mcp)). This behaviour depends on the
  Claude Code version; check the docs for the version you use.

**Alternative (stores the key in plain text):**

```bash
claude mcp add patchproof -e NEBIUS_API_KEY=your-key -- patchproof
```

This writes the key value into `~/.claude.json` (local scope, the default).

### Codex

```bash
codex mcp add patchproof --env NEBIUS_API_KEY=your-key -- patchproof
```

or the equivalent in `~/.codex/config.toml`:

```toml
[mcp_servers.patchproof]
command = "patchproof"
env = { NEBIUS_API_KEY = "your-key" }
```

Both forms store the key in `~/.codex/config.toml`.

Verification status: with codex-cli 0.137.0, both forms were checked to produce a configuration that
`codex mcp get patchproof` reads correctly. **Codex host integration (Codex actually calling PatchProof's
tools) is not yet verified.**

### Any MCP client

Run the `patchproof` command over stdio with `NEBIUS_API_KEY` set in its environment.

## Usage

Ask your agent, for example:

> Use PatchProof to diagnose this failure in /home/me/project: <paste traceback>

Or call the tools directly from the command line:

```bash
patchproof analyze /abs/path/to/repo
patchproof diagnose /abs/path/to/repo traceback.txt
```

### Tool contracts

Both tools publish strict JSON Schemas (`inputSchema` and `outputSchema`, `additionalProperties: false`).

**`analyze_repository`** input: `{ "repository_root": "<absolute path>" }`
Output: `coverage` (files indexed, skipped items, `partial` flag, notes), `graph` (node and edge counts,
unresolved calls), `modules`, `tests` (statically discovered, never run), `limits`, `notices`.

**`diagnose_failure`** input: `{ "repository_root": "<absolute path>", "traceback": "<full Python traceback>" }`
Output:

- `status`: `diagnosed` or `inconclusive`.
- `hypotheses[]`: `rank`, `title`, `explanation`, `confidence` (`high` / `medium` / `low`), `evidence_ids`,
  `locations` (derived locally from the cited evidence), and `source: "model_hypothesis"`.
- `facts`: deterministic static facts, including the exception and every frame's mapping status
  (`resolved`, `not_in_index`, `outside_repository`, `ambiguous`, `line_out_of_range`) and whether the frame's
  source line and function name still match the working tree.
- `evidence[]`: exactly the excerpts sent to the model, with IDs `E1`…`En`.
- `related_tests[]`: statically related tests, always `executed: false`.
- `missing_information`, `verification`, `coverage`, `privacy`, `model`, `notices`.

If no frame maps to the repository, PatchProof returns `inconclusive` **without calling the model**.

### Error codes

Errors are returned as `{"error": {"code", "message"}}` with `isError: true`. Messages never include source
code, provider responses, or credentials.

`INVALID_ARGUMENT`, `REPOSITORY_NOT_FOUND`, `REPOSITORY_INVALID`, `NO_PYTHON_SOURCES`, `TRACEBACK_EMPTY`,
`TRACEBACK_TOO_LARGE`, `TRACEBACK_INVALID`, `MISSING_API_KEY`, `PROVIDER_AUTH_ERROR`, `PROVIDER_RATE_LIMITED`,
`PROVIDER_MODEL_NOT_FOUND`, `PROVIDER_ERROR`, `NETWORK_ERROR`, `MODEL_OUTPUT_INVALID`, `INTERNAL_ERROR`.

Network and provider errors are **not** retried automatically. If the model returns invalid JSON or
schema-invalid output (including unknown evidence IDs), PatchProof makes exactly **one** repair call.

## Resource limits

All limits live in [`src/patchproof/config.py`](src/patchproof/config.py).

| Limit | Value | Behaviour when exceeded |
|---|---|---|
| Python files | 500 | Stop indexing; coverage marked `partial` |
| Single source file | 256 KiB | File skipped; disclosed |
| Total source | 8 MiB | Stop indexing; disclosed |
| Traceback | 64 KiB | **Rejected**, never truncated |
| Graph traversal | 2 hops | — |
| Evidence items | 24 | Lowest-priority items dropped; disclosed |
| Excerpt | 1,500 chars | Window centred on the failing line |
| Model input | 12,000 tokens (estimated) | Lowest-priority evidence dropped deterministically; rejected if the minimal prompt does not fit |
| Model output | 4,000 tokens | Truncated output fails validation and uses the single repair call |
| Repair attempts | 1 | `MODEL_OUTPUT_INVALID` |

Excluded directories: `.git`, `.hg`, `.svn`, `.venv`, `venv`, `env`, `.env`, `node_modules`, `__pycache__`,
`.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.tox`, `.nox`, `.eggs`, `build`, `dist`, `site-packages`,
`.idea`, `.vscode`, `*.egg-info`.

## Security and privacy

- **Explicit root and confinement.** `repository_root` must be an existing absolute directory and must not be a
  symlink or junction. Indexing never follows symlinks or Windows junctions, and every file read is checked to
  resolve inside the root. Evidence is taken only from already-indexed text; nothing outside the root is read.
- **No execution.** Analysis uses Python's `ast` module only.
- **What leaves your machine.** For `diagnose_failure`, the redacted traceback, deterministic facts, and up to
  24 redacted excerpts (each at most 1,500 characters) are sent to Nebius Token Factory. Your repository is
  never uploaded in full. `analyze_repository` makes no network calls.
- **Secret redaction.** Before inference, PatchProof locally redacts likely secrets: private keys, AWS, GitHub,
  Slack, OpenAI-style and Google keys, JWTs, bearer tokens, credentials in URLs, secret-named assignments, and
  the value of `NEBIUS_API_KEY` itself. **Redaction is heuristic.** Unusual secret formats may not be caught, so
  do not use PatchProof on code whose excerpts you are not permitted to send to Nebius.
- **Prompt injection.** Repository content and tracebacks are treated as untrusted data. They are sent only
  JSON-encoded in the user message, the system prompt instructs the model to ignore embedded instructions, the
  model has no tools, and its output is accepted only if it passes a strict schema with locally validated
  evidence IDs.
- **Stale snapshots.** The graph is rebuilt on every call. Frames whose source line, function name, or line
  range no longer match the working tree are flagged, and a notice warns that the traceback may come from a
  different revision.
- **Logging.** Logs go to stderr and contain only event names and error codes, never source code, tracebacks
  or keys.

## Limitations

- Python only, and only standard CPython traceback text (chained exceptions supported; exception groups and
  pytest's short traceback format are not).
- Static resolution is conservative. Dynamic dispatch, `getattr`, decorators that replace functions,
  monkeypatching, and calls on values of unknown type stay unresolved (counted in `graph.unresolved_calls`).
  Import bindings are module-scoped, so the same name bound to different targets is treated as ambiguous and
  left unresolved.
- Token counts are estimated (about 3 characters per token), not tokenizer-exact.
- Hypotheses are model output. Treat them as leads, and confirm with tests in your own environment.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
pytest
```

The live tests make real, billable calls and are opt-in:

```bash
PATCHPROOF_LIVE_TESTS=1 NEBIUS_API_KEY=... pytest -m live -s
```

The fixture repository [`examples/sample_repo`](examples/sample_repo) contains a deliberate upstream bug:
an unknown coupon makes `parse_coupon` return `None`, which fails later in `apply_discount`.

## Hosted demo

[`demo/`](demo) contains a separate Streamlit app that only diagnoses the bundled synthetic repository and
traceback, with no uploads or custom inputs. It is not the MCP server. See [`demo/README.md`](demo/README.md).

## License

MIT. See [LICENSE](LICENSE).
