"""Preflight. Touches the emulator read-only, under the shared lock. --keys makes one tiny call per model."""

import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from simula import config, decide, llm, runlog
from simula.cli import mobile_mcp_version, package_version
from simula.config import ROOT

LOCK = Path("/tmp/simula-emu.lock")
EXPECTED = {"mobile-mcp": "1.0.5", "mcp": "2.2.0"}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'ok ' if ok else 'FAIL'}  {name:<60} {detail}")
    return ok


def adb() -> str | None:
    home = os.environ.get("ANDROID_HOME") or str(Path.home() / "Library/Android/sdk")
    path = Path(home) / "platform-tools" / "adb"
    return str(path) if path.exists() else shutil.which("adb")


@contextmanager
def emulator_lock(wait_s: int = 120):
    deadline = time.monotonic() + wait_s
    while True:
        try:
            LOCK.mkdir()
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{LOCK} held by another process for {wait_s}s")
            time.sleep(5)
    try:
        yield
    finally:
        LOCK.rmdir()


def check_local() -> None:
    check("python >= 3.12", sys.version_info >= (3, 12), sys.version.split()[0])
    for pkg in ("anthropic", "openai", "typesafe-sdk", "system-one-adapter"):
        version = package_version(pkg)
        check(f"package {pkg}", version is not None, version or "missing")
    check("package mcp", package_version("mcp") == EXPECTED["mcp"], f"{package_version('mcp')} (want {EXPECTED['mcp']})")
    installed = (ROOT / "node_modules/@mobilenext/mobile-mcp/package.json").exists()
    version = mobile_mcp_version()
    check("mobile-mcp pinned + installed", installed and version == EXPECTED["mobile-mcp"],
          f"{version} (want {EXPECTED['mobile-mcp']}){'' if installed else ', run npm ci'}")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY"):
        check(f"env {name}", bool(os.environ.get(name)), "set" if os.environ.get(name) else "missing from .env")


def check_browser() -> None:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 411, "height": 914}, device_scale_factor=2.625)
            page.set_content("<body style='margin:0;background:#123'></body>")
            png = page.screenshot()
            browser.close()
        from io import BytesIO
        from PIL import Image
        size = Image.open(BytesIO(png)).size
        check("playwright chromium renders", True, f"{size[0]}x{size[1]} at 411x914@2.625 (expected 1079x2399)")
    except Exception as e:  # noqa: BLE001 - doctor reports any launch failure
        check("playwright chromium renders", False, f"{type(e).__name__}: run `uv run playwright install chromium`")


def check_emulator() -> None:
    tool = adb()
    if not check("adb found", tool is not None, tool or "set ANDROID_HOME or `source setup/env.sh`"):
        return
    try:
        with emulator_lock():
            devices = subprocess.run([tool, "devices"], capture_output=True, text=True, timeout=20).stdout
            online = [line.split()[0] for line in devices.splitlines()[1:] if line.endswith("\tdevice")]
            emulators = [serial for serial in online if serial.startswith("emulator-")]
            if not check("emulator online", bool(emulators), ", ".join(online) or "no device"):
                return
            serial = emulators[0]
            for app in sorted(p.stem for p in (config.CONFIG / "apps").glob("*.toml")):
                package = config.app_config(app)["package"]
                out = subprocess.run([tool, "-s", serial, "shell", "dumpsys", "package", package],
                                     capture_output=True, text=True, timeout=30).stdout
                version = next((line.split("=", 1)[1] for line in out.splitlines() if "versionName=" in line), None)
                check(f"app {app} installed", version is not None, f"{package} {version or 'not installed'}")
    except TimeoutError as e:
        check("emulator lock", False, str(e))


class Ping(BaseModel):
    ok: bool
    word: str


PING = [{"role": "user", "content": [{"type": "text", "text": 'Reply with ok=true and word="ready".'}]}]


def log_spend(decider: str, model: str, label: str, tokens_in: int = 0, tokens_out: int = 0, usd: float = 0.0,
              outcome: str = "ok", effort: str | None = None, confidence: float | None = None) -> None:
    """Every paid doctor probe leaves one line in build/trace.jsonl, so doctor runs need no hand-typed notes."""
    runlog.trace(runlog.BUILD_TRACE, stage="build", step="doctor", decider=decider, model=model, effort=effort,
                 confidence=confidence, tokens_in=tokens_in, tokens_out=tokens_out, usd=round(usd, 6),
                 outcome=outcome, note=label)


def ping(label: str, model: str, effort: str | None, max_tokens: int = 1024) -> dict:
    provider = config.models()[model]["provider"]
    started = time.monotonic()
    try:
        reply = llm.PROVIDERS[provider](model, "", PING, effort, Ping, max_tokens)
        parsed = Ping.model_validate_json(reply.text)
    except Exception as e:  # noqa: BLE001 - doctor reports any provider failure
        check(label, False, f"{type(e).__name__}: {str(e)[:160]}")
        log_spend("model", model, label, outcome="error", effort=effort)
        return {"ok": False, "error": type(e).__name__}
    cost = llm.usd(model, reply.tokens_in, reply.tokens_out, reply.tokens_cached)
    log_spend("model", model, label, reply.tokens_in, reply.tokens_out, cost, effort=effort)
    check(label, parsed.ok, f"{reply.model} · {reply.tokens_in}/{reply.tokens_out} tok · ${cost:.4f} · "
                            f"{time.monotonic() - started:.1f}s · stop={reply.stop_reason}")
    for key, value in sorted(reply.headers.items()):
        print(f"        {key}: {value}")
    return {"ok": parsed.ok, "seconds": round(time.monotonic() - started, 1), "usd": round(cost, 5)}


def haiku_effort_probe() -> bool:
    """Returns whether Haiku accepts output_config.effort. The dev profile never sends it either way."""
    import anthropic
    client = anthropic.Anthropic(max_retries=0, timeout=60)
    model = "claude-haiku-4-5-20251001"
    try:
        message = client.messages.create(model=model, max_tokens=16, output_config={"effort": "low"},
                                         messages=[{"role": "user", "content": "Say ok."}])
    except anthropic.BadRequestError as e:
        check("haiku with effort", True, f"rejected as expected: {str(e)[:100]}")
        log_spend("model", model, "haiku with effort (rejected, unbilled)", outcome="error", effort="low")
        return False
    usage = message.usage
    log_spend("model", model, "haiku with effort", usage.input_tokens, usage.output_tokens,
              llm.usd(model, usage.input_tokens, usage.output_tokens), effort="low")
    check("haiku with effort", True, "accepted (dev profile still omits it)")
    return True


def jev_probe(backend: str, n_options: int) -> dict:
    labels = [f"tap option {i}" for i in range(1, n_options)] + ["Upgrade to premium (subscription paywall button)"]
    state = "Explorer candidates on an app's home screen. Goal: find the paywall."
    try:
        result = decide.ask_choice(state, "Which tap most likely reveals a paywall?", labels, backend)
    except Exception as e:  # noqa: BLE001 - doctor reports any backend failure
        check(f"jev {backend} ({n_options} options)", False, f"{type(e).__name__}: {str(e)[:160]}")
        log_spend("jev", decide.ADAPTER_MODEL if backend == "adapter" else "jev-latest",
                  f"jev {backend} probe", outcome="error")
        return {"ok": False, "error": type(e).__name__}
    right = result.option_id == f"o{n_options:02d}"
    log_spend("jev", result.model, f"jev {backend} probe ({n_options} options)", result.tokens_in,
              result.tokens_out, result.usd, confidence=result.confidence)
    check(f"jev {backend} ({n_options} options)", right,
          f"{result.model} picked {result.option_id} conf {result.confidence:.2f} · {result.seconds:.2f}s "
          f"· {result.tokens_in}/{result.tokens_out} tok · ${result.usd:.5f}")
    return {"ok": right, "model": result.model, "seconds": round(result.seconds, 2)}


PROBE_MAX_TOKENS = 4096


def role_probes() -> dict[tuple, list[str]]:
    """Each distinct (model, effort, max_tokens) the real profile uses, with the roles that use it.
    A declared fallback is probed at its role's effort."""
    probes: dict[tuple, list[str]] = {}
    for name, role in config.roles("real").items():
        if role["model"] == "jev-latest":
            continue
        models = [role["model"]] + ([role["declared_fallback"]] if "declared_fallback" in role else [])
        for model in models:
            efforts = {role.get("effort"), role.get("effort_last_round")} - {None} or {None}
            for effort in efforts:
                key = (model, effort, role.get("max_tokens", PROBE_MAX_TOKENS))
                probes.setdefault(key, []).append(name if model == role["model"] else f"{name} fallback")
    probes.setdefault(("claude-haiku-4-5-20251001", None, PROBE_MAX_TOKENS), []).append("dev profile, jev adapter")
    return probes


def check_keys() -> None:
    found = {}
    for (model, effort, max_tokens), roles in sorted(role_probes().items(), key=lambda kv: kv[0][0]):
        label = f"{model} {effort or 'no effort'} {max_tokens // 1000}k"
        found[f"{model}-{effort or 'none'}-{max_tokens}"] = ping(f"{label} ({', '.join(roles)})"[:60], model, effort,
                                                                   max_tokens=max_tokens)
    found["jev-typesafe"] = jev_probe("typesafe", 16)
    found["jev-adapter"] = jev_probe("adapter", 10)
    haiku_effort = haiku_effort_probe()
    lines = [f"# Written by `simula doctor --keys` at {time.strftime('%Y-%m-%d %H:%M')}. Local only; gitignored.", ""]
    for name, info in found.items():
        lines.append(f'[models."{name}"]')
        lines += [f"{k} = {str(v).lower() if isinstance(v, bool) else repr(v)}" for k, v in info.items()]
        lines.append("")
    lines += ['[models."claude-haiku-4-5-20251001-effort"]', f"accepts_effort = {str(haiku_effort).lower()}", ""]
    (config.CONFIG / "models.local.toml").write_text("\n".join(lines).replace("'", '"'))


def main(keys: bool = False) -> int:
    results.clear()
    check_local()
    check_browser()
    check_emulator()
    if keys:
        check_keys()
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0
