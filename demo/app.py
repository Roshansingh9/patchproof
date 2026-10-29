"""PatchProof hosted demo (Streamlit).

Fixed inputs only: the synthetic repository in examples/sample_repo and one
bundled traceback. There is no upload, no free-text input and no filesystem
path input. The Nebius key stays server-side (Streamlit secrets or env).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import streamlit as st

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from patchproof.config import NEBIUS_API_KEY_ENV, model_id  # noqa: E402
from patchproof.core import analyze_repository, diagnose_failure  # noqa: E402
from patchproof.errors import PatchProofError  # noqa: E402

SAMPLE_REPO = PROJECT / "examples" / "sample_repo"
TRACEBACK = (PROJECT / "examples" / "tracebacks" / "unknown_coupon.txt").read_text(encoding="utf-8")

MAX_MODEL_CALLS_PER_HOUR = 20  # global, across all visitors
MAX_RUNS_PER_SESSION = 3
RESULT_CACHE_SECONDS = 600


def _load_key() -> bool:
    if os.environ.get(NEBIUS_API_KEY_ENV):
        return True
    try:
        key = st.secrets.get(NEBIUS_API_KEY_ENV)
    except Exception:  # no secrets file configured
        key = None
    if key:
        os.environ[NEBIUS_API_KEY_ENV] = str(key)
        return True
    return False


@st.cache_resource
def _call_budget() -> dict:
    return {"lock": threading.Lock(), "window_start": time.time(), "count": 0}


def _take_model_call() -> bool:
    budget = _call_budget()
    with budget["lock"]:
        now = time.time()
        if now - budget["window_start"] >= 3600:
            budget["window_start"], budget["count"] = now, 0
        if budget["count"] >= MAX_MODEL_CALLS_PER_HOUR:
            return False
        budget["count"] += 1
        return True


@st.cache_data(ttl=RESULT_CACHE_SECONDS, show_spinner=False)
def _cached_diagnosis(model: str) -> dict:
    # Inputs are fixed, so identical requests within the TTL share one model call.
    # Exceptions are not cached, so a transient failure does not stick.
    if not _take_model_call():
        raise _RateLimited()
    return diagnose_failure(str(SAMPLE_REPO), TRACEBACK)


class _RateLimited(Exception):
    pass


def _diagnose() -> dict:
    try:
        return _cached_diagnosis(model_id())
    except _RateLimited:
        return {"error": {"code": "DEMO_RATE_LIMITED",
                          "message": "The demo's hourly model budget is used up. Please try again later."}}
    except PatchProofError as err:
        return err.to_dict()


@st.cache_data(show_spinner=False)
def _analysis() -> dict:
    return analyze_repository(str(SAMPLE_REPO))


def _render_diagnosis(result: dict) -> None:
    st.warning(result["verification"]["note"])
    status = result["status"]
    st.subheader(f"Status: {status}")
    st.write(result["summary"])
    for h in result["hypotheses"]:
        with st.container(border=True):
            st.markdown(f"**#{h['rank']} · {h['title']}** — confidence: `{h['confidence']}` · "
                        f"model hypothesis, not a verified fact")
            st.write(h["explanation"])
            locs = ", ".join(f"{loc['evidence_id']} → {loc['file'] or 'traceback'}"
                             + (f":{loc['line_start']}-{loc['line_end']}" if loc["line_start"] else "")
                             for loc in h["locations"])
            st.caption(f"Cited evidence: {locs}")
    if result["missing_information"]:
        st.markdown("**Missing information**")
        for m in result["missing_information"]:
            st.markdown(f"- {m}")
    _render_evidence(result)
    st.caption(f"Model: {result['model']['model']} via Nebius Token Factory · "
               f"repair attempted: {result['model']['repair_attempted']} · "
               f"evidence items sent: {result['privacy']['evidence_items_sent']} · "
               f"redactions: {result['privacy']['redactions_applied']}")


def _render_evidence(result: dict) -> None:
    st.markdown("**Deterministic facts (static analysis)**")
    frames = [{k: f[k] for k in ("function", "file", "line", "symbol", "mapping", "source_matches")}
              for f in result["facts"]["frames"]]
    st.dataframe(frames, width="stretch")
    st.markdown(f"**Evidence sent to the model ({len(result['evidence'])} items)**")
    for e in result["evidence"]:
        label = f"{e['id']} · {e['kind']} · {e['symbol'] or e['file'] or 'exception'}"
        with st.expander(label):
            st.caption(e["reason"])
            st.code(e["excerpt"], language="python" if e["kind"] != "exception" else None)
    if result["related_tests"]:
        st.markdown("**Statically related tests (not executed)**")
        st.write([t["symbol"] for t in result["related_tests"]])


def main() -> None:
    st.set_page_config(page_title="PatchProof demo", page_icon="🔎", layout="wide")
    st.title("PatchProof demo")
    st.markdown(
        "PatchProof maps a Python traceback onto a static code graph of a repository, selects bounded and "
        "redacted source evidence, and asks **NVIDIA Nemotron on Nebius Token Factory** to rank root-cause "
        "hypotheses that cite that evidence. It never runs code or tests and never claims a fix is verified."
    )
    st.info(
        "**Demo limits:** this page only analyzes a fixed synthetic repository with one bundled traceback. "
        f"No uploads or custom inputs. Results are cached for {RESULT_CACHE_SECONDS // 60} minutes; at most "
        f"{MAX_MODEL_CALLS_PER_HOUR} model calls per hour in total and {MAX_RUNS_PER_SESSION} runs per visitor "
        "session. For your own code, install the local MCP server (see the project README)."
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**Traceback (fixed)**")
        st.code(TRACEBACK, language="text")
    with right:
        st.markdown("**Synthetic repository** (`examples/sample_repo`)")
        for path in sorted((SAMPLE_REPO / "shop").glob("*.py")):
            with st.expander(f"shop/{path.name}"):
                st.code(path.read_text(encoding="utf-8"), language="python")

    analysis = _analysis()
    g = analysis["graph"]
    st.caption(f"Static graph: {g['modules']} modules · {g['functions'] + g['methods']} functions/methods · "
               f"{g['edges']['calls']} call edges · {g['edges']['imports']} import edges · "
               f"{g['test_functions']} tests discovered (not run)")

    has_key = _load_key()
    if not has_key:
        st.error("This deployment has no Nebius API key configured, so live diagnosis is unavailable.")
    runs = st.session_state.setdefault("runs", 0)
    disabled = not has_key or runs >= MAX_RUNS_PER_SESSION
    if st.button("Diagnose with Nemotron", type="primary", disabled=disabled):
        st.session_state["runs"] = runs + 1
        with st.spinner("Building graph, selecting evidence, calling Nemotron..."):
            result = _diagnose()
        if "error" in result:
            st.error(f"{result['error']['code']}: {result['error']['message']}")
        else:
            _render_diagnosis(result)
    elif runs >= MAX_RUNS_PER_SESSION:
        st.warning("Per-session run limit reached.")


main()
