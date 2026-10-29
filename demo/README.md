# PatchProof hosted demo

A minimal Streamlit page that runs `diagnose_failure` on **fixed** inputs only:
the synthetic repository in `examples/sample_repo` and the traceback in
`examples/tracebacks/unknown_coupon.txt`. Visitors cannot upload code, enter
paths or edit the traceback.

This demo is separate from the local MCP server and does not expose it.

## Run locally

```bash
python -m venv .venv-demo
.venv-demo/bin/pip install -r demo/requirements.txt     # Windows: .venv-demo\Scripts\pip
export NEBIUS_API_KEY=...                               # server-side only
streamlit run demo/app.py
```

Headless smoke test (no network, mocked model):

```bash
python demo/smoke_test.py
```

## Deploying (for example, Streamlit Community Cloud)

- Entry point: `demo/app.py`. Dependencies: `demo/requirements.txt`. Community Cloud uses a dependency file
  in the entry point's folder before one in the repository root, so this file takes priority over the root
  `pyproject.toml` ([docs](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)).
- Python version: select **3.12** in the deploy settings. The demo was tested locally on 3.12 only.
- Put the key in the app's secrets as `NEBIUS_API_KEY = "..."`. Never commit it; `.streamlit/secrets.toml`
  is git-ignored.
- Optionally set `PATCHPROOF_MODEL`.
- The demo imports PatchProof from `../src`, so deploy from a checkout of the full repository.

Status: run locally against real Nebius inference (Streamlit 1.50.0, Python 3.12). **Not yet deployed**;
hosted behaviour is unverified.

## Usage limits (built in)

- Results are cached for 10 minutes, so repeated visits share one model call.
- At most 20 model calls per hour across all visitors, per server process.
- At most 3 runs per visitor session.
- Inputs are bounded by PatchProof's normal limits (evidence items, excerpt size, token budgets).

These limits are per server process and are not a substitute for a provider-side spending cap. Set a
budget or quota on the Nebius key you deploy with.
