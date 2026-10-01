"""mobile-mcp over stdio: the explorer's only channel to the device. Every call has a client-side timeout; a
hung server is killed with its whole process tree and a fresh session takes its place."""

import asyncio
import concurrent.futures
import json
import os
import re
import threading
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from simula.config import ROOT
from simula.device.observe import dhash

SERVER_JS = ROOT / "node_modules" / "@mobilenext" / "mobile-mcp" / "lib" / "index.js"
ELEMENTS_PREFIX = "Found these elements on screen: "
ACTION_TIMEOUT_S = 10.0
LIST_TIMEOUT_S = 20.0
SCREENSHOT_TIMEOUT_S = 30.0
LAUNCH_TIMEOUT_S = 30.0
START_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 15.0
LIST_ATTEMPTS = 3
LIST_RETRY_PAUSE_S = 1.5
DEVICE_ATTEMPTS = 6
DEVICE_RETRY_PAUSE_S = 5.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEVICE_LOST = re.compile(r'Device ".*" not found')


class McpTimeout(Exception):
    pass


class McpReplyError(Exception):
    pass


# ---------- reply parsing ----------

def reply_text(reply: dict) -> str:
    return "".join(c.get("text", "") for c in reply.get("content") or [] if c.get("type") == "text")


def parse_elements(reply: dict) -> list[dict]:
    text = reply_text(reply)
    if reply.get("isError") or not text.startswith(ELEMENTS_PREFIX):
        raise McpReplyError(f"not an element list: {text[:120]!r}")
    elements = json.loads(text[len(ELEMENTS_PREFIX):])
    if not isinstance(elements, list) or not all(isinstance(e, dict) and "coordinates" in e for e in elements):
        raise McpReplyError("element list without coordinates")
    return elements


def parse_foreground(reply: dict) -> str:
    """'Foreground app: Example (com.example.app)' -> 'com.example.app'."""
    match = re.search(r"\(([\w.]+)\)\s*$", reply_text(reply))
    if reply.get("isError") or not match:
        raise McpReplyError(f"no foreground package in {reply_text(reply)[:120]!r}")
    return match.group(1)


def parse_screen_size(reply: dict) -> tuple[int, int]:
    match = re.search(r"(\d+)x(\d+)", reply_text(reply))
    if reply.get("isError") or not match:
        raise McpReplyError(f"no screen size in {reply_text(reply)[:120]!r}")
    return int(match.group(1)), int(match.group(2))


def check_png(path: Path, size: tuple[int, int] | None = None) -> None:
    """mobile-mcp answers a refused save with text, not an error, so the file itself is the proof."""
    if not path.exists() or path.read_bytes()[:8] != PNG_MAGIC:
        raise McpReplyError(f"screenshot not written to {path}")
    if size and Image.open(path).size not in (size, size[::-1]):  # the screen may have turned sideways
        raise McpReplyError(f"screenshot is {Image.open(path).size}, expected {size}")


# ---------- the server ----------

def server_env() -> dict[str, str]:
    home = os.environ.get("ANDROID_HOME") or str(Path.home() / "Library" / "Android" / "sdk")
    return {**os.environ, "ANDROID_HOME": home, "MOBILEMCP_DISABLE_TELEMETRY": "1"}


class Server:
    """One mobile-mcp process. A background event loop owns the session so the explorer can stay synchronous."""

    def __init__(self, cwd: Path, command: list[str] | None = None):
        command = command or ["node", str(SERVER_JS)]
        self.params = StdioServerParameters(command=command[0], args=command[1:], cwd=str(cwd), env=server_env())
        self.errlog = open(os.devnull, "w")  # mobile-mcp logs every raw reply there, before redaction
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.respawns = 0
        self._start()

    async def _serve(self, ready: concurrent.futures.Future) -> None:
        try:
            async with stdio_client(self.params, errlog=self.errlog) as (read, write), \
                    ClientSession(read, write) as session:
                await session.initialize()
                self.session, self.stopping = session, asyncio.Event()
                ready.set_result(None)
                await self.stopping.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            raise

    def _start(self) -> None:
        ready = concurrent.futures.Future()
        self.task = asyncio.run_coroutine_threadsafe(self._serve(ready), self.loop)
        ready.result(START_TIMEOUT_S)

    def _stop(self) -> None:
        self.loop.call_soon_threadsafe(self.stopping.set)
        try:
            self.task.result(STOP_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - a dead server may end any way; the tree is killed either way
            self.task.cancel()

    def respawn(self) -> None:
        self._stop()
        self.respawns += 1
        self._start()

    def close(self) -> None:
        self._stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.errlog.close()

    def call(self, tool: str, timeout: float, **args) -> dict:
        future = asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(self.session.call_tool(tool, args), timeout), self.loop)
        try:
            return future.result(timeout + 5).model_dump(mode="json")
        except TimeoutError as e:
            future.cancel()
            raise McpTimeout(f"{tool} took over {timeout:.0f}s") from e


# ---------- the phone ----------

class Phone:
    """The app under test on the one Android device mobile-mcp lists. Reads retry once after a respawn;
    actions never repeat (the first one may have landed), so their timeout goes to the caller."""

    def __init__(self, server: Server, package: str, scratch: Path, serial: str | None = None,
                 avd: str | None = None):
        self.server, self.package, self.scratch = server, package, scratch
        self.list_seconds: list[float] = []
        self.device = self.find_device(serial, avd)

    def find_device(self, serial: str | None, avd: str | None) -> str:
        """mobile-mcp's id for this device: the one whose id is the adb serial, else the one named for the
        emulator's AVD; the first Android device when neither is given. Two emulators of one AVD look alike to
        mobile-mcp, so more than one match is an error. The list comes back empty now and then while adb is busy."""
        for attempt in range(1, DEVICE_ATTEMPTS + 1):
            android = self.android()
            matches = ([d for d in android if d.get("id") == serial]
                       or [d for d in android if avd and avd in (d.get("id"), d.get("name"))]
                       if serial or avd else android[:1])
            if len(matches) > 1:
                raise SystemExit(f"{len(matches)} mobile-mcp devices match {serial} / {avd}: two emulators of one "
                                 "AVD can't be told apart; run one per AVD")
            if matches:
                return matches[0]["id"]
            if attempt < DEVICE_ATTEMPTS:
                time.sleep(DEVICE_RETRY_PAUSE_S)
        raise SystemExit(f"no Android device {serial or avd or ''} online: start the emulator first")

    def android(self) -> list[dict]:
        """The Android devices mobile-mcp lists; none while mobilecli can't list them (an error reply, not JSON)."""
        try:
            devices = json.loads(reply_text(self.server.call("mobile_list_available_devices", START_TIMEOUT_S)))
        except ValueError:
            return []
        return [d for d in devices.get("devices", []) if d.get("platform") == "android"]

    def call(self, tool: str, timeout: float = ACTION_TIMEOUT_S, retry: bool = False, **args) -> dict:
        """One tool call. A 'Device not found' answer (mobilecli loses the device now and then while the emulator
        is busy; mobile-mcp sends it as text, without isError) means nothing ran, so it is asked again, even for an
        action, and still lost after DEVICE_ATTEMPTS it is an error."""
        for attempt in range(1, DEVICE_ATTEMPTS + 1):
            reply = self.call_once(tool, timeout, retry, **args)
            if not DEVICE_LOST.match(reply_text(reply)):
                return reply
            if attempt < DEVICE_ATTEMPTS:
                time.sleep(DEVICE_RETRY_PAUSE_S)
        raise McpReplyError(f"{tool}: {reply_text(reply)[:120]!r}")

    def call_once(self, tool: str, timeout: float, retry: bool, **args) -> dict:
        try:
            return self.server.call(tool, timeout, device=self.device, **args)
        except McpTimeout:
            self.respawn()
            if not retry:
                raise
            return self.server.call(tool, timeout, device=self.device, **args)

    def respawn(self) -> None:
        """A fresh server, once it lists this device again: its mobilecli can miss it for a while (still at 19 s
        after a respawn, measured 2026-10-01). Missing after START_TIMEOUT_S, the next call finds it lost."""
        self.server.respawn()
        deadline = time.monotonic() + START_TIMEOUT_S
        while all(d.get("id") != self.device for d in self.android()) and time.monotonic() < deadline:
            time.sleep(DEVICE_RETRY_PAUSE_S)

    def elements(self) -> tuple[dict, list[dict]]:
        """The element list. uiautomator's dump fails now and then mid-animation ("no XML content"); an error
        reply is read again twice before it counts."""
        for attempt in range(1, LIST_ATTEMPTS + 1):
            started = time.monotonic()
            reply = self.call("mobile_list_elements_on_screen", LIST_TIMEOUT_S, retry=True, format="json")
            self.list_seconds.append(time.monotonic() - started)
            try:
                return reply, parse_elements(reply)
            except McpReplyError:
                if attempt == LIST_ATTEMPTS:
                    raise
                time.sleep(LIST_RETRY_PAUSE_S)

    def screenshot(self, path: Path, size: tuple[int, int] | None = None) -> Path:
        path.unlink(missing_ok=True)
        self.call("mobile_save_screenshot", SCREENSHOT_TIMEOUT_S, retry=True, saveTo=str(path.resolve()))
        check_png(path, size)
        return path

    def small_hash(self) -> int:
        path = self.scratch / "small.png"
        path.unlink(missing_ok=True)
        self.call("mobile_save_screenshot", SCREENSHOT_TIMEOUT_S, retry=True, saveTo=str(path.resolve()), maxSize=96)
        check_png(path)
        return dhash(Image.open(path))

    def foreground(self) -> str:
        return parse_foreground(self.call("mobile_get_foreground_app", retry=True))

    def screen_size(self) -> tuple[int, int]:
        return parse_screen_size(self.call("mobile_get_screen_size", retry=True))

    def act(self, tool: str, timeout: float = ACTION_TIMEOUT_S, retry: bool = False, **args) -> None:
        """A device action. An error reply means it didn't happen, so it never counts as done."""
        reply = self.call(tool, timeout, retry, **args)
        if reply.get("isError"):
            raise McpReplyError(f"{tool} failed: {reply_text(reply)[:120]!r}")

    def tap(self, x: int, y: int) -> None:
        self.act("mobile_click_on_screen_at_coordinates", x=x, y=y)

    def back(self) -> None:
        self.act("mobile_press_button", button="BACK")

    def swipe(self, direction: str) -> None:
        self.act("mobile_swipe_on_screen", direction=direction)

    def type_text(self, text: str) -> None:
        """A failed type's reply is withheld: mobile-mcp's error repeats the adb command, typed text and all."""
        try:
            self.act("mobile_type_keys", text=text, submit=False)
        except McpReplyError:
            raise McpReplyError("mobile_type_keys failed (its reply is withheld: it repeats the typed text)") from None

    def launch(self) -> None:
        self.act("mobile_launch_app", LAUNCH_TIMEOUT_S, retry=True, packageName=self.package)

    def terminate(self) -> None:
        self.act("mobile_terminate_app", LAUNCH_TIMEOUT_S, retry=True, packageName=self.package)
