"""A fake phone built from fixture captures, for offline explorer tests. Each screen is a real tree + PNG; a tap
moves to another screen by the tapped element's words (or its center, for controls without words)."""

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from simula import decide, llm, runlog
from simula.contracts import Manifest, Provenance
from simula.device.mcp import McpTimeout
from simula.device.observe import dhash, hamming, words
from simula.stages import Ctx
from simula.stages import explore as stage

FIXTURES = Path(__file__).parent / "fixtures" / "trees"
PREFIX = "Found these elements on screen: "
PACKAGE = "com.janitor.ai"
PREFER = ("JJK", "Kang Jun-Seo", "Upgrade to", "icon")
RESTRICTIVE = re.compile(r"\b(only|safe|sfw)\b", re.IGNORECASE)


@dataclass
class Screen:
    elements: list[dict]
    image: Image.Image
    package: str


def capture(app: str, name: str, package: str | None = None) -> Screen:
    reply = json.loads((FIXTURES / app / f"{name}.elements.json").read_text())
    elements = json.loads(reply["content"][0]["text"][len(PREFIX):])
    png = FIXTURES / app / f"{name}.png"
    image = Image.open(png).convert("RGB") if png.exists() else Image.new("RGB", (1080, 2400), (240, 240, 240))
    fg = json.loads((FIXTURES / app / f"{name}.foreground.json").read_text())["content"][0]["text"]
    return Screen(elements, image, package or fg.rsplit("(", 1)[1].rstrip(")"))


def blank(package: str) -> Screen:
    return Screen([], Image.new("RGB", (1080, 2400), (20, 20, 20)), package)


def element_key(e: dict) -> str:
    c = e["coordinates"]
    ident = (e.get("identifier") or "").rsplit("/", 1)[-1]
    return words(e) or ident or f"{c['x'] + c['width'] // 2},{c['y'] + c['height'] // 2}"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class FakePhone:
    screens: dict[str, Screen]
    start: str
    taps: dict[tuple[str, str], str]
    clock: Clock
    backs: dict[str, str] = field(default_factory=dict)
    swipes: dict[str, str] = field(default_factory=dict)
    replies: dict[str, list[str]] = field(default_factory=dict)
    dirty: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    splash: int = 0
    hung_lists: int = 0
    device: str = "fake-1"
    after_sends: dict[int, str] = field(default_factory=dict)
    splash_screen: str | None = None
    generating: float = 0.0
    busy_label: str = "Cancel"
    chat_top: int = 401
    in_task: set[str] = field(default_factory=set)  # other packages' screens the app's own task holds (a system dialog)

    def __post_init__(self):
        self.screen, self.history, self.log, self.typed = self.start, [], [], []
        self.list_seconds, self.shots, self.reply_polls, self.splash_left = [], {}, 0, 0
        self.chats: dict[str, list[tuple[str, str]]] = {}
        self.draft, self.sent, self.busy_until = "", 0, 0.0
        self.alive = False  # the app's task: a launch brings a live one back as it was
        self.screens.setdefault("launcher", blank("com.android.launcher"))

    def tick(self, seconds: float = 0.3) -> None:
        self.clock.t += seconds

    def current_elements(self) -> list[dict]:
        if self.splash_left:
            self.splash_left -= 1
            return list(self.screens[self.splash_screen].elements) if self.splash_screen else []
        elements = list(self.screens[self.screen].elements)
        if self.screen not in self.replies:
            return elements
        box = next(e for e in elements if e["type"].endswith("EditText"))
        if self.draft:
            elements = [{**e, "text": self.draft} if e is box else e for e in elements]
        if self.clock.t < self.busy_until:
            busy = {"text": self.busy_label, "label": "", "enabled": False}
            elements = [{**e, **busy} if "send" in words(e).lower() else e for e in elements]
        self.reply_polls += 1
        return elements + [{"ref": f"@m{n}", "type": "android.widget.TextView", "text": text,
                            "coordinates": {"x": x, "y": y, "width": w, "height": h}}
                           for n, (text, x, y, w, h) in enumerate(self.bubbles(box["coordinates"]["y"]))]

    def bubbles(self, composer_y: int) -> list[tuple[str, int, int, int, int]]:
        """A real chat: each send adds the user's bubble and the reply above the composer, bottom-anchored, so older
        bubbles move up into the header band; the reply streams in; the history survives leaving the chat."""
        out, y = [], composer_y - 40
        history = self.chats.get(self.screen, [])
        for n in reversed(range(len(history))):
            mine, reply = history[n]
            if n == len(history) - 1:
                reply = reply[: 40 * self.reply_polls]
            for text, x, w, h in ((f"5:{20 + n:02d} PM", 900, 120, 40), (reply, 42, 900, 150), (mine, 400, 640, 100)):
                y -= h + 16
                if y < self.chat_top:
                    return out
                out.append((text, x, y, w, h))
        return out

    def elements(self):
        self.tick()
        if self.hung_lists:
            self.hung_lists -= 1
            self.tick(40.0)
            raise McpTimeout("mobile_list_elements_on_screen took over 20s")
        self.list_seconds.append(0.3)
        elements = self.current_elements()
        return {"content": [{"type": "text", "text": PREFIX + json.dumps(elements)}], "isError": False}, elements

    def image(self) -> Image.Image:
        """The screen's capture, with a stop square where send is while a reply is written, and the chat's bubbles."""
        image = self.screens[self.screen].image.copy()
        seen = self.shots.get(self.screen, 0)
        if self.screen in self.dirty and seen > 1:
            ImageDraw.Draw(image).rectangle(self.dirty[self.screen], fill=(255, 255, 255))
        if self.screen in self.replies and self.clock.t < self.busy_until:
            for e in self.screens[self.screen].elements:
                if "send" in words(e).lower():
                    c = e["coordinates"]
                    ImageDraw.Draw(image).rectangle((c["x"], c["y"], c["x"] + c["width"], c["y"] + c["height"]),
                                                    fill=(200, 30, 30))
        if self.chats.get(self.screen):
            draw = ImageDraw.Draw(image)
            box = next(e for e in self.screens[self.screen].elements if e["type"].endswith("EditText"))
            draw.rectangle((0, self.chat_top, 1080, box["coordinates"]["y"] - 24), fill=(245, 245, 245))
            for text, x, y, w, h in self.bubbles(box["coordinates"]["y"]):
                draw.rectangle((x, y, x + w, y + h), fill=(30, 90, 200) if x == 400 else (90, 90, 90))
        return image

    def small_hash(self) -> int:
        self.tick()
        return dhash(self.image())

    def screenshot(self, path: Path, size=None) -> Path:
        self.tick()
        self.shots[self.screen] = self.shots.get(self.screen, 0) + 1
        self.image().save(path)
        self.log.append(("shot", self.screen))
        return path

    def foreground(self) -> str:
        self.tick(0.1)
        return self.screens[self.screen].package

    def screen_size(self) -> tuple[int, int]:
        return 1080, 2400

    def go(self, target: str | None) -> None:
        if target and target != self.screen:
            self.history.append(self.screen)
            self.screen = target

    def tap(self, x: int, y: int) -> None:
        """The smallest element under the point gets the tap; one with no mapping passes it to the next one out,
        the way a parent view handles a click its child ignores."""
        self.tick()
        holding = sorted((e for e in self.current_elements() if e["coordinates"]["x"] <= x < e["coordinates"]["x"]
                          + e["coordinates"]["width"] and e["coordinates"]["y"] <= y < e["coordinates"]["y"]
                          + e["coordinates"]["height"]),
                         key=lambda e: e["coordinates"]["width"] * e["coordinates"]["height"])
        keys = [element_key(e) for e in holding] or ["nothing"]
        self.log.append(("tap", self.screen, keys[0]))
        if self.screen in self.replies and self.draft and self.clock.t >= self.busy_until and any(
                "send" in k.lower() for k in keys):
            self.send()
        self.go(next((self.taps[(self.screen, k)] for k in keys if (self.screen, k) in self.taps), None))

    def send(self) -> None:
        history = self.chats.setdefault(self.screen, [])
        replies = self.replies[self.screen]
        history.append((self.draft, replies[len(history) % len(replies)]))
        self.draft, self.reply_polls, self.busy_until = "", 0, self.clock.t + self.generating
        self.sent += 1
        self.go(self.after_sends.get(self.sent))

    def ours(self, screen: str) -> bool:
        return self.screens[screen].package == self.screens[self.start].package or screen in self.in_task

    def back(self) -> None:
        """BACK out of the app ends its task, as BACK from Perplexity's last screen did on the emulator."""
        self.tick()
        self.log.append(("back", self.screen))
        was_ours = self.ours(self.screen)
        if self.screen in self.backs:
            self.screen = self.backs[self.screen]
        else:
            self.screen = self.history.pop() if self.history else "launcher"
        self.alive = self.alive and not (was_ours and not self.ours(self.screen))

    def swipe(self, direction: str) -> None:
        self.tick()
        self.log.append(("swipe", self.screen, direction))
        self.go(self.swipes.get(self.screen))

    def type_text(self, text: str) -> None:
        self.tick()
        self.log.append(("type", self.screen, text))
        self.typed.append(text)
        self.draft += text

    def launch(self) -> None:
        """A live task comes to the front as it was, over whatever another app opened on it; else a fresh start."""
        self.tick(2.0)
        self.log.append(("launch",))
        if not self.alive:
            self.screen, self.history, self.splash_left, self.alive = self.start, [], self.splash, True
        while not self.ours(self.screen):
            self.screen = self.history.pop()

    def terminate(self) -> None:
        self.tick()
        self.alive = False


# ---------- a run with fake Jev and a fake Sonnet ----------

def fake_jev(state, instructions, labels, backend, core_pick="send messages"):
    if "state of this switch" in instructions:  # the restrictive state: on for hide or block, off for show or allow
        pick = 1 if re.search(r"\b(show|allow)\b", labels[0], re.IGNORECASE) else 0
    elif "content or safety filter" in instructions:
        pick = next((i for i, label in enumerate(labels) if RESTRICTIVE.search(label)), len(labels) - 1)
    elif "comes to this app" in instructions:
        pick = next((i for i, label in enumerate(labels) if core_pick in label), 0)
    else:
        pick = next((i for i, label in enumerate(labels) for word in PREFER if word in label), 0)
    probabilities = {f"o{i + 1:02d}": 0.7 if i == pick else 0.3 / max(len(labels) - 1, 1) for i in range(len(labels))}
    return decide.ChoiceResult(option_id=f"o{pick + 1:02d}", confidence=0.7, probabilities=probabilities,
                               model="jev-fake", tokens_in=120, tokens_out=0, usd=0.00001, seconds=0.1)


WALK_PICK = re.compile(r"^(new chat|start chat.*|chat with .*)$", re.IGNORECASE)


def header_hash(png: bytes) -> int:
    image = Image.open(io.BytesIO(png))
    return dhash(image.crop((0, 0, image.width, image.height // 10)))


def fake_sonnet(model, system, messages, effort, schema, max_tokens, total_timeout=None):
    """Sonnet on a fake phone: an arrival is the same place when the two screens share their header; the walk's
    start control is a chat-starting label; a screen that keeps changing is still working while a stop square shows,
    and otherwise only decoration moves."""
    parts = messages[0]["content"]
    text = parts[-1]["text"]
    if schema.__name__ == "IconPass":
        wanted = text.split("Name these boxes: ", 1)[1].split(".", 1)[0]
        ids = [int(n) for n in wanted.split(", ") if n.isdigit()]
        body = {"names": [{"box_id": n, "name": f"icon {n}"} for n in ids], "extra_points": []}
    elif schema.__name__ == "Arrival":
        same = hamming(header_hash(parts[0]["png"]), header_hash(parts[1]["png"])) <= 6
        body = {"identifying_text": "", "verdict": "same" if same else "elsewhere", "confidence": 0.9, "action": None,
                "element_id": None, "direction": None, "side_effect": False, "reason": "headers compared"}
    elif schema.__name__ == "WalkPick":
        listed = dict(line.split(": ", 1) for line in text.splitlines() if re.match(r"o\d\d: ", line))
        pick = next((i for i, label in listed.items() if WALK_PICK.match(label.rsplit(" (", 1)[0])), None)
        body = {"on_screen": pick is not None, "element_id": pick, "confidence": 0.9, "reason": "a start control"}
    elif schema.__name__ == "Progress":
        now = Image.open(io.BytesIO(parts[1]["png"])).convert("RGB")
        busy = (200, 30, 30) in {color for _, color in now.getcolors(1 << 16) or []}
        body = {"verdict": "progressing" if busy else "finished", "reason": "the stop square" if busy else "decoration"}
    else:
        body = {"action": "done", "element_id": None, "text": None, "direction": None, "reason": "nothing left"}
    return llm.Reply(text=json.dumps(body), model=model, tokens_in=1500, tokens_out=60)


def new_run(tmp_path, **ctx_overrides) -> Ctx:
    run_dir = tmp_path / "runs" / "janitorai" / "r1"
    run_dir.mkdir(parents=True)
    runlog.write_manifest(run_dir, Manifest(
        run_id="r1", app="janitorai", created_at="now", git_sha="x", git_dirty=False, profile="real",
        budget="transfer", allow_account_create=False, roles={}, prompt_hashes={}, app_package=PACKAGE,
        app_version=None, mobile_mcp_version=None, playwright_version=None, caps_usd={}, no_send=False,
        provenance=Provenance(source="explorer_run"), stages_done=[], usd_total=0.0))
    ctx = dict(app={"name": "janitorai", "package": PACKAGE}, run_dir=run_dir, profile="real", no_cache=False,
               replay=False, usd_cap=None, allow_fixtures=False, budget="transfer", no_send=False)
    return Ctx(**{**ctx, **ctx_overrides})


def explorer(tmp_path, monkeypatch, phone_factory, cache_dir=None, **ctx_overrides):
    """An explorer on a fake phone with fake Jev and Sonnet, not run yet."""
    monkeypatch.delenv("SIMULA_JEV_BACKEND", raising=False)
    monkeypatch.setattr(decide, "ask_choice", fake_jev)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", fake_sonnet)
    monkeypatch.setattr(stage, "adb_value", lambda *a: None)
    ctx = new_run(tmp_path, **ctx_overrides)
    clock = Clock()
    phone = phone_factory(clock)
    ex = stage.Explorer(ctx, phone, ctx.run_dir / "explore", clock=clock, sleep=clock.sleep)
    ex.cache_dir = cache_dir or tmp_path / "cache"
    return ex, phone


def explore(tmp_path, monkeypatch, phone_factory, cache_dir=None, **ctx_overrides):
    """Runs the whole explore stage on a fake phone; returns the explorer and the phone."""
    ex, phone = explorer(tmp_path, monkeypatch, phone_factory, cache_dir, **ctx_overrides)
    stage.explore_app(ex)
    return ex, phone
