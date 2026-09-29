"""Facts about the checkout the CLI and doctor run from: the files a loader reads that git doesn't track, and the
installed tool versions a run records. No stage imports this, so editing it never makes a finished stage stale."""

import importlib.metadata
import json
import subprocess
from pathlib import Path

from simula import runfolder
from simula.config import ROOT
from simula.stages import EXTRA_INPUTS


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def mobile_mcp_version() -> str | None:
    lock = ROOT / "package-lock.json"
    if not lock.exists():
        return None
    packages = json.loads(lock.read_text()).get("packages", {})
    return packages.get("node_modules/@mobilenext/mobile-mcp", {}).get("version")


def loader_files() -> list[Path]:
    """Every file a loader picks up by glob or folder listing rather than by exact name: the prompts, the app
    configs, and the folders stages read outside their run (EXTRA_INPUTS)."""
    extra = [ROOT / path for paths in EXTRA_INPUTS.values() for path in paths]
    return sorted({*(ROOT / "prompts").rglob("*.md"), *(ROOT / "config" / "apps").glob("*.toml"),
                   *runfolder.expand(extra)})


def untracked_inputs() -> list[str] | None:
    """The loader files in the checkout that git doesn't track, ignored ones included, or None when ROOT isn't its
    own git checkout."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    try:
        if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != ROOT.resolve():
            return None
        files = [str(p.relative_to(ROOT)) for p in loader_files() if p.is_relative_to(ROOT)]
        listed = git("--literal-pathspecs", "ls-files", "--others", "-z", "--", *files)
        return sorted(p for p in listed.split("\0") if p)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
