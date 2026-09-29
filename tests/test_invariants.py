"""The four invariants that replaced the explorer's case rules, on small captures cut from the recorded runs: arrival by
what a screen shows (with the element lists' overlap as the second signal), settle before leaving, tap only what the
screen shows, and the walk done by the model. Also: no app's name or content anywhere in the explorer's code or
prompts."""

import json
from pathlib import Path

from PIL import Image, ImageDraw

from simula import runlog
from simula.contracts import Arrival, Device, Rect
from simula.device import observe as ob
from simula.device.mcp import parse_elements
from simula.stages import explore as stage
from tests.fake_device import PACKAGE, Screen
from tests.fake_device import explore as run_explorer
from tests.fake_device import explorer as new_explorer
from tests.test_explore_offline import chatty, janitor_like

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures" / "invariants"
DEVICE = Device()
TABS = [Rect(x=x, y=170, w=167, h=167) for x in (72, 264, 457, 649, 841)]  # the tab bar, in the 400 px bottom band


def elements(name: str) -> list[dict]:
    return parse_elements(json.loads((FIX / f"{name}.elements.json").read_text()))


def text(ref: str, words: str, y: int) -> dict:
    return {"ref": ref, "type": "android.widget.TextView", "text": words,
            "coordinates": {"x": 42, "y": y, "width": 600, "height": 60}}


# ---------- invariant 1: arrival by what the screen shows ----------

def test_a_re_sorted_list_is_the_same_place_by_structure_and_a_chat_is_not():
    before, resorted = elements("mychats-before"), elements("mychats-resorted")
    assert ob.structure(before, resorted, DEVICE) >= ob.STRUCTURE_SAME
    assert ob.structure(before, elements("chat"), DEVICE) < ob.STRUCTURE_SAME


def test_counts_times_and_dynamic_regions_never_make_another_screen():
    then = [text("@a", "My list", 300), text("@b", "4 items", 600), text("@c", "10:29", 900)]
    now = [text("@a", "My list", 300), text("@b", "5 items", 600), text("@c", "11:02", 900)]
    assert ob.structure(then, now, DEVICE) == 1.0
    banner = [text("@d", "Today's pick: one", 1500)]
    other = [text("@d", "Today's pick: two", 1500)]
    region = Rect(x=0, y=1400, w=1080, h=300)
    assert ob.structure(then + banner, then + other, DEVICE) < 1.0
    assert ob.structure(then + banner, then + other, DEVICE, [region]) == 1.0


def test_a_hop_to_a_changed_screen_arrives_by_what_it_shows_without_a_new_state(tmp_path, monkeypatch):
    ex, phone = run_explorer(tmp_path, monkeypatch, janitor_like)
    tab = next(t for t in ex.tabs if t.rect.x <= 540 < t.rect.x + t.rect.w)
    chats = ex.by_id[ex.tab_to[tab.key]]
    old = phone.screens["chats"]
    image = old.image.copy()
    for x in range(0, 1080, 120):  # the list below the header re-sorted: most of the content pixels changed
        ImageDraw.Draw(image).rectangle((x, 700, x + 60, 2100), fill=(230, 230, 230))
    chip = {"ref": "@chip", "type": "android.widget.Button", "text": "Unread only",
            "coordinates": {"x": 42, "y": 420, "width": 333, "height": 111}}
    phone.screens["chats"] = Screen([*old.elements[::-1], chip], image, old.package)
    phone.screen, phone.history = "limited", []
    ex.scratch.mkdir()
    ex.current = ex.record(ex.observe(), None, None, None)
    states, hops = len(ex.states), ex.counts["hops arrived by the model"]
    assert ex.goto(chats) and ex.current is chats and len(ex.states) == states
    assert not ob.same_state(ex.obs.fp, chats.fp) and ex.counts["hops arrived by the model"] == hops + 1
    line = [t for t in runlog.read_trace(ex.run_dir / "trace.jsonl") if t.step == f"arrival.{chats.sid}"][-1]
    assert line.decider == "model" and line.note.startswith(f"arrived at {chats.sid}")
    assert f"explore/states/{chats.sid}.png" in line.note and "explore/arrival/" in line.note


def arrival(text: str, verdict: str = "same", element_id: str | None = None) -> Arrival:
    return Arrival(identifying_text=text, verdict=verdict, confidence=0.9, action="tap" if element_id else None,
                   element_id=element_id, direction=None, side_effect=False, reason="test")


def on_the_filtered_feed(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screen = "limited"
    ex.current = ex.record(ex.observe(), None, None, None)
    ex.filter_taps = [next(c for c in ex.obs.cands if c.label == "Limited Only")]
    return ex


def test_a_same_verdict_counts_only_when_its_text_is_on_the_screen(tmp_path, monkeypatch):
    ex = on_the_filtered_feed(tmp_path, monkeypatch)
    title = stage.title_of(ex.current.elements, DEVICE)
    for text, arrived in (("A title from another screen", False), (title.upper(), True), ("", True)):
        monkeypatch.setattr(ex, "ask", lambda *a, text=text: arrival(text))
        assert ex.judge(ex.current).arrived is arrived
    assert ex.counts["model same without its text on screen"] == 1


def test_a_model_named_tap_never_touches_the_content_filter_row(tmp_path, monkeypatch):
    ex = on_the_filtered_feed(tmp_path, monkeypatch)
    row = {c.label for c in ex.obs.cands if c.key in ex.filter_row(ex.obs.cands)}
    asked = []
    monkeypatch.setattr(ex, "ask",
                        lambda prompt, step, text, *a: asked.append(text) or arrival("", "one_action", "o01"))
    landing = ex.judge(ex.current)
    assert {"All", "Limited Only"} <= row and not any(f": {label} (" in asked[0] for label in row)
    assert landing.move and landing.move.cand.label not in row


# ---------- invariant 3: tap only what the screen shows ----------

def band(name: str) -> Image.Image:
    return Image.open(FIX / f"tabs-{name}.png").convert("RGB")


def test_a_tab_under_a_sheet_is_covered_and_a_highlighted_tab_is_still_the_tab():
    home, search, sheet = band("home"), band("search"), band("sheet")
    for tab in TABS:
        assert ob.looks_same(home, tab, search, tab, DEVICE)
    assert not ob.looks_same(home, TABS[2], sheet, TABS[2], DEVICE)


def test_a_sheet_halfway_in_counts_as_covering_the_tap_point():
    home = band("home")
    half = home.copy()
    tab = TABS[2]
    ImageDraw.Draw(half).rectangle((tab.x, tab.y + tab.h // 3, tab.x + tab.w, tab.y + tab.h), fill=(40, 40, 44))
    assert not ob.looks_same(home, tab, half, tab, DEVICE)


# ---------- invariant 2: settle before leaving ----------

def test_the_chat_waits_for_the_reply_and_for_send_to_come_back(tmp_path, monkeypatch):
    def slow(clock):
        phone = chatty(clock)
        phone.generating = 40.0
        return phone
    ex, phone = run_explorer(tmp_path, monkeypatch, slow, budget="deep")
    assert phone.sent == ex.core_reps == 8 and not ex.core_hit
    assert ex.settles and all(how in ("settled", "finished") for _, how in ex.settles)
    assert all(seconds >= 40 for seconds, how in ex.settles if how == "settled")
    assert not any(entry[:1] == ("tap",) and entry[2] == "Cancel" for entry in phone.log)


def test_decorative_motion_ends_the_wait_by_the_model_not_the_cap(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, chatty, budget="deep")
    ex.device = DEVICE
    ex.core = stage.CoreAction("chat", None, [], "send messages")
    frames = {"n": 0}

    def blinking(path, size=None):
        image = Image.new("RGB", (1080, 2400), (240, 240, 240))
        frames["n"] += 1
        if frames["n"] % 2:
            ImageDraw.Draw(image).rectangle((60, 2050, 64, 2120), fill=(0, 0, 0))
        image.save(path)
        return path
    phone.screenshot = blinking
    phone.screen = "chat"
    ex.watch(set(), "reply")
    (seconds, how), = ex.settles
    assert how == "finished" and stage.SETTLE_ASK_S <= seconds < stage.SETTLE_CAP_S
    notes = [line for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.step == "settle"]
    assert any(line.decider == "model" for line in notes)


# ---------- invariant 4: the walk done by the model ----------

def long_page(clock, pick_on_page: int):
    phone = janitor_like(clock)
    back = {"ref": "@back", "type": "android.widget.Button", "text": "", "identifier": "x:id/back",
            "coordinates": {"x": 42, "y": 168, "width": 84, "height": 84}}
    go = {"ref": "@go", "type": "android.view.ViewGroup", "text": "Chat with Avarus",
          "coordinates": {"x": 88, "y": 2198, "width": 904, "height": 102}}
    pages = [f"page{n}" for n in range(12)]
    for n, name in enumerate(pages):
        para = {"ref": "@p", "type": "android.widget.TextView", "text": f"Paragraph {n} of a long description",
                "coordinates": {"x": 42, "y": 700, "width": 996, "height": 120}}
        image = Image.new("RGB", (1080, 2400), (20 * n % 255, 20, 240 - 20 * n))
        if n == pick_on_page:
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((88, 2198, 992, 2300), radius=30, fill=(120, 80, 220))
            draw.text((380, 2230), "Chat with Avarus", fill=(255, 255, 255), font_size=40)
        phone.screens[name] = Screen([back, para] + ([go] if n == pick_on_page else []), image, PACKAGE)
    phone.swipes.update(zip(pages, pages[1:]))
    phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "page0"
    phone.taps[(f"page{pick_on_page}", "Chat with Avarus")] = "chat"
    return phone


def test_the_walk_scrolls_past_the_old_cap_until_the_model_sees_the_start_control(tmp_path, monkeypatch):
    ex, phone = run_explorer(tmp_path, monkeypatch, lambda clock: long_page(clock, 10))
    assert ex.core.kind == "chat" and phone.sent
    assert sum(entry[:2] == ("swipe", f"page{n}") for n in range(10) for entry in phone.log) >= 10
    walk = [line for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.step.startswith("walk.")]
    assert walk and all(line.decider == "model" for line in walk)


def test_a_start_control_the_screen_no_longer_shows_is_never_tapped(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, lambda clock: long_page(clock, 0))
    observe, ask, asked = ex.observe, ex.ask, []

    def asking(prompt, *args, **kwargs):
        asked.append(prompt)
        return ask(prompt, *args, **kwargs)

    def sheet_slides_in():
        obs = observe()
        if asked and asked[-1] == "walk":
            ImageDraw.Draw(obs.image).rectangle((0, 1900, 1080, 2400), fill=(10, 10, 10))
        return obs
    monkeypatch.setattr(ex, "ask", asking)
    monkeypatch.setattr(ex, "observe", sheet_slides_in)
    stage.explore_app(ex)
    assert ex.counts["walk picks the screen no longer shows"] >= 1
    assert ("tap", "page0", "Chat with Avarus") not in phone.log


# ---------- nothing app-specific ----------

APP_WORDS = ["janitor", "luzia", "aol", "ooc"]
CONTENT = ["kang jun-seo", "nanami", "avarus", "harriet", "yagami", "vac the", "former husband", "gojo", "teacher",
           "toki", "wavemaker", "medieval fantasy", "real life world", "adrian & eleanor", "darko", "aaron",
           "mercenary", "pencil", "su fang", "countdown", "usa today", "vmas"]


def fixture_titles() -> set[str]:
    """Feed-item titles in every app's captures: content, never something the explorer may know by name."""
    titles = set()
    for path in (ROOT / "tests" / "fixtures" / "trees").glob("*/*.elements.json"):
        cands = ob.controls(parse_elements(json.loads(path.read_text())), DEVICE)
        titles |= {c.tree_label.split(",")[0].split("\n")[0].strip().lower() for c in ob.feed_items(cands, DEVICE)
                   if len(c.tree_label.split()) >= 3}
    return titles


def test_the_explorer_code_and_prompts_name_no_app_and_no_content():
    files = [ROOT / "simula" / "stages" / "explore.py", ROOT / "simula" / "decide.py",
             *(ROOT / "simula" / "device").glob("*.py"), *(ROOT / "prompts" / "explore").glob("*")]
    banned = APP_WORDS + CONTENT + sorted(fixture_titles())
    assert len(banned) > len(APP_WORDS) + len(CONTENT)
    hits = [(f.name, word) for f in files for word in banned if word in f.read_text().lower()]
    assert hits == []


def test_the_exhibit_reports_the_invariants_counters(tmp_path, monkeypatch):
    ex, _ = run_explorer(tmp_path, monkeypatch, janitor_like)
    exhibit = (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    assert "## Invariants" in exhibit and "hops:" in exhibit and "settle waits:" in exhibit
    assert "replayed moves reached the recorded screen" in exhibit and "Fingerprint alone:" in exhibit
    assert any(line.step == "counters" for line in runlog.read_trace(ex.run_dir / "trace.jsonl"))
