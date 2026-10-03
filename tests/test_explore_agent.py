"""The agent explorer offline: a fake phone made of fixture captures, a fake planner returning canned AgentTurns
through llm.call, and fake Jev. Every hard block, the filter check, the stops, the dial and the record."""

import io
import json
import re

from PIL import Image

from simula import cli, config, decide, llm, runlog
from simula.contracts import AdLine, ExploreFile
from simula.stages import explore as stage
from simula.stages import model as model_stage
from simula.stages.explore_agent import STALE_TURNS, AgentExplorer
from tests.fake_device import PACKAGE, Clock, capture, fake_jev, fake_sonnet, new_run
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


def test_the_google_account_chooser_is_acted_on_not_left(tmp_path, monkeypatch):
    """Review focus 1: Continue with Google opens Play services' chooser, another package; the agent picks the
    account there and comes back, with no relaunch."""
    def with_chooser(clock):
        phone = janitor_like(clock)
        phone.screens["chooser"] = capture("janitorai", "j08_tab5", package="com.google.android.gms")
        phone.taps[("root", "Limited Only")] = "chooser"
        phone.taps[("chooser", "View public profile")] = "limited"
        return phone

    script = scripted(lambda text: turn(tap(oid(text, "Limited Only"), "the account chooser")),
                      lambda text: turn(tap(oid(text, "View public profile"), "signed in")))
    ex, phone, planner = run(tmp_path, monkeypatch, script, phone_factory=with_chooser)
    assert ("chooser", "View public profile") in taps(phone)
    chooser = next(s for s in ex.states if s.fg == "com.google.android.gms")
    assert chooser.kind == "screen" and ex.relaunches == 0 and not ex.returns


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


def test_a_failed_filter_check_tells_the_planner_and_the_run_goes_on(tmp_path, monkeypatch):
    script = scripted(lambda text: turn(tap(oid(text, "Favorites"), "favorites only")),
                      lambda text: turn({"action": "swipe", "direction": "up", "expect": "more"}, filter_set=True))
    ex, _, planner = run(tmp_path, monkeypatch, script)
    assert len(planner.texts) == 3 and "not verified (check 1)" in planner.texts[2]
    assert [t.outcome for t in trace(ex) if t.step == "filter.check"][0] == "error"
    assert not (ex.run_dir / "needs-human.md").exists()


def test_a_filter_already_set_is_verified_by_the_element_the_planner_names(tmp_path, monkeypatch):
    """The strictest option shows selected already, so the planner taps nothing and names it instead."""
    script = scripted(lambda text: turn(tap(oid(text, "Limited Only"), "the list narrows")),
                      lambda text: turn({"action": "swipe", "direction": "down", "expect": "the top"}),
                      lambda text: turn(DONE, filter_set=True, filter_element=oid(text, "Limited Only")))
    ex, _, _ = run(tmp_path, monkeypatch, script)
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
