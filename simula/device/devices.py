"""Which device a run uses, and the lock that keeps two runs off one device: shared by the explorer and doctor."""

import fcntl
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_DIR = Path("/tmp")


def adb() -> str | None:
    home = os.environ.get("ANDROID_HOME") or str(Path.home() / "Library/Android/sdk")
    path = Path(home) / "platform-tools" / "adb"
    return str(path) if path.exists() else shutil.which("adb")


def online(tool: str) -> list[str]:
    out = subprocess.run([tool, "devices"], capture_output=True, text=True, timeout=20).stdout
    return [line.split()[0] for line in out.splitlines()[1:] if line.endswith("\tdevice")]


def resolve_serial(flag: str | None) -> str:
    """--device, else ANDROID_SERIAL, else the only device adb lists."""
    if flag or os.environ.get("ANDROID_SERIAL"):
        return flag or os.environ["ANDROID_SERIAL"]
    tool = adb()
    devices = online(tool) if tool else []
    if len(devices) != 1:
        raise SystemExit(f"{len(devices)} devices online ({', '.join(devices) or 'none'}): "
                         "start one, or pick one with --device SERIAL")
    return devices[0]


@contextmanager
def emulator_lock(serial: str, wait_s: int = 120):
    """One lock per device (a mkdir, so it is atomic), so two explores can run on two emulators. The holder writes
    its pid inside; a lock whose holder died (SIGKILL, a closed terminal) is broken instead of waited on."""
    lock = LOCK_DIR / f"simula-emu-{serial}.lock"
    deadline = time.monotonic() + wait_s
    while True:
        try:
            lock.mkdir()
            (lock / "pid").write_text(str(os.getpid()))
            break
        except FileExistsError:
            if holder_dead(lock):
                reclaim(lock)
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} held by another process for {wait_s}s")
            time.sleep(5)
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def holder_dead(lock: Path) -> bool:
    """The pid inside is gone. A lock with no pid file is dead once it is a minute old (the holder writes it
    right after the mkdir; older checkouts' locks have none)."""
    try:
        pid = int((lock / "pid").read_text())
    except (FileNotFoundError, ValueError):
        try:
            return time.time() - lock.stat().st_mtime > 60
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def reclaim(lock: Path) -> None:
    """Removes a dead holder's lock. The check and the removal happen under a short flock, so of two waiters that
    both saw the dead holder, the second finds the first one's fresh lock alive and leaves it. The guard is opened
    read-only and never through a symlink, so a planted link in /tmp can't make it truncate anything."""
    guard = os.open(lock.with_name(f"{lock.name}.guard"), os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(guard, fcntl.LOCK_EX)
        if holder_dead(lock):
            shutil.rmtree(lock, ignore_errors=True)
    finally:
        os.close(guard)
