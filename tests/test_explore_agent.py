"""The agent explorer offline: a fake phone made of fixture captures, a fake planner returning canned AgentTurns
through llm.call, and fake Jev. Every hard block, the filter check, the stops, the dial and the record."""

import io
import json
import re

import pytest
from PIL import Image, ImageDraw

from simula import cli, config, decide, llm, runlog
from simula.contracts import AdLine, ExploreFile
from simula.device import guard
from simula.stages import explore as stage
from simula.stages import model as model_stage
from simula.stages.explore_agent import STALE_TURNS, AgentExplorer, Halt
from tests.fake_device import PACKAGE, Clock, FakePhone, Screen, fake_jev, fake_sonnet, new_run
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
    def script(n, text):
        if n == 2:
            ex.clock.t = ex.deadline
            return turn({"action": "swipe", "direction": "down", "expect": "the top"})
        mark = {"action": "start_core", "element": oid(text, "Explore"), "recipient": "ai", "expect": "marked"}
        return turn(tap(oid(text, "Explore"), "explore opens"), mark)
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
