"""The agent explorer offline: a fake phone made of fixture captures, a fake planner returning canned AgentTurns
through llm.call, and fake Jev. Every hard block, the filter check, the stops, the dial and the record."""

import dataclasses
import io
import json
import re

import pytest
from PIL import Image, ImageDraw

from simula import cli, config, decide, llm, runlog, scorecard
from simula.contracts import AdLine, AgentStep, AgentTurn, ExploreFile
from simula.device import guard
from simula.stages import explore as stage
from simula.stages import model as model_stage
from simula.contracts import Rect
from simula.device.mcp import McpTimeout
from simula.stages import explore_agent as stage_agent
from simula.stages.explore_agent import STALE_TURNS, AgentExplorer, Halt, ScreenMoved
from tests.fake_device import PACKAGE, Clock, FakePhone, Screen, capture, fake_jev, fake_sonnet, new_run
from tests.test_explore_offline import janitor_like

CHATS_TAB = (540, 2253)
DRAWER = (996, 209)
LINE = re.compile(r"^(o\d+): \S+ '(.*?)'.* \[(\d+),(\d+),(\d+),(\d+)\]", re.MULTILINE)


def oid(text: str, label: str | None = None, at: tuple[int, int] | None = None) -> str:
    """The id the planner was shown for an element, by its label or a point inside it."""
    for i, said, x, y, w, h in LINE.findall(text):
        x, y, w, h = map(int, (x, y, w, h))
        if (label and said.startswith(label)) or (at and x <= at[0] < x + w and y <= at[1] < y + h):
            return i
    raise AssertionError(f"no element {label or at} in:\n{text}")


def control(label: str, n: int, kind: str = "Button", **fields) -> dict:
    """An element of a drawn screen: one full-width row per n."""
    return {"ref": f"@{n}", "type": f"android.widget.{kind}", "text": label, "label": "",
            "identifier": f"app:id/control{n}", "enabled": True,
            "coordinates": {"x": 100, "y": 200 + 200 * n, "width": 800, "height": 100}, **fields}


def drawn(*elements: dict, package: str = PACKAGE) -> Screen:
    """A screen whose picture draws its elements, under a title row naming the first."""
    elements = [{**control(f"Title {elements[0]['text']}", 0, "TextView"), "coordinates": {
        "x": 100, "y": 150, "width": 800, "height": 100}}, *elements]
    image = Image.new("RGB", (1080, 2400), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    for e in elements:
        r = e["coordinates"]
        draw.rectangle((r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"]), fill=(50, 80, 100))
        draw.text((r["x"] + 10, r["y"] + 10), e["text"], fill="white")
    return Screen(elements, image, package)


def phone_of(screens: dict, taps: dict | None = None, **fields):
    """A phone factory over drawn screens, starting on "root"."""
    return lambda clock: FakePhone(dict(screens), "root", dict(taps or {}), clock, **fields)


def turn(*steps, **fields) -> dict:
    return {"screen": fields.pop("screen", "a screen"), "goal": fields.pop("goal", "cover the app"),
            "steps": list(steps), **fields}


def tap(element: str, expect: str = "the screen changes") -> dict:
    return {"action": "tap", "element": element, "expect": expect}


DONE = {"action": "done", "expect": "nothing"}


def scripted(*turns):
    """A planner script from one function per turn, each given the user text it was shown."""
    return lambda n, text: turns[n - 1](text) if n <= len(turns) else None


class Planner:
    """Canned turns: script(n, text) gives turn n's answer for the user text it was shown (an exception is raised);
    past the script's end, done."""

    def __init__(self, script):
        self.script, self.texts, self.images = script, [], []

    def __call__(self, model, system, messages, effort, schema, max_tokens, total_timeout=None):
        if schema.__name__ != "AgentTurn":
            return fake_sonnet(model, system, messages, effort, schema, max_tokens, total_timeout)
        parts = messages[0]["content"]
        self.texts.append(parts[-1]["text"])
        self.images.append([p["png"] for p in parts if p["type"] == "image"])
        body = self.script(len(self.texts), self.texts[-1])
        if isinstance(body, BaseException):
            raise body
        return llm.Reply(text=json.dumps(body or turn(DONE, done_reason="covered")), model=model, tokens_in=2000,
                         tokens_out=200)


def agent(tmp_path, monkeypatch, script, phone_factory=janitor_like, jev=fake_jev, **ctx_overrides):
    """An agent explorer on a fake phone, not run yet, and its planner."""
    monkeypatch.delenv("SIMULA_JEV_BACKEND", raising=False)
    monkeypatch.setattr(decide, "ask_choice", jev)
    planner = Planner(script)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", planner)
    monkeypatch.setattr(stage, "adb_value", lambda *a: None)
    ctx = new_run(tmp_path, **{"no_cache": True, **ctx_overrides})
    clock = Clock()
    phone = phone_factory(clock)
    ex = AgentExplorer(ctx, phone, ctx.run_dir / "explore", clock=clock, sleep=clock.sleep)
    ex.cache_dir = tmp_path / "cache"
    return ex, phone, planner


def run(tmp_path, monkeypatch, script, **kwargs):
    ex, phone, planner = agent(tmp_path, monkeypatch, script, **kwargs)
    stage.explore_app(ex)
    return ex, phone, planner


def taps(phone) -> list[tuple[str, str]]:
    return [(screen, key) for kind, *rest in phone.log if kind == "tap" for screen, key in [rest]]


def trace(ex) -> list:
    return runlog.read_trace(ex.run_dir / "trace.jsonl")


def loads_cleanly(ex) -> None:
    device = ExploreFile.model_validate_json((ex.out / "explore.json").read_text()).device
    states, _, _ = model_stage.load_states(ex.out, device)
    assert {s.id for s in states} == {s.sid for s in ex.states}
    assert model_stage.read_actions(ex.out)


def test_a_short_run_records_states_and_actions_the_model_stage_loads(tmp_path, monkeypatch):
    script = scripted(lambda text: turn(tap(oid(text, "Limited Only"), "the list narrows")),
                      lambda text: turn(tap(oid(text, at=CHATS_TAB), "the chat list opens"), filter_set=True),
                      lambda text: turn(DONE, done_reason="covered"))
    ex, phone, planner = run(tmp_path, monkeypatch, script)
    assert len(planner.texts) == 3 and ex.stop_reason == "the planner said done: covered"
    assert ("root", "Limited Only") in taps(phone) and phone.screen != "root"
    assert [t.label for t in ex.filter_taps] == ["Limited Only"] and ex.filtered
    assert ex.filter_checks and all(ok for _, ok, _ in ex.filter_checks)  # the replay check's launches check it too
    assert "verified (check 1)" in planner.texts[2]
    loads_cleanly(ex)
    saved = ExploreFile.model_validate_json((ex.out / "explore.json").read_text())
    assert [c.label for c in saved.filter_controls] == ["Limited Only"]


def test_a_relaunch_puts_the_filter_back_and_checks_it_again(tmp_path, monkeypatch):
    script = scripted(lambda text: turn(tap(oid(text, "Limited Only"), "the list narrows")),
                      lambda text: turn({"action": "launch", "expect": "the launch screen"}, filter_set=True))
    ex, phone, planner = run(tmp_path, monkeypatch, script)
    assert ex.relaunches == 1 and [ok for _, ok, _ in ex.filter_checks][:2] == [True, True]
    assert ex.filter_checks[1][0] == 2 and "verified (check 2)" in planner.texts[2]


def test_on_the_google_account_chooser_only_an_account_row_or_a_flow_button_is_tapped(tmp_path, monkeypatch):
    """Review focus 1 and the red team's HIGH 2: the chooser is another package and is acted on, not left; only the
    account row (its email redacted) or a plain flow button may be tapped there, never account management."""
    chooser = drawn(control("someone@example.com", 1), control("Manage your Google Account", 2),
                    control("Add another account", 3), package=guard.ACCOUNT_CHOOSER)
    phone_factory = phone_of({"root": drawn(control("Continue with Google", 1)), "chooser": chooser,
                              "home": drawn(control("Signed in home", 1))},
                             {("root", "Continue with Google"): "chooser", ("chooser", "someone@example.com"): "home",
                              ("chooser", "Manage your Google Account"): "root"})
    script = scripted(lambda text: turn(tap(oid(text, "Continue with Google"), "the account chooser")),
                      lambda text: turn(tap(oid(text, "Manage your Google Account"), "account settings")),
                      lambda text: turn(tap(oid(text, "[redacted]"), "signed in")))
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("chooser", "Manage your Google Account") not in taps(phone)
    assert ("chooser", "someone@example.com") in taps(phone)
    assert planner.texts[3].startswith(f"App in front: {PACKAGE}")  # signed in, back in the app
    assert "was not run" in planner.texts[2] and ex.relaunches == 0
    picked = next(s for s in ex.states if s.fg == guard.ACCOUNT_CHOOSER)
    assert picked.kind == "screen"
    denied = [json.loads(line) for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    assert any(d["outcome"] == "denied" and "account chooser" in d["change_summary"] for d in denied)


def test_a_google_screen_that_isnt_the_chooser_is_left_with_back_and_is_no_product_state(tmp_path, monkeypatch):
    account = drawn(control("Data & privacy", 1), control("Personal info", 2), package=guard.ACCOUNT_CHOOSER)
    phone_factory = phone_of({"root": drawn(control("Your account", 1)), "account": account},
                             {("root", "Your account"): "account"})
    script = scripted(lambda text: turn(tap(oid(text, "Your account"), "the account page")))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("back", "account") in phone.log and not [t for t in taps(phone) if t[0] == "account"]
    assert [s.kind for s in ex.states if s.fg == guard.ACCOUNT_CHOOSER] == ["external"]


def test_a_planned_element_gone_from_the_screen_is_skipped_and_the_planner_asked_again(tmp_path, monkeypatch):
    """Review focus 2: the second step names a card of the narrowed list, but BACK left that list."""
    def script(n, text):
        if n == 1:
            return turn(tap(oid(text, "Limited Only"), "the list narrows"))
        if n == 2:
            return turn({"action": "back", "expect": "the full list"}, tap(oid(text, "JJK"), "a character opens"))
        return None
    ex, phone, planner = run(tmp_path, monkeypatch, script)
    assert len(planner.texts) == 3 and "gone from the screen" in planner.texts[2]
    assert not [key for _, key in taps(phone) if key.startswith("JJK")]
    assert any(t.step == "agent.skip" for t in trace(ex))


def test_the_play_store_mid_plan_gets_back_and_drops_the_plan(tmp_path, monkeypatch):
    """Review focus 3: a tap opens the store's payment sheet; BACK before the planner sees it, the rest dropped."""
    def script(n, text):
        if n == 1:
            return turn(tap(oid(text, at=DRAWER), "the drawer opens"))
        if n == 2:
            return turn(tap(oid(text, "Upgrade to"), "the plans"), tap(oid(text, "Settings"), "settings"))
        return None
    ex, phone, planner = run(tmp_path, monkeypatch, script)
    assert ex.counts["billing screens escaped"] >= 1 and ("back", "billing") in phone.log
    assert ("drawer", "Settings") not in taps(phone)
    assert "purchase screen" in planner.texts[2] and "com.android.vending" not in planner.texts[2].split("\n")[0]


def test_a_run_cut_short_by_the_cap_still_leaves_a_loadable_record(tmp_path, monkeypatch):
    """Review focus 4."""
    def script(n, text):
        if n == 3:
            return llm.CapReached("explore spent its $15.00 cap; raise with --usd-cap 30")
        return turn(tap(oid(text, "Limited Only") if n == 1 else oid(text, at=CHATS_TAB), "the screen changes"))
    ex, _, _ = run(tmp_path, monkeypatch, script)
    assert ex.stop_reason.startswith("$ cap") and len(ex.states) >= 3
    loads_cleanly(ex)


def test_the_planner_sees_the_raw_screen_and_nothing_on_disk_holds_a_redacted_string(tmp_path, monkeypatch):
    """Review focus 5: SIMULA_REDACT names a chip's word; the planner echoes it back in its notes."""
    monkeypatch.setenv("SIMULA_REDACT", "Trending")

    def script(n, text):
        assert "Trending" not in text
        if n == 1:
            return turn(tap(oid(text, "Limited Only"), "the list narrows"), screen="Trending home",
                        notes=["a Trending chip"])
        return None
    ex, _, planner = run(tmp_path, monkeypatch, script)
    root = next(s for s in ex.states if s.kind == "screen")
    saved = (ex.out / f"states/{root.sid}.png").read_bytes()
    assert planner.images[0] and planner.images[0][0] != stage.png_half(Image.open(io.BytesIO(saved)))
    written = [p for p in ex.out.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".md")]
    written += [ex.run_dir / "trace.jsonl", *(ex.run_dir / "exhibits").glob("*")]
    assert written and not [p.name for p in written if "trending" in p.read_text().lower()]
    assert "[redacted] home" in planner.texts[1]


def test_a_hard_blocked_control_is_logged_denied_and_never_tapped(tmp_path, monkeypatch):
    def script(n, text):
        if n == 1:
            return turn(tap(oid(text, at=DRAWER), "the drawer opens"))
        if n == 2:
            return turn(tap(oid(text, "Logout"), "signed out"))
        return None
    ex, phone, planner = run(tmp_path, monkeypatch, script)
    assert ("drawer", "Logout") not in taps(phone) and "was not run" in planner.texts[2]
    denied = [json.loads(line) for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    assert any(d["outcome"] == "denied" and "logout" in d["change_summary"] for d in denied)


def test_text_that_isnt_allowed_is_never_typed_nor_written(tmp_path, monkeypatch):
    script = scripted(lambda text: turn({"action": "type", "text": "my secret plan", "expect": "text shows"}),
                      lambda text: turn({"action": "type", "text": "popular", "expect": "text shows"}))
    ex, phone, planner = run(tmp_path, monkeypatch, script)
    assert phone.typed == [] and len(planner.texts) == 3
    assert "my secret plan" not in (ex.run_dir / "trace.jsonl").read_text()


def chat_script(recipient_first: str):
    def script(n, text):
        if n == 1:
            return turn(tap(oid(text, at=CHATS_TAB), "the chat list"))
        if n == 2:
            return turn(tap(oid(text, "Kang"), "a chat opens"))
        if n in (3, 4):
            recipient = recipient_first if n == 3 else "ai"
            return turn({"action": "start_core", "recipient": recipient, "expect": "marked"})
        return None
    return script


def test_start_core_is_refused_for_a_person_and_measured_for_an_ai(tmp_path, monkeypatch):
    def chats(clock):
        phone = janitor_like(clock)
        phone.taps[("chats", "Kang Jun-Seo (Idol x Idol), Sat, 1 chat")] = "chat"  # the card's center
        return phone
    ex, phone, planner = run(tmp_path, monkeypatch, chat_script("person"), phone_factory=chats)
    assert "start_core was refused" in planner.texts[3]
    assert ex.core and ex.core.kind == "chat" and phone.sent >= 1
    passes = [json.loads(line) for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    assert {p["loop_pass"] for p in passes if p["loop_pass"]} >= {1}
    assert any(t.step == "core" and t.note.startswith("core action: ") for t in trace(ex))


def banner_after(*sends: int, extra: tuple = ()):
    """The chat phone, with an upsell banner over the composer after these sends (and these extra elements); its
    dismiss returns to the chat."""
    def phone_factory(clock):
        phone = janitor_like(clock)
        phone.taps[("chats", "Kang Jun-Seo (Idol x Idol), Sat, 1 chat")] = "chat"
        chat = phone.screens["chat"]
        banner = [{"ref": "@tip", "type": "android.widget.TextView", "text": "Keep long chats on track",
                   "coordinates": {"x": 200, "y": 2000, "width": 600, "height": 50}},
                  {"ref": "@dismiss", "type": "android.view.ViewGroup", "text": "", "label": "Dismiss upgrade prompt",
                   "coordinates": {"x": 900, "y": 1990, "width": 72, "height": 72}},
                  {"ref": "@upgrade", "type": "android.widget.Button", "text": "Upgrade",
                   "coordinates": {"x": 200, "y": 2080, "width": 300, "height": 90}}]
        phone.screens["chat_banner"] = Screen(chat.elements + [*extra, *banner], chat.image, chat.package)
        phone.taps[("chat_banner", "Dismiss upgrade prompt")] = "chat"
        phone.after_sends = dict.fromkeys(sends, "chat_banner")
        return phone
    return phone_factory


def test_a_banner_over_the_core_action_is_recorded_dismissed_and_the_passes_go_on(tmp_path, monkeypatch):
    """Run 3: an upsell banner with its own dismiss over the composer after the first send stopped the loop at one
    pass; it is recorded, dismissed through the guard, and the passes go on. Upgrade is never tapped."""
    ex, phone, _ = run(tmp_path, monkeypatch, chat_script("ai"), phone_factory=banner_after(1))
    assert ("chat_banner", "Dismiss upgrade prompt") in taps(phone)
    assert ("chat_banner", "Upgrade") not in taps(phone)
    assert phone.sent >= 3 and ex.core_completed >= 3
    assert any("recorded and dismissed with 'Dismiss upgrade prompt'" in r for r in ex.core_results)
    stops = [json.loads(line)["loop_stop"] for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    assert any(stops)  # the banner's stop is still on its action line, with its capture


def test_the_banner_dismiss_is_never_reply_content_or_an_offer(tmp_path, monkeypatch):
    """rt-58: a new reply reading like a dismissal ('Got it — I can help') lies outside the banner and isn't control
    shaped; an offer ('Skip the wait') is no dismissal: the real dismiss is taken, and with only the offer the loop
    stops on the banner as before."""
    reply = {"ref": "@reply", "type": "android.widget.TextView", "text": "Got it — I can help",
             "coordinates": {"x": 100, "y": 900, "width": 800, "height": 100}}
    ex, phone, _ = run(tmp_path / "reply", monkeypatch, chat_script("ai"), phone_factory=banner_after(1, extra=(reply,)))
    assert ("chat_banner", "Dismiss upgrade prompt") in taps(phone)
    assert not [t for t in taps(phone) if t[1] in ("Got it — I can help", "Upgrade")]
    skip = {"ref": "@skip", "type": "android.widget.Button", "text": "Skip the wait",
            "coordinates": {"x": 600, "y": 2080, "width": 300, "height": 90}}

    def offer_only(clock):
        phone = banner_after(1, extra=(skip,))(clock)
        banner = phone.screens["chat_banner"]
        phone.screens["chat_banner"] = Screen([e for e in banner.elements if e["ref"] != "@dismiss"], banner.image,
                                              banner.package)
        return phone
    ex, phone, _ = run(tmp_path / "offer", monkeypatch, chat_script("ai"), phone_factory=offer_only)
    assert phone.sent == 1 and ex.core_hit
    assert not [t for t in taps(phone) if t[0] == "chat_banner"]


def test_a_banner_back_on_every_pass_stops_the_loop(tmp_path, monkeypatch):
    ex, phone, _ = run(tmp_path, monkeypatch, chat_script("ai"), phone_factory=banner_after(*range(1, 9)))
    assert phone.sent == stage_agent.BANNER_PASSES and ex.core_hit
    assert [t for t in taps(phone) if t == ("chat_banner", "Dismiss upgrade prompt")] == [
        ("chat_banner", "Dismiss upgrade prompt")] * (stage_agent.BANNER_PASSES - 1)

def test_start_core_runs_the_passes_at_once_and_the_tour_goes_on(tmp_path, monkeypatch):
    """Run 4: after the tour the loop had to walk back to the chat through a card that had changed, and failed. The
    passes run when start_core is accepted, on its screen; the tour resumes after them, and no core phase follows."""
    def chats(clock):
        phone = janitor_like(clock)
        phone.taps[("chats", "Kang Jun-Seo (Idol x Idol), Sat, 1 chat")] = "chat"
        return phone

    def script(n, text):
        if n < 3:
            return chat_script("ai")(n, text)
        if n == 3:
            return turn({"action": "start_core", "recipient": "ai", "expect": "marked"})
        if n == 4:
            return turn({"action": "back", "expect": "the chat list"})
        return None
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=chats)
    lines = [json.loads(line) for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    passes = [n for n, line in enumerate(lines) if line["loop_pass"]]
    later = [n for n, line in enumerate(lines) if line["action"] == "back" and not line["loop_pass"]]
    assert passes and later and max(passes) < min(n for n in later if n > min(passes))
    assert "measured now" in planner.texts[3] and phone.sent == ex.core_reps
    assert any(t.step == "core" and "ran during the tour" in t.note for t in trace(ex))

def test_a_cap_during_the_passes_in_the_tour_stops_everything_after_it(tmp_path, monkeypatch):
    def chats(clock):
        phone = janitor_like(clock)
        phone.taps[("chats", "Kang Jun-Seo (Idol x Idol), Sat, 1 chat")] = "chat"
        return phone
    ex, phone, _ = agent(tmp_path, monkeypatch, chat_script("ai"), phone_factory=chats)
    once = ex.core_once

    def capped(n):
        if n == 2:
            raise llm.CapReached("$ cap")
        return once(n)
    ex.core_once = capped
    stage.explore_app(ex)
    sends = [i for i, e in enumerate(phone.log) if e[0] == "type"]
    assert len(sends) == 1 and not [e for e in phone.log[sends[0] + 1:] if e[0] in ("launch", "swipe", "type")]
    assert any("core_loop stopped: CapReached" in r for r in ex.core_results) and ex.capped

def test_a_control_code_doesnt_take_for_the_filter_is_not_verified_and_the_run_goes_on(tmp_path, monkeypatch):
    script = scripted(lambda text: turn(tap(oid(text, "Favorites"), "favorites only")),
                      lambda text: turn({"action": "swipe", "direction": "up", "expect": "more"}, filter_set=True))
    ex, _, planner = run(tmp_path, monkeypatch, script)
    assert len(planner.texts) == 3 and "not verified: 'Favorites' isn't the content filter" in planner.texts[2]
    assert not ex.filter_taps and not ex.filtered and not ex.filter_checks
    assert [t.outcome for t in trace(ex) if t.step == "filter.recognize"] == ["error"]
    assert not (ex.run_dir / "needs-human.md").exists()


def test_an_unrelated_switch_named_as_the_filter_is_not_verified(tmp_path, monkeypatch):
    """The red team's HIGH 4: the planner can't make code verify any control it names."""
    phone_factory = phone_of({"root": drawn(control("Enable notifications", 1, "Switch", checked=False),
                                            control("Explore", 2))})
    script = scripted(lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Enable notifications")))
    ex, _, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    saved = ExploreFile.model_validate_json((ex.out / "explore.json").read_text())
    assert not ex.filtered and not ex.filter_checks and saved.content_filter is None


def test_a_filter_switch_is_wanted_in_the_state_jev_names_not_the_one_it_shows(tmp_path, monkeypatch):
    """A content-filter switch left off: Jev says on is the strict state, so the check fails and the planner is told."""
    phone_factory = phone_of({"root": drawn(control("Safe mode", 1, "Switch", checked=False), control("Explore", 2))})
    script = scripted(lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Safe mode")))
    ex, _, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ex.filter_on is True and ex.filter_checks[0][1] is False and not ex.filtered
    assert ex.filter_news.startswith("not verified")


def test_the_filter_path_holds_its_opener_across_planner_turns(tmp_path, monkeypatch):
    """The red team's MEDIUM 3: an opener tapped a turn before its option is checked with it, read off the opener."""
    options = drawn(control("Safe only", 1), control("Show all", 2))
    ImageDraw.Draw(options.image).rectangle((0, 900, 300, 1900), fill="black")
    phone_factory = phone_of({"root": drawn(control("Content filter", 1), control("Explore", 2)), "options": options,
                              "safe": drawn(control("Content filter: Safe only", 1), control("Explore", 2))},
                             {("root", "Content filter"): "options", ("options", "Safe only"): "safe"})
    script = scripted(lambda text: turn(tap(oid(text, "Content filter"), "the options")),
                      lambda text: turn(tap(oid(text, "Safe only"), "the filter shows safe")),
                      lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Content filter: Safe only")))
    ex, _, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert [c.label for c in ex.filter_taps] == ["Content filter", "Safe only"] and ex.filter_checks[0][1]


def test_a_filter_already_set_is_verified_by_the_element_the_planner_names(tmp_path, monkeypatch):
    """The strictest option shows selected already, so the planner taps nothing and names it instead."""
    def selected_already(clock):
        phone = janitor_like(clock)
        phone.taps[("launch", "Close subscription announcement")] = "limited"
        return phone
    script = scripted(lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Limited Only")))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=selected_already)
    assert not [t for t in taps(phone) if t[1] == "Limited Only" and t[0] == "root"]
    assert [t.label for t in ex.filter_taps] == ["Limited Only"] and ex.filter_checks[0][1]
    assert [t.outcome for t in trace(ex) if t.step == "filter.check"][0] == "ok"


def test_a_launch_onto_another_home_never_relaunches_for_the_filter(tmp_path, monkeypatch):
    """Run 4: signed in, the app launched onto a home with no recorded way to the screen where the filter was set,
    and the filter recheck relaunched ten times in a row. One launch, then the planner hears it is not verified."""
    def signed_in(clock):
        phone = janitor_like(clock)
        phone.taps[("launch", "Close subscription announcement")] = "limited"
        phone.screens["home2"] = drawn(control("Welcome back", 1))
        launch = phone.launch

        def later_launches(retry=True):
            if ("launch",) in phone.log:
                phone.start = "home2"
            launch(retry)
        phone.launch = later_launches
        return phone
    script = scripted(lambda text: turn(tap(oid(text, "Limited Only"), "stays on"), filter_set=True,
                                        filter_element=oid(text, "Limited Only")),
                      lambda text: turn({"action": "launch", "expect": "the app opens again"}),
                      lambda text: turn(DONE))
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=signed_in, no_send=True)
    assert ex.filter_checks and ex.filter_checks[0][1]
    assert ex.relaunches == 1  # the planner's; BACK off the new home left the app and a launch, uncounted, returned
    assert any(t.step == "filter" and "with no relaunch for it" in t.note for t in trace(ex))
    assert "Content filter: not verified" in planner.texts[2]
    # rt-58: the missing control is a failed check, so the saved run doesn't read as verified
    assert [ok for _, ok, _ in ex.filter_checks] == [True, False]
    assert any(t.step == "filter.check" and t.outcome == "error" and "control not on the launch screen" in t.note
               for t in trace(ex))
    saved = ExploreFile.model_validate_json((ex.out / "explore.json").read_text())
    assert scorecard.content_filter(saved, trace(ex))["filter verified"] == "no"


def test_a_button_core_action_keeps_the_hard_blocks(tmp_path, monkeypatch):
    """rt-58: the core exception to the send family holds for a chat's composer and send only; a 'Submit' button
    marked as the core action is refused on every pass."""
    script = scripted(lambda text: turn({"action": "start_core", "element": oid(text, "Submit"), "recipient": "ai",
                                         "expect": "marked"}))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_of({"root": drawn(control("Submit", 1))}))
    assert ex.core and ex.core.kind != "chat" and ("root", "Submit") not in taps(phone)
    lines = [json.loads(line) for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    assert not [line for line in lines if line["loop_pass"] and line["outcome"] == "ok" and line["action"] == "tap"]


def test_an_offer_word_anywhere_is_no_dismissal():
    """rt-58: 'Close or Upgrade' is an offer; only a close icon's own description may name an upgrade."""
    for label in ("Close or Upgrade", "Close and buy now", "Close to unlock", "Close / Subscribe", "Skip the wait"):
        assert not stage_agent.dismissal(label) and not stage_agent.dismissal(label, icon=True), label
    assert not stage_agent.dismissal("Dismiss upgrade prompt")  # as words it is an offer's text
    assert stage_agent.dismissal("Dismiss long chat upgrade prompt", icon=True)
    assert all(stage_agent.dismissal(label) for label in ("Not now", "Close", "x", "No thanks", "Close tips"))


def counting_jev(asked):
    def jev(state, instructions, labels, backend):
        if instructions.startswith("Which control does this"):
            asked.append(instructions)
            want = instructions.removeprefix("Which control does this: ").rstrip("?")
            pick = next((i for i, label in enumerate(labels) if label.startswith(want)), 0)
            return decide.ChoiceResult(option_id=f"o{pick + 1:02d}", confidence=0.9, probabilities={}, model="jev-fake",
                                       tokens_in=100, tokens_out=0, usd=0.00001, seconds=0.1)
        return fake_jev(state, instructions, labels, backend)
    return jev


def test_dial_on_grounds_an_intent_with_jev(tmp_path, monkeypatch):
    asked = []

    script = scripted(lambda text: turn({"action": "tap", "intent": "Limited Only", "expect": "the list narrows"}))
    ex, phone, _ = run(tmp_path, monkeypatch, script, jev=counting_jev(asked))
    assert asked and ("root", "Limited Only") in taps(phone) and ex.counts["steps grounded by Jev"] == 1


def test_dial_off_never_asks_jev_and_runs_one_step(tmp_path, monkeypatch):
    asked = []

    script = scripted(lambda text: turn({"action": "tap", "intent": "Limited Only", "expect": "x"}),
                      lambda text: turn(tap(oid(text, "Limited Only")), tap(oid(text, at=CHATS_TAB))))
    ex, phone, planner = run(tmp_path, monkeypatch, script, jev=counting_jev(asked), explore_jev="off")
    assert asked == [] and ("root", "Limited Only") in taps(phone)
    assert not [t for t in taps(phone) if t[0] == "limited"]  # the second step was never run


def test_dial_shadow_asks_jev_beside_every_tap_and_traces_it(tmp_path, monkeypatch):
    asked = []

    script = scripted(lambda text: turn(tap(oid(text, "Limited Only"), "Limited Only")))
    ex, _, _ = run(tmp_path, monkeypatch, script, jev=counting_jev(asked), explore_jev="shadow")
    shadows = [t for t in trace(ex) if t.step == "jev.shadow"]
    assert asked and shadows and shadows[0].note.startswith("agree")


def test_turns_that_find_no_new_state_stop_the_run(tmp_path, monkeypatch):
    def script(n, text):
        return turn({"action": "swipe", "direction": "down", "expect": "nothing new"})
    ex, _, planner = run(tmp_path, monkeypatch, script)
    assert ex.stop_reason == f"no new state in {STALE_TURNS} planner turns"
    assert len(planner.texts) == STALE_TURNS


def test_ads_seen_and_tapped_are_written_live_as_ad_lines(tmp_path, monkeypatch):
    def script(n, text):
        if n == 1:
            return turn(tap(oid(text, at=CHATS_TAB), "the chat list"))
        if n == 2:
            ad = oid(text, at=DRAWER)
            return turn(tap(ad, "the advertiser's page"), ads=[ad], ad_notes=f"{ad}: banner; Acme Shoes; shopping")
        return None
    ex, _, _ = run(tmp_path, monkeypatch, script)
    lines = [AdLine.model_validate_json(raw) for raw in (ex.out / "ads.jsonl").read_text().splitlines()]
    assert [(a.format, a.advertiser, a.category, a.tapped) for a in lines] == [
        ("banner", "Acme Shoes", "shopping", False), ("banner", "Acme Shoes", "shopping", True)]
    assert lines[1].landing and lines[1].landing != PACKAGE and ex.relaunches == 0
    assert not (ex.out / "states" / "ads.jsonl").exists()



def test_the_hard_block_words_are_part_of_explores_fingerprint(tmp_path):
    assert config.ROOT / "config" / "hard_blocks.toml" in cli.stage_inputs("explore", new_run(tmp_path))


# ---------- the red team's findings on c75e9fc ----------

def moving(to: str, screens: dict):
    """A drawn root with Explore, and a planner that leaves the phone on another screen while it answers."""
    held = {}

    def factory(clock):
        held["phone"] = phone_of({"root": drawn(control("Explore", 1), control("Settings", 2)), **screens},
                                 backs={to: "root"})(clock)
        return held["phone"]

    def script(n, text):
        if n == 1:
            held["phone"].go(to)
            return turn(tap(oid(text, "Explore"), "explore opens"))
        return None
    return factory, script


def test_a_purchase_sheet_that_opens_while_the_planner_thinks_gets_back_before_the_tap(tmp_path, monkeypatch):
    factory, script = moving("billing", {"billing": drawn(control("Buy now", 1), package=guard.BILLING)})
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=factory, no_send=True)
    assert not [t for t in taps(phone) if t[0] == "billing"] and ("back", "billing") in phone.log
    assert ex.counts["billing screens escaped"] >= 1 and "screen changed" in planner.texts[1]


def test_the_guard_reads_the_screen_as_it_is_at_the_tap(tmp_path, monkeypatch):
    """A control the screen changed under while the planner answered is found again, or not tapped."""
    factory, script = moving("changed", {"changed": drawn(control("Delete account", 1), control("Settings", 2))})
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=factory, no_send=True)
    assert not [t for t in taps(phone) if t[0] == "changed"]
    assert any(t.step == "agent.moved" for t in trace(ex))


HANDLE = "owner-fake@example.invalid"


def test_what_the_planner_says_about_the_account_never_reaches_the_cache_or_the_trace(tmp_path, monkeypatch):
    """The red team's HIGH 3: the planner reads the raw screen, so its answers and failures are scrubbed in llm.call."""
    monkeypatch.setenv("SIMULA_REDACT", HANDLE)

    def script(n, text):
        if n == 1:
            return llm.LLMFailure("error", f"the provider choked on {HANDLE}", raw=HANDLE)
        return turn(DONE, screen=HANDLE, notes=[f"signed in as {HANDLE}"])
    phone_factory = phone_of({"root": drawn(control(HANDLE, 1), control("Explore", 2))})
    ex, _, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert len(planner.texts) == 2 and ex.found == ["signed in as [redacted]"]
    written = [*ex.cache_dir.glob("*.json"), ex.run_dir / "trace.jsonl", *ex.out.rglob("*.json*")]
    assert len(list(ex.cache_dir.glob("*.json"))) >= 2 and not [p.name for p in written if HANDLE in p.read_text()]


def test_the_scrub_isnt_part_of_the_cache_key(tmp_path, monkeypatch):
    calls = []

    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model)
        return llm.Reply(text=f"hello {HANDLE}", model=model, tokens_in=10, tokens_out=5)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    ask = dict(trace_path=tmp_path / "trace.jsonl", stage="explore", step="s", model="claude-sonnet-5-5", effort=None,
               system="sys", messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}], max_tokens=50,
               budget=llm.Budget.for_stage("explore", tmp_path / "trace.jsonl", 1.0), cache_dir=tmp_path / "cache")
    said, _ = llm.call(**ask, scrub=lambda text: text.replace(HANDLE, "[redacted]"))
    again, _ = llm.call(**ask)
    assert said == again == "hello [redacted]" and len(calls) == 1


def test_nothing_runs_after_the_wall_clock(tmp_path, monkeypatch):
    """The red team's HIGH 5: one deadline for the tour, the core loop and the replay check."""
    def script(n, text):  # the core passes run when start_core is marked: mark it once the clock has run out
        if n == 2:
            ex.clock.t = ex.deadline
            mark = {"action": "start_core", "element": oid(text, "A page"), "recipient": "ai", "expect": "marked"}
            return turn(mark, {"action": "swipe", "direction": "down", "expect": "the top"})
        return turn(tap(oid(text, "Explore"), "explore opens"))
    ex, phone, _ = agent(tmp_path, monkeypatch, script,
                         phone_factory=phone_of({"root": drawn(control("Explore", 1)),
                                                 "page": drawn(control("A page", 1))}, {("root", "Explore"): "page"}))
    stage.explore_app(ex)
    late = phone.log[next(i for i, e in enumerate(phone.log) if e[0] == "tap" and e[2] == "Explore") + 1:]
    assert ex.stop_reason == "wall-time cap" and not [e for e in late if e[0] in ("tap", "swipe", "type", "launch")]
    assert ex.core_results == ["not run: wall-time cap"]
    loads_cleanly(ex)
    with pytest.raises(Halt):
        ex.perform(stage.Move("swipe"), None)


def test_a_cap_anywhere_stops_every_device_action_after_it(tmp_path, monkeypatch):
    """The red team's HIGH 6: the cap hits Jev mid-plan; no later phase launches, taps or swipes."""
    script = scripted(lambda text: turn({"action": "swipe", "direction": "down", "expect": "Explore"},
                                        {"action": "tap", "intent": "Explore", "expect": "explore"}))
    ex, phone, _ = agent(tmp_path, monkeypatch, script, phone_factory=phone_of({"root": drawn(control("Explore", 1))}),
                         no_send=True)
    seen = []

    def capped(*args):
        seen.append(len(phone.log))
        ex.budget.cap = ex.budget.spent
        return AgentExplorer.ground(ex, *args)
    monkeypatch.setattr(ex, "ground", capped)
    stage.explore_app(ex)
    assert ex.stop_reason.startswith("$ cap") and ex.capped
    assert not [e for e in phone.log[seen[0]:] if e[0] in ("tap", "swipe", "type", "launch")]
    loads_cleanly(ex)


def test_the_agents_taps_never_count_against_the_scripted_deny_list(tmp_path, monkeypatch):
    """The red team's MEDIUM 1: Following is on the scripted deny-list, not a hard block."""
    script = scripted(lambda text: turn(tap(oid(text, "Following"), "followed characters")))
    ex, phone, _ = run(tmp_path, monkeypatch, script)
    assert ("root", "Following") in taps(phone) and ex.denied_executed == 0


def test_an_ads_landing_in_the_app_is_ad_evidence_not_a_product_state(tmp_path, monkeypatch):
    """The red team's MEDIUM 2: the ad opens an advertiser page in the app's own package."""
    page = drawn(control("Advertiser website", 1), control("Offer one", 2), control("Offer two", 3))
    phone_factory = phone_of({"root": drawn(control("Sponsored deal", 1), control("Explore", 2)), "page": page},
                             {("root", "Sponsored deal"): "page"})

    def script(n, text):
        if n == 1:
            ad = oid(text, "Sponsored deal")
            return turn(tap(ad, "the advertiser"), ads=[ad], ad_notes=f"{ad}: native; Acme; shopping")
        return None
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert not any(c.label.startswith(("Advertiser", "Offer")) for s in ex.states for c in s.cands)
    tapped = AdLine.model_validate_json((ex.out / "ads.jsonl").read_text().splitlines()[-1])
    assert tapped.tapped and tapped.landing == "Title Advertiser website" and ("back", "page") in phone.log


def test_another_app_in_front_at_the_moment_of_acting_stops_the_plan(tmp_path, monkeypatch):
    """Greptile on 2e43d0e: an app that comes to the front while the planner answers gets no swipe and no typing."""
    held = {}

    def factory(clock):
        held["phone"] = phone_of({"root": drawn(control("Explore", 1)),
                                  "other": drawn(control("Search", 1, "EditText", focused=True),
                                                 package="com.example.other")})(clock)
        return held["phone"]

    def script(n, text):
        if n == 1:
            held["phone"].go("other")
            return turn({"action": "swipe", "direction": "up", "expect": "more"},
                        {"action": "type", "text": "popular", "expect": "a search"})
        return None
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=factory, no_send=True)
    assert not [e for e in phone.log if e[0] in ("swipe", "type") and e[1] == "other"] and phone.typed == []
    assert "com.example.other came to the front" in planner.texts[1]


def test_a_cached_answer_is_scrubbed_however_it_was_written(tmp_path, monkeypatch):
    """Greptile on 2e43d0e: an entry written without a scrub, answer or failure, is scrubbed when read with one."""
    def answering(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        return llm.Reply(text=f"hello {HANDLE}", model=model, tokens_in=10, tokens_out=5)

    def failing(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        raise llm.LLMFailure("refusal", f"choked on {HANDLE}", raw=HANDLE)  # kept, unlike a lost call

    def scrub(text):
        return text.replace(HANDLE, "[redacted]")
    for provider, text in ((answering, "hi"), (failing, "bye")):
        monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
        ask = dict(trace_path=tmp_path / "trace.jsonl", stage="explore", step="s", model="claude-sonnet-5-5",
                   effort=None, system="sys", messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
                   max_tokens=50, budget=llm.Budget.for_stage("explore", tmp_path / "trace.jsonl", 1.0),
                   cache_dir=tmp_path / "cache")
        try:
            llm.call(**ask)
        except llm.LLMFailure:
            pass
        monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)  # a second call must come from the cache
        if provider is answering:
            assert llm.call(**ask, scrub=scrub)[0] == "hello [redacted]"
        else:
            with pytest.raises(llm.LLMFailure) as failed:
                llm.call(**ask, scrub=scrub)
            assert HANDLE not in str(failed.value) + failed.value.raw


# ---------- Fable's review on 2e43d0e ----------

def test_a_consent_step_after_the_account_row_is_tapped_through_its_flow_button(tmp_path, monkeypatch):
    """M1: the chooser's Continue is allowed (its text alone, not text and label joined)."""
    consent = drawn(control("someone@example.com", 1), control("Cancel", 2), control("Continue", 3),
                    package=guard.ACCOUNT_CHOOSER)
    phone_factory = phone_of({"root": drawn(control("Continue with Google", 1)), "home": drawn(control("Inside", 1)),
                              "chooser": drawn(control("someone@example.com", 1), control("Add another account", 2),
                                               package=guard.ACCOUNT_CHOOSER), "consent": consent},
                             {("root", "Continue with Google"): "chooser",
                              ("chooser", "someone@example.com"): "consent", ("consent", "Continue"): "home"})
    script = scripted(lambda text: turn(tap(oid(text, "Continue with Google"), "the chooser")),
                      lambda text: turn(tap(oid(text, "[redacted]"), "the consent")),
                      lambda text: turn(tap(oid(text, "Continue"), "signed in")))
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("consent", "Continue") in taps(phone) and planner.texts[3].startswith(f"App in front: {PACKAGE}")


def test_a_flow_button_on_a_google_dialog_that_names_a_deletion_stays_refused(tmp_path, monkeypatch):
    """L1: the word guard runs on Google's screens too."""
    ex, _, _ = agent(tmp_path, monkeypatch, scripted(), phone_factory=phone_of({"root": drawn(control("Explore", 1))}))
    dialog = [control("someone@example.com", 1, "TextView"), control("Remove this account", 2, "TextView"),
              control("OK", 3)]
    ok = next(c for c in stage.ob.controls(dialog, ex.device) if c.tree_label == "OK")
    assert "hard block" in ex.refusal(ok, guard.ACCOUNT_CHOOSER, dialog, dialog, False)


def test_a_google_screen_with_a_price_is_a_purchase_and_gets_back(tmp_path, monkeypatch):
    """L2: a payment sheet in Play services shows the account and a price: it is no chooser."""
    pay = drawn(control("someone@example.com", 1, "TextView"), control("$4.99 per month", 2, "TextView"),
                control("Continue", 3), package=guard.ACCOUNT_CHOOSER)
    phone_factory = phone_of({"root": drawn(control("Upgrade", 1)), "pay": pay}, {("root", "Upgrade"): "pay"},
                             backs={"pay": "root"})
    script = scripted(lambda text: turn(tap(oid(text, "Upgrade"), "plans")))
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("back", "pay") in phone.log and not [t for t in taps(phone) if t[0] == "pay"]
    assert not any(text.startswith(f"App in front: {guard.ACCOUNT_CHOOSER}") for text in planner.texts)
    assert planner.texts[1].count("A purchase screen opened") == 1 and ex.counts["billing screens escaped"] >= 1
    assert ("back", "root") not in phone.log  # one BACK per sheet: none past it, out of the app


def filter_switch_phone(on: bool = True, **more):
    switch = drawn(control("Safe only", 1, "Switch", checked=on), control("Help", 2), control("Explore", 3))
    return {"root": switch, **more}


def test_a_return_from_another_app_rechecks_the_filter_before_the_planner_reads_it(tmp_path, monkeypatch):
    """M2: a launch brings the app back; the planner reads a fresh check, never the one before it left."""
    phone_factory = phone_of(filter_switch_phone(browser=drawn(control("A web page", 1), package="com.example.web")),
                             {("root", "Help"): "browser"})
    script = scripted(lambda text: turn(tap(oid(text, "Help"), "help"), filter_set=True,
                                        filter_element=oid(text, "Safe only")),
                      lambda text: turn(DONE))
    ex, _, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ex.returns and "Content filter: verified (check 2)" in planner.texts[1]


def test_a_switch_flipped_in_place_is_reported_on(tmp_path, monkeypatch):
    """M3: the listing shows a control's live state, and a state change is a change."""
    screens = filter_switch_phone(on=False, flipped=filter_switch_phone(on=True)["root"])
    phone_factory = phone_of(screens, {("root", "Safe only"): "flipped"})
    script = scripted(lambda text: turn(tap(oid(text, "Safe only"), "the switch shows on")))
    ex, _, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert len(ex.states) == 1 and re.search(r"Switch 'Safe only' .*\(on\)", planner.texts[1])
    assert "didn't happen" not in planner.texts[1]


def test_the_scrub_takes_the_identity_parts_as_whole_words(tmp_path, monkeypatch):
    """L4: as ob.redact does."""
    monkeypatch.setenv("SIMULA_TEST_NAME", "Quillon Varga")
    ex, _, _ = agent(tmp_path, monkeypatch, scripted(), phone_factory=phone_of({"root": drawn(control("Explore", 1))}))
    assert ex.scrub("hello Quillon, Quillonx") == "hello [redacted], Quillonx"


# ---------- Codex's review on 2e43d0e ----------

def one_screen(tmp_path, monkeypatch, *elements, **screens):
    """An agent on a drawn root of these elements, launched, its planner done at once."""
    ex, phone, _ = agent(tmp_path, monkeypatch, scripted(),
                         phone_factory=phone_of({"root": drawn(*elements), **screens}), no_send=True)
    ex.relaunch(first=True)
    return ex, phone


def test_a_redacted_name_is_no_account_row_and_account_words_are_refused_on_google(tmp_path, monkeypatch):
    """Item 1: only an email in the unredacted list makes an account row; manage, privacy and the like never pass."""
    monkeypatch.setenv("SIMULA_REDACT", "Jamie")
    page = drawn(control("Manage account for Jamie", 1), control("Privacy", 2), package=guard.ACCOUNT_CHOOSER)
    phone_factory = phone_of({"root": drawn(control("Continue with Google", 1)), "page": page},
                             {("root", "Continue with Google"): "page"}, backs={"page": "root"})
    script = scripted(lambda text: turn(tap(oid(text, "Continue with Google"), "the chooser")))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("back", "page") in phone.log and not [t for t in taps(phone) if t[0] == "page"]
    ex2, _ = one_screen(tmp_path / "unit", monkeypatch, control("Explore", 1))
    row = [control("someone@example.com", 1, "TextView"), control("Manage your account", 2)]
    manage = next(c for c in stage.ob.controls(row, ex2.device) if c.tree_label == "Manage your account")
    assert "account chooser" in ex2.refusal(manage, guard.ACCOUNT_CHOOSER, row, row, False)


def test_an_account_row_is_judged_by_its_unredacted_email(tmp_path, monkeypatch):
    """Run 1: the chooser's name line ('[redacted] Om') was refused six times, since its email sits in a sibling line
    of the same row. An email in the raw text, on the element or in its row, makes an account row; a redacted name
    alone or an account-management word never does."""
    ex, _ = one_screen(tmp_path, monkeypatch, control("Explore", 1))

    def box(e, x, y, w, h):
        return {**e, "coordinates": {"x": x, "y": y, "width": w, "height": h}}

    def judged(raw, redacted, label):
        c = next(c for c in stage.ob.controls(redacted, ex.device) if c.tree_label == label)
        return ex.refusal(c, guard.ACCOUNT_CHOOSER, redacted, raw, False)

    own = [control("Jamie Om jamie@example.com", 1)]
    assert judged(own, [control("[redacted] Om", 1)], "[redacted] Om") == ""
    assert judged([control("Jamie", 1)], [control("[redacted]", 1)], "[redacted]")
    assert "account chooser" in judged([control("Manage account for Jamie", 1)],
                                       [control("Manage account for [redacted]", 1)], "Manage account for [redacted]")

    def chooser(name, address, add):
        return [box(control("", 1, "LinearLayout"), 70, 1265, 940, 169),
                box(control("", 2, "ImageView"), 133, 1312, 74, 74),
                box(control(name, 3), 239, 1297, 205, 56), box(control(address, 4, "TextView"), 239, 1353, 404, 49),
                box(control("", 5, "LinearLayout"), 70, 1434, 940, 130),
                box(control(add, 6), 228, 1471, 719, 56)]

    raw = chooser("Jamie Om", "jamie@example.com", "Add another account")
    redacted = chooser("[redacted] Om", "[redacted]", "Add another account")
    assert judged(raw, redacted, "[redacted] Om") == ""
    assert "account chooser" in judged(raw, redacted, "Add another account")
    nameless = chooser("Jamie Om", "Jamie", "Add another account")
    assert "account chooser" in judged(nameless, redacted, "[redacted] Om")
    # rt-58: a card holding an email header lends it to no separate button in it: the holder must be row-sized
    card = [box(control("", 1, "LinearLayout"), 70, 180, 940, 650),
            box(control("jamie@example.com", 2, "TextView"), 130, 230, 600, 50),
            box(control("Personal info", 3), 130, 500, 600, 70)]
    shown = [card[0], {**card[1], "text": "[redacted]"}, card[2]]
    assert "account chooser" in judged(card, shown, "Personal info")
    compact = [box(card[0], 70, 180, 940, 280), card[1], box(card[2], 130, 360, 600, 70)]  # rt-58 again: row-sized
    assert "account chooser" in judged(compact, [compact[0], shown[1], compact[2]], "Personal info")

def bar(*xs: int) -> list[dict]:
    """A bottom tab bar of wordless icons, as an app draws one, centered at these x."""
    return [{**control("", 10 + n, "ViewGroup", identifier=""), "coordinates": {
        "x": x - 83, "y": 2170, "width": 167, "height": 167}} for n, x in enumerate(xs)]


def test_a_tab_the_bar_redrew_under_the_same_screen_is_tapped_live(tmp_path, monkeypatch):
    """Run 2: signing in redrew the tab bar (new icons) while the screen kept its fingerprint, and the recorded crop of
    the middle tab refused it as covered nine turns in a row; the live bar's tab is offered and tapped."""
    before = drawn(control("Sign in", 1), *bar(155, 540, 924))
    after = Screen(before.elements, before.image.copy(), PACKAGE)
    ImageDraw.Draw(after.image).ellipse((480, 2200, 600, 2310), fill=(250, 200, 0))
    phone_factory = phone_of({"root": before, "after": after, "tab": drawn(control("Tab page", 1))},
                             {("root", "Sign in"): "after", ("after", "540,2253"): "tab"})
    script = scripted(lambda text: turn(tap(oid(text, "Sign in"), "signed in")),
                      lambda text: turn(tap(oid(text, at=(540, 2253)), "the middle tab opens")))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("after", "540,2253") in taps(phone)


def test_an_unlisted_overlay_across_the_bar_keeps_the_recorded_tab_refused(tmp_path, monkeypatch):
    """rt-58: a sheet the element list doesn't show, drawn across the bar, changes the bar's gaps too: the recorded
    crop check stands and the tab isn't tapped."""
    before = drawn(control("Sign in", 1), *bar(155, 540, 924))
    after = Screen(before.elements, before.image.copy(), PACKAGE)
    draw = ImageDraw.Draw(after.image)
    draw.rectangle((0, 2230, 1080, 2337), fill=(250, 250, 250))
    for x in range(0, 1080, 60):
        draw.rectangle((x, 2230, x + 25, 2337), fill=(20, 20, 20))
    phone_factory = phone_of({"root": before, "after": after, "tab": drawn(control("Tab page", 1))},
                             {("root", "Sign in"): "after", ("after", "540,2253"): "tab"})
    script = scripted(lambda text: turn(tap(oid(text, "Sign in"), "signed in")),
                      lambda text: turn(tap(oid(text, at=(540, 2253)), "the middle tab opens")))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("after", "540,2253") not in taps(phone)


def test_a_tab_the_bar_gained_after_home_taught_the_tabs_is_no_content_under_the_bar(tmp_path, monkeypatch):
    """Signing in grew the bar from three tabs to five: the new ones are tabs, not content scrolled under the bar."""
    phone_factory = phone_of({"root": drawn(control("Sign in", 1), *bar(155, 540, 924)),
                              "after": drawn(control("Signed in", 1), *bar(155, 347, 540, 733, 924)),
                              "bell": drawn(control("Bell page", 1))},
                             {("root", "Sign in"): "after", ("after", "733,2253"): "bell"})
    script = scripted(lambda text: turn(tap(oid(text, "Sign in"), "signed in")),
                      lambda text: turn(tap(oid(text, at=(733, 2253)), "the fourth tab opens")))
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("after", "733,2253") in taps(phone)

def test_a_covered_tap_names_its_cover_and_twice_covered_is_marked_not_offered(tmp_path, monkeypatch):
    """A tap skipped as covered tells the planner what lies over it and that BACK may uncover it; covered twice on
    one screen, the control stays listed, marked covered, and a step naming it isn't run until a move runs."""
    ex, phone = one_screen(tmp_path, monkeypatch, control("Explore", 1), control("Go", 2))
    explore = next(c for c in ex.current.cands if c.label == "Explore")
    image = ex.obs.image.copy()
    ImageDraw.Draw(image).rectangle((0, 380, 1080, 620), fill=(250, 250, 250))
    for x in range(0, 1080, 60):
        ImageDraw.Draw(image).rectangle((x, 380, x + 25, 620), fill=(20, 20, 20))
    sheet = {**control("Welcome tips", 3, "TextView"), "coordinates": {"x": 0, "y": 420, "width": 1080, "height": 200}}
    elements = [*ex.obs.elements, sheet]
    ex.obs = dataclasses.replace(ex.obs, image=image, elements=elements, cands=stage.ob.controls(elements, ex.device))
    ex.ids = {"o01": explore}
    ex.turn = AgentTurn(screen="home", goal="cover", steps=[AgentStep(action="tap", element="o01", expect="opens")])
    step = AgentStep(action="tap", element="o01", expect="opens")
    ex.run_step(step)
    assert "'Welcome tips' covers it" in " ".join(ex.news) and "BACK" in " ".join(ex.news)
    assert not [t for t in taps(phone) if t[1] == "Explore"]
    ex.news = []
    ex.run_step(step)
    assert "(covered" in ex.situation(ex.current)
    ex.news = []
    ex.run_step(step)
    assert "covered twice" in " ".join(ex.news) and not [t for t in taps(phone) if t[1] == "Explore"]
    ex.run_step(AgentStep(action="swipe", direction="up", expect="scrolls"))
    assert not ex.covered and "(covered" not in ex.situation(ex.current)

def test_a_json_escaped_handle_and_a_propagated_stop_are_scrubbed(tmp_path, monkeypatch):
    """Item 2: the decoded values are scrubbed, and a stop that leaves llm.call carries a scrubbed message."""
    name = "José"
    ask = dict(trace_path=tmp_path / "trace.jsonl", stage="explore", step="s", model="claude-sonnet-5-5", effort=None,
               system="sys", messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}], max_tokens=50,
               budget=llm.Budget.for_stage("explore", tmp_path / "trace.jsonl", 1.0), cache_dir=tmp_path / "cache")

    def answering(model, *args):
        return llm.Reply(text=json.dumps({"said": f"hello {name}"}), model=model, tokens_in=10, tokens_out=5)

    def stopping(model, *args):
        raise llm.ProviderUnavailable(f"quota for {name}")
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", answering)
    said, reply = llm.call(**ask, scrub=lambda text: text.replace(name, "[redacted]"))
    assert json.loads(said) == {"said": "hello [redacted]"} and name not in json.loads(reply.text)["said"]
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", stopping)
    with pytest.raises(llm.ProviderUnavailable) as stopped:
        llm.call(**{**ask, "system": "other"}, scrub=lambda text: text.replace(name, "[redacted]"))
    assert name not in str(stopped.value) and name not in (tmp_path / "trace.jsonl").read_text()


def test_a_lone_filter_control_must_show_a_strict_value(tmp_path, monkeypatch):
    """Item 3: an opener reading "Show all" is the filter, but not set strict; Jev is asked about its value."""
    asked = []

    def jev(state, instructions, labels, backend):
        asked.append(instructions)
        if "content or safety filter" in instructions:  # the opener is the filter control
            pick = next(i for i, label in enumerate(labels) if label.startswith("Content filter"))
            return decide.ChoiceResult(option_id=f"o{pick + 1:02d}", confidence=0.9, probabilities={}, model="fake",
                                       tokens_in=10, tokens_out=0, usd=0.000001, seconds=0.1)
        return fake_jev(state, instructions, labels, backend)
    phone_factory = phone_of({"root": drawn(control("Content filter: Show all", 1), control("Sort", 2))})
    script = scripted(lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Content filter")))
    ex, _, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True, jev=jev)
    assert not ex.filtered and not ex.filter_checks and "doesn't show a strict setting" in ex.filter_news
    assert any(q == stage.FILTER_MENU_QUESTION for q in asked)


def test_after_the_limit_back_only_leaves_another_app_and_nothing_launches(tmp_path, monkeypatch):
    """Item 4: past the clock, BACK in the app, a terminate, a replay step and a wait all stop."""
    ex, phone = one_screen(tmp_path, monkeypatch, control("Explore", 1))
    ex.clock.t = ex.deadline
    with pytest.raises(Halt):
        ex.perform(stage.Move("back"), None)
    with pytest.raises(Halt):
        ex.relaunch(why="after the clock")
    with pytest.raises(Halt):
        ex.sleep(1.0)
    assert not [e for e in phone.log if e[0] == "back"]
    ex.obs.fg = "com.example.other"  # another app in front: BACK is how the agent leaves it
    ex.perform(stage.Move("back"), None)
    assert phone.log[-1][0] == "back"


def test_a_wait_never_runs_past_the_deadline(tmp_path, monkeypatch):
    ex, _ = one_screen(tmp_path, monkeypatch, control("Explore", 1))
    ex.clock.t = ex.deadline - 2
    ex.sleep(10)
    assert ex.clock.t == ex.deadline


def test_the_play_store_arriving_during_the_element_read_gets_back_not_the_tap(tmp_path, monkeypatch):
    """Item 5: the foreground is read again after the slow element read."""
    ex, phone = one_screen(tmp_path, monkeypatch, control("Continue", 1),
                           billing=drawn(control("Continue", 1), package=guard.BILLING))
    phone.backs["billing"] = "root"
    target = next(c for c in ex.obs.cands if c.tree_label == "Continue")
    read = phone.elements

    def slow():
        phone.go("billing")
        return read()
    monkeypatch.setattr(phone, "elements", slow)
    with pytest.raises(ScreenMoved):
        ex.tap(target, ex.obs.elements)
    assert ("back", "billing") in phone.log and not [t for t in taps(phone) if t[0] == "billing"]


def test_the_core_loop_types_only_into_the_composer_it_focused(tmp_path, monkeypatch):
    """Item 6: focus elsewhere (or no focus) refuses the core loop's text."""
    composer = {**control("Message", 2, "EditText", focused=False),
                "coordinates": {"x": 100, "y": 1800, "width": 800, "height": 100}}
    ex, phone = one_screen(tmp_path, monkeypatch, control("Name", 1, "EditText", focused=True), composer)
    assert ex.perform(stage.Move("type", text=stage.CORE_MESSAGES[0]), None, core=True)
    ex.text_box = next(c for c in ex.obs.cands if c.tree_label == "Message")
    assert ex.perform(stage.Move("type", text=stage.CORE_MESSAGES[0]), None, core=True) and not phone.typed


def test_a_planned_control_is_found_again_only_as_itself(tmp_path, monkeypatch):
    """Item 7: another id under the same words, the same words far away, or a picture no longer drawn: not tapped."""
    first, second = control("Open", 1), control("Open", 2)
    first["identifier"], second["identifier"] = "app:id/open_draft", "app:id/open_saved"
    ex, phone = one_screen(tmp_path, monkeypatch, first, control("Explore", 3))
    planned = next(c for c in ex.obs.cands if c.tree_label == "Open")
    with pytest.raises(ScreenMoved):
        ex.resolve(planned, stage.ob.controls([second], ex.device))
    worded = next(c for c in ex.obs.cands if c.tree_label == "Explore")
    far = {**control("Explore", 3), "identifier": "", "coordinates": {"x": 100, "y": 2000, "width": 800,
                                                                       "height": 100}}
    worded.ident = ""
    with pytest.raises(ScreenMoved):
        ex.resolve(worded, stage.ob.controls([far], ex.device))
    picture = stage.ob.Candidate("a picture", "picture", Rect(x=100, y=1400, w=300, h=300), None, "")
    with pytest.raises(ScreenMoved):
        ex.resolve(picture, [])


def test_the_filters_opener_is_the_tap_code_reads_as_it_not_the_first_hop(tmp_path, monkeypatch):
    """Item 8: Settings, then the filter's opener, then its option."""
    options = drawn(control("Safe only", 1), control("Show all", 2))
    ImageDraw.Draw(options.image).rectangle((0, 900, 300, 1900), fill="black")
    phone_factory = phone_of({"root": drawn(control("Settings", 1), control("Explore", 2)),
                              "settings": drawn(control("Settings page", 1), control("Content filter", 2)),
                              "options": options,
                              "safe": drawn(control("Settings page", 1), control("Content filter: Safe only", 2))},
                             {("root", "Settings"): "settings", ("settings", "Content filter"): "options",
                              ("options", "Safe only"): "safe"})
    script = scripted(lambda text: turn(tap(oid(text, "Settings"))),
                      lambda text: turn(tap(oid(text, "Content filter"))),
                      lambda text: turn(tap(oid(text, "Safe only"))),
                      lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Content filter: Safe only")))
    ex, _, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert [c.label for c in ex.filter_taps] == ["Content filter", "Safe only"] and ex.filter_checks[0][1]


def ad_phone(**more):
    return phone_of({"root": drawn(control("Sponsored deal", 1), control("Explore", 2)), **more},
                    {("root", "Sponsored deal"): "landing"})


def ad_turn(text):
    ad = oid(text, "Sponsored deal")
    return turn(tap(ad, "the advertiser"), ads=[ad])


def test_an_ads_return_exempts_only_itself_from_the_relaunch_count(tmp_path, monkeypatch):
    """Item 9."""
    script = scripted(ad_turn, lambda text: turn({"action": "launch", "expect": "a fresh launch"}))
    ex, _, _ = run(tmp_path, monkeypatch, script, no_send=True,
                   phone_factory=ad_phone(landing=drawn(control("Advertiser", 1), package="com.example.web")))
    assert ex.relaunches == 1 and not ex.ad_leave


def test_an_ad_tap_whose_landing_cant_be_read_is_still_recorded(tmp_path, monkeypatch):
    """Item 10: the tap ran, so its action line and its tapped ad line are written whatever the capture does."""
    held = {}

    def script(n, text):
        if n == 1:
            def broken():
                raise McpTimeout("the landing capture timed out")
            monkeypatch.setattr(held["ex"], "observe", broken)
            return ad_turn(text)
        return None
    ex, phone, _ = agent(tmp_path, monkeypatch, script, no_send=True,
                         phone_factory=ad_phone(landing=drawn(control("Advertiser", 1))))
    held["ex"] = ex
    stage.explore_app(ex)
    lines = [json.loads(line) for line in (ex.out / "actions.jsonl").read_text().splitlines()]
    ads = [AdLine.model_validate_json(line) for line in (ex.out / "ads.jsonl").read_text().splitlines()]
    assert ("root", "Sponsored deal") in taps(phone) and any("an ad" in line["change_summary"] for line in lines)
    assert ads[-1].tapped and ads[-1].landing is None



# ---------- Greptile on 837dd26 ----------

def test_with_no_focus_reported_the_core_loop_types_into_the_composer_it_tapped(tmp_path, monkeypatch):
    """The real chat capture reports focus on no element: the composer the core pass tapped takes the message, and a
    box the device does say is focused, above it, refuses it."""
    ex, phone, _ = agent(tmp_path, monkeypatch, scripted(), no_send=True,
                         phone_factory=phone_of({"root": capture("luzia", "luzia-chat-thread", package=PACKAGE)}))
    ex.relaunch(first=True)
    composer = next(c for c in ex.obs.cands if c.kind == "EditText")
    assert not ex.tap(composer, ex.obs.elements, core=True)
    assert not ex.perform(stage.Move("type", text=stage.CORE_MESSAGES[0]), None, core=True)
    assert phone.typed == [stage.CORE_MESSAGES[0]]
    phone.screens["root"].elements.append({**control("Name", 1, "EditText", focused=True), "identifier": ""})
    assert ex.perform(stage.Move("type", text=stage.CORE_MESSAGES[1]), None, core=True)
    assert phone.typed == [stage.CORE_MESSAGES[0]]


def test_with_no_focus_reported_a_query_goes_into_the_search_box_the_plan_tapped(tmp_path, monkeypatch):
    phone_factory = phone_of({"root": drawn(control("Search", 1, "EditText"), control("Explore", 2))})
    script = scripted(lambda text: turn(tap(oid(text, "Search"), "the keyboard")),
                      lambda text: turn({"action": "type", "text": "popular", "expect": "popular"}))
    _, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert phone.typed and set(phone.typed) == {"popular"}  # the replay check types it again


def test_a_google_consent_step_with_no_account_row_is_signed_through(tmp_path, monkeypatch):
    """Chooser, then a consent screen that names no account: its Continue is tapped and the app comes back."""
    chooser = drawn(control("someone@example.com", 1), control("Add another account", 2),
                    package=guard.ACCOUNT_CHOOSER)
    consent = drawn(control("Allow the app to see your name", 1, "TextView"), control("Cancel", 2),
                    control("Continue", 3), package=guard.ACCOUNT_CHOOSER)
    phone_factory = phone_of({"root": drawn(control("Continue with Google", 1)), "chooser": chooser,
                              "consent": consent, "home": drawn(control("Inside", 1))},
                             {("root", "Continue with Google"): "chooser",
                              ("chooser", "someone@example.com"): "consent", ("consent", "Continue"): "home"})
    script = scripted(lambda text: turn(tap(oid(text, "Continue with Google"), "the chooser")),
                      lambda text: turn(tap(oid(text, "[redacted]"), "the consent")),
                      lambda text: turn(tap(oid(text, "Continue"), "signed in")))
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=phone_factory, no_send=True)
    assert ("consent", "Continue") in taps(phone) and ("back", "consent") not in phone.log
    assert planner.texts[3].startswith(f"App in front: {PACKAGE}")


def test_another_app_before_an_action_is_a_return_not_a_relaunch(tmp_path, monkeypatch):
    """Greptile on 837dd26: the app resumes where it was left, so leaving the other app counts no relaunch."""
    held = {}

    def factory(clock):
        held["phone"] = phone_of({"root": drawn(control("Explore", 1)),
                                  "other": drawn(control("A notice", 1), package="com.example.other")})(clock)
        return held["phone"]

    def script(n, text):
        if n == 1:
            held["phone"].go("other")
            return turn(tap(oid(text, "Explore"), "explore opens"))
        return None
    ex, phone, _ = run(tmp_path, monkeypatch, script, phone_factory=factory, no_send=True)
    assert not [t for t in taps(phone) if t[0] == "other"] and ex.relaunches == 0 and ex.returns



def test_consent_prose_about_data_is_sign_in_but_a_manage_button_is_not(tmp_path, monkeypatch):
    """Greptile on 2eec2ce: the account-management words count on a control's label, never on prose."""
    ex, _, _ = agent(tmp_path, monkeypatch, scripted(), phone_factory=phone_of({"root": drawn(control("Explore", 1))}))
    prose = control("The app will access your data and your activity on this device", 1, "TextView")
    consent = [prose, control("Allow", 2)]
    assert ex.signing([], consent)
    assert not ex.signing([], [prose, control("Allow", 2), control("Manage your Google Account", 3)])


def test_the_prompt_asks_for_every_tab_a_fresh_conversation_and_no_early_done():
    """Runs 1-3: the start tab was never tapped, the core pass continued a long chat, and done came early."""
    said = " ".join((stage.PROMPTS / "agent.md").read_text().split())
    assert "the one you start on included (tap its own control)" in said
    assert "Prefer starting a new conversation" in said
    assert "only when no unvisited control of the app's own navigation" in said
    assert "Open at least one item of each list or feed" in said
    assert "never plan the same step the same way on your next turn" in said
