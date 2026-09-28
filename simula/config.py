"""Loads the TOML config files. The app config is the entire per-app surface."""

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
STAGES = ("explore", "model", "mock", "qa", "propose", "judge", "flows")


def load_env() -> None:
    load_dotenv(ROOT / ".env", override=False)


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def app_config(app: str) -> dict:
    path = CONFIG / "apps" / f"{app}.toml"
    if not path.exists():
        known = sorted(p.stem for p in (CONFIG / "apps").glob("*.toml"))
        raise SystemExit(f"unknown app {app!r}; known: {', '.join(known)}")
    return {"name": app, **_toml(path)}


def models() -> dict:
    return _toml(CONFIG / "models.toml")["models"]


def profiles() -> dict:
    return _toml(CONFIG / "profiles.toml")


def roles(profile: str) -> dict:
    return profiles()[profile]["roles"]


def budget(name: str) -> dict:
    return profiles()["budgets"][name]


def stage_cap(stage: str) -> float:
    return profiles()["caps_usd"][stage]


def jev_backend(profile: str) -> str:
    return os.environ.get("SIMULA_JEV_BACKEND") or roles(profile)["jev"].get("backend") or profiles()["jev_backend"]
