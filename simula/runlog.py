"""Trace lines, the run manifest, needs-human files, exhibits, and hand-fix notes."""

import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from simula.config import ROOT, STAGES
from simula.contracts import Manifest, TraceLine
from simula.runfolder import read_done, write_json_atomic

BUILD_TRACE = ROOT / "build" / "trace.jsonl"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def trace(path: Path, **fields) -> TraceLine:
    line = TraceLine(ts=now(), **fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(line.model_dump_json() + "\n")
    return line


def run_trace(run_dir: Path, **fields) -> TraceLine:
    return trace(run_dir / "trace.jsonl", **fields)


def read_trace(path: Path) -> list[TraceLine]:
    if not path.exists():
        return []
    return [TraceLine.model_validate_json(line) for line in path.read_text().splitlines() if line]


def write_manifest(run_dir: Path, manifest: Manifest) -> None:
    write_json_atomic(run_dir / "manifest.json", manifest.model_dump_json(indent=1))


def read_manifest(run_dir: Path) -> Manifest:
    return Manifest.model_validate_json((run_dir / "manifest.json").read_text())


def update_manifest(run_dir: Path, **changes) -> Manifest:
    manifest = read_manifest(run_dir).model_copy(update=changes)
    write_manifest(run_dir, manifest)
    return manifest


def sync_manifest(run_dir: Path) -> Manifest:
    """The manifest's stages_done and usd_total, read again from the done markers and the trace, so the manifest never
    lists a stage its marker doesn't show as complete."""
    done = [s for s in STAGES if complete(run_dir, s)]
    usd_total = sum(line.usd for line in read_trace(run_dir / "trace.jsonl"))
    return update_manifest(run_dir, stages_done=done, usd_total=round(usd_total, 4))


def complete(run_dir: Path, stage: str) -> bool:
    """Whether the stage's marker shows it complete. A marker that can't be read counts as not done, with a trace line
    naming it, so it never blocks the run that would rewrite it."""
    try:
        marker = read_done(run_dir / stage)
    except (ValidationError, OSError) as e:
        run_trace(run_dir, stage=stage, step="marker", decider="code", outcome="error",
                  note=f"done.json can't be read, so the stage counts as not done: {e}"[:300])
        return False
    return marker is not None and marker.outcome.status == "complete"


def record_fallback(run_dir: Path, note: str) -> None:
    """Writes a used model fallback into the manifest, when this call belongs to a run."""
    if (run_dir / "manifest.json").exists():
        manifest = read_manifest(run_dir)
        update_manifest(run_dir, fallbacks_used=[*manifest.fallbacks_used, note])


def notify(title: str, message: str) -> bool:
    if platform.system() != "Darwin":
        return False
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0


def needs_human(run_dir: Path, stage: str, what: str, why: str, evidence: list[str], command: str) -> Path:
    path = run_dir / "needs-human.md"
    lines = [f"## {now()} · {stage}", "", f"**Needed:** {what}", "", f"**Why:** {why}", "",
             "**Evidence:**", *[f"- `{e}`" for e in evidence], "", f"**Continue with:** `{command}`", "", ""]
    with open(path, "a") as f:
        f.write("\n".join(lines))
    sent = notify("simula needs you", f"{run_dir.parent.name}: {what}")
    run_trace(run_dir, stage=stage, step="needs_human", decider="code", outcome="blocked",
              note=what if sent else f"{what} (notification failed; see needs-human.md)")
    return path


def resolve_needs_human(run_dir: Path, stage: str) -> None:
    """Once a stage completes, marks its open requests in needs-human.md done, whatever asked for them (its cap, the
    provider, a failed check), so the file never asks a person to continue work that has finished."""
    path = run_dir / "needs-human.md"
    headers = [line for line in path.read_text().splitlines() if line.startswith("## ")] if path.exists() else []
    asked = [h for h in headers if h.endswith((f" · {stage}", f" · {stage} · resolved"))]
    if not asked or asked[-1].endswith(" · resolved"):
        return
    with open(path, "a") as f:
        f.write("\n".join([f"## {now()} · {stage} · resolved", "",
                           f"**Done:** {stage} finished complete, so the requests above for it need nothing more.", "", ""]))
    run_trace(run_dir, stage=stage, step="needs_human", decider="code", note="resolved: the stage finished complete")


def fixture_banner(run_dir: Path) -> str:
    if run_dir.name.endswith("-fixture"):
        return "> **FIXTURE TEST DATA, not a deliverable.**\n\n"
    return ""


def write_exhibit(run_dir: Path, number: int, stage: str, markdown: str) -> Path:
    path = run_dir / "exhibits" / f"{number:02d}-{stage}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(fixture_banner(run_dir) + markdown)
    return path


def note(text: str, usd: float = 0.0, run_dir: Path | None = None) -> TraceLine:
    path = run_dir / "trace.jsonl" if run_dir else BUILD_TRACE
    return trace(path, stage="build" if run_dir is None else "hand_fix", step="note", decider="human",
                 usd=usd, note=text)
