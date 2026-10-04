"""TH1 (take-home 1, at $SIMULA_TH1, default /Users/aadi/simula-ctr; its data at $SIMULA_TH1_DATA) runs only as a
subprocess: both repos ship a top-level `simula` package, so the tools/ scripts run under TH1's own uv project with TH1
on PYTHONPATH, and talk JSON. Nothing here writes in TH1's tree: --no-sync keeps uv off its .venv and
PYTHONDONTWRITEBYTECODE keeps Python out of its __pycache__."""

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from simula.config import ROOT

TH1 = Path(os.environ.get("SIMULA_TH1", "/Users/aadi/simula-ctr")).expanduser().resolve()
DATA = Path(os.environ.get("SIMULA_TH1_DATA", "/Users/aadi/Desktop/Simula/ml-takehome/data")).expanduser().resolve()
BUNDLE = TH1 / "bundle"
TOOLS = ROOT / "tools"
SAMPLE_SEED = 20261004


def command(script: str, *args: str) -> list[str]:
    """The command that runs tools/<script> in TH1's environment; run it with cwd=TH1 and env()."""
    return ["uv", "run", "--no-sync", "--project", str(TH1), "python", str(TOOLS / script), *args]


def env() -> dict[str, str]:
    """This process's environment with TH1 on PYTHONPATH, no bytecode writes, and exp-connect's virtualenv dropped."""
    kept = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return {**kept, "PYTHONPATH": str(TH1), "PYTHONDONTWRITEBYTECODE": "1"}


def _run(script: str, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(command(script, *args), cwd=TH1, env=env(), input=stdin, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"tools/{script} failed with exit {done.returncode}: {done.stderr[-2000:]}")
    return done.stdout


def bridge(payloads: list[dict], *, bundle: Path = BUNDLE, data: Path = DATA, policy: dict | None = None) -> list[dict]:
    """TH1's rank() on each payload, unchanged, in TH1's process; each decision carries its payload's id."""
    args = ["--model", str(bundle), "--data", str(data), *(["--policy", json.dumps(policy)] if policy else [])]
    return json.loads(_run("th1_bridge.py", *args, stdin=json.dumps(payloads)))


def starvation(out_dir: Path, *, requests: int = 10_000, seed: int = SAMPLE_SEED, bundle: Path = BUNDLE,
               data: Path = DATA) -> dict:
    """Runs tools/th1_starvation.py, which writes starvation.json and starvation_decisions.jsonl into out_dir; returns
    the summary. About 4 minutes for 10,000 requests."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run("th1_starvation.py", "--model", str(bundle), "--data", str(data), "--out", str(out_dir.resolve()),
         "--requests", str(requests), "--seed", str(seed))
    return json.loads((out_dir / "starvation.json").read_text())


def decisions(out_dir: Path) -> Iterator[dict]:
    """The per-request records th1_starvation.py wrote into out_dir, in sample order."""
    with open(out_dir / "starvation_decisions.jsonl") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
