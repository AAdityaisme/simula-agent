"""The explorer end to end on a fake device made of fixture captures: tab sweep first, caps, the content
filter, Jev and Sonnet through the stage's cap and cache, externals, billing, a relaunch, dynamic regions,
the core-loop pass, and outputs in the frozen explore/ format."""

import functools
import json

import pytest

from simula import decide, llm, runlog
from simula.contracts import ActionLine, ExploreFile, StateFile
from simula.device import observe as ob
from simula.device.mcp import parse_elements
from simula.stages import explore as stage
from tests.fake_device import PACKAGE, Clock, FakePhone, blank, capture, fake_jev, new_run
from tests.fake_device import explore as run_explorer
from tests.fake_device import explorer as new_explorer

TABS = ["155,2253", "347,2253", "540,2253", "732,2253", "924,2253"]
TRANSITIONS = {"push", "modal", "tab", "back", "replace", "unknown"}


def janitor_like(clock: Clock) -> FakePhone:
    screens = {"launch": capture("janitorai", "j01_launch"), "root": capture("janitorai", "janitorai-a"),
               "limited": capture("janitorai", "janitorai-limited"), "chats": capture("janitorai", "j02_home"),
               "notif": capture("janitorai", "j07_tab4"), "profile": capture("janitorai", "j08_tab5"),
               "search": capture("janitorai", "j05_tab2"), "drawer": capture("janitorai", "j10_drawer_settled"),
               "chat": capture("luzia", "luzia-chat-thread", package=PACKAGE),
               "web": capture("aol", "aol-article-external"), "billing": blank("com.android.vending"),
               "store": blank("com.android.vending")}
    taps = {("launch", "Close subscription announcement"): "root", ("root", "Limited Only"): "limited",
            ("chats", "Kang Jun-Seo (Idol x Idol)"): "chat", ("chats", "996,209"): "web",
            ("limited", "JJK - GOJO’S RELATIVE"): "chat",
            ("root", "996,209"): "drawer", ("limited", "996,209"): "drawer",
            ("drawer", "Upgrade to Janitor Plus"): "billing", ("notif", "996,209"): "store"}
    for screen in ("root", "limited", "chats", "notif", "profile", "search"):
        targets = ["limited", "search", "chats", "notif", "profile"]
        taps.update({(screen, key): target for key, target in zip(TABS, targets, strict=True)})
    return FakePhone(screens=screens, start="launch", taps=taps, clock=clock, backs={"store": "launcher"}, splash=4,
                     replies={"chat": ["Hello! I can help with stories, advice, and everyday questions."] * 3},
                     dirty={"root": (100, 1400, 400, 1500)})


def explore(tmp_path, monkeypatch, phone_factory=None, **kwargs):
    return run_explorer(tmp_path, monkeypatch, phone_factory or janitor_like, **kwargs)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    try:
        yield explore(tmp_path_factory.mktemp("explore"), monkeypatch)
    finally:
        monkeypatch.undo()


def lines(ex) -> list[ActionLine]:
    return [ActionLine.model_validate_json(raw) for raw in (ex.out / "actions.jsonl").read_text().splitlines()]


def test_outputs_follow_the_frozen_format(run):
    ex, _ = run
    explore_file = ExploreFile.model_validate_json((ex.out / "explore.json").read_text())
    assert explore_file.coverage.states_found == len(ex.states) >= 8
    for s in ex.states:
        state_file = StateFile.model_validate_json((ex.out / "states" / f"{s.sid}.json").read_text())
        assert (ex.out / state_file.screenshot).exists()
        parse_elements(json.loads((ex.out / state_file.elements_reply).read_text()))
    assert (ex.run_dir / "exhibits" / "01-explore.md").exists()
    assert not (ex.out / ".scratch").exists()


def test_launch_dialog_is_recorded_before_it_is_dismissed(run):
    ex, _ = run
    first = ex.states[0]
    assert first.kind == "modal" and first.parent == ex.states[1].sid
    dialog = StateFile.model_validate_json((ex.out / "states" / f"{first.sid}.json").read_text())
    home = StateFile.model_validate_json((ex.out / "states" / f"{first.parent}.json").read_text())
    assert dialog.parent_id == home.state_id and home.parent_id is None and home.kind == "screen"
    dismiss = next(line for line in lines(ex) if line.outcome == "ok")
    assert dismiss.from_state == first.sid and dismiss.outcome == "ok" and dismiss.mcp_ref


def test_every_bottom_tab_is_swept_before_ranked_taps(run):
    ex, _ = run
    assert len(ex.tabs) == 5 and all(ex.tab_to.get(t.key) for t in ex.tabs)
    moves = [line for line in lines(ex) if line.outcome == "ok" and line.action == "tap"]
    first_ranked = next(i for i, line in enumerate(moves) if line.transition != "tab" and i > 1)
    assert sum(line.transition == "tab" for line in moves[:first_ranked]) >= 4


def test_content_filter_is_chosen_and_verified(run):
    ex, _ = run
    assert [t.label for t in ex.filter_taps] == ["Limited Only"]
    assert ex.filter_checks and all(ok for _, ok, _ in ex.filter_checks)
    assert (ex.run_dir / ex.filter_checks[0][2]).exists()


def test_caps_hold_and_no_denied_tap_runs(run):
    ex, phone = run
    assert ex.tour_actions <= ex.limits["actions"]
    denied = [line for line in lines(ex) if line.outcome == "denied"]
    assert denied and all(line.to_state is None for line in denied)
    tapped = {key for kind, *rest in phone.log if kind == "tap" for key in rest[1:]}
    assert not tapped & {"Logout", "Blocks", "Following", "Favorites"}
    assert ex.denied_executed == 0
    assert f"{len(denied)} logged, 0 executed" in (ex.run_dir / "exhibits" / "01-explore.md").read_text()


def test_every_edge_has_a_transition(run):
    ex, _ = run
    edges = [line for line in lines(ex) if line.outcome == "ok" and line.to_state and line.action != "relaunch"]
    assert edges and {line.transition for line in edges} <= TRANSITIONS - {"unknown"}
    assert {"tab", "back"} <= {line.transition for line in edges}


def test_leaving_the_app_records_an_external_state_then_goes_back(run):
    ex, _ = run
    external = [s for s in ex.states if s.kind == "external"]
    assert external
    by_from = {line.from_state: line for line in lines(ex) if line.outcome == "ok"}
    assert all(by_from[s.sid].action == "back" for s in external)


def test_billing_screen_gets_back_at_once(run):
    ex, phone = run
    trace = runlog.read_trace(ex.run_dir / "trace.jsonl")
    assert any(line.step == "billing" for line in trace)
    on_billing = [entry for entry in phone.log if entry[0] != "shot" and entry[1:2] == ("billing",)]
    assert on_billing and all(entry == ("back", "billing") for entry in on_billing)
    seen = [n for n, entry in enumerate(phone.log) if entry == ("shot", "billing")]
    assert all(phone.log[n + 1] == ("back", "billing") for n in seen)
    assert "English only" in (ex.run_dir / "exhibits" / "01-explore.md").read_text()


def test_a_revisit_records_dynamic_regions(run):
    ex, _ = run
    chats = next(s for s in ex.states if s.visits > 1 and s.dynamic)
    assert all(r.w > 0 and r.h > 0 for r in chats.dynamic)


def test_jev_and_sonnet_go_through_the_stage_budget(run):
    ex, _ = run
    trace = runlog.read_trace(ex.run_dir / "trace.jsonl")
    assert any(line.decider == "jev" and line.usd > 0 for line in trace)
    assert any(line.decider == "model" and line.step.startswith("icons.") for line in trace)
    assert ex.budget.spent > 0


def test_core_loop_sends_and_measures_the_reply(run):
    ex, phone = run
    assert ex.core and ex.core.kind == "chat"
    assert len(phone.typed) >= 2
    sends = [line for line in lines(ex) if "reply started" in line.change_summary]
    assert len(sends) == len(phone.typed) and "chars" in sends[0].change_summary


def test_replay_check_reaches_the_recorded_states(run):
    ex, _ = run
    matched, total = ex.replay
    assert total and matched / total >= 0.9


def test_no_send_turns_the_core_loop_off(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, no_send=True)
    assert not phone.typed and ex.core_results == ["off (--no-send)"]


def test_second_run_answers_jev_from_the_cache(tmp_path, monkeypatch):
    explore(tmp_path / "first", monkeypatch, cache_dir=tmp_path / "cache")
    ex, _ = explore(tmp_path / "second", monkeypatch, cache_dir=tmp_path / "cache")
    jev = [line for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.decider == "jev" and line.model]
    assert jev and all(line.cache_hit for line in jev)


def test_blocked_root_is_recorded_and_asks_for_a_person(tmp_path, monkeypatch):
    def gone(clock):
        return FakePhone(screens={"home": blank("com.android.launcher")}, start="home", taps={}, clock=clock)
    ex, _ = explore(tmp_path, monkeypatch, phone_factory=gone)
    assert ex.stop_reason.startswith("blocked root")
    state = StateFile.model_validate_json((ex.out / "states" / "s01.json").read_text())
    assert state.kind == "blocked" and state.blocked_reason
    assert ExploreFile.model_validate_json((ex.out / "explore.json").read_text()).blocked_state_ids == ["s01"]
    assert (ex.run_dir / "needs-human.md").exists()


def test_replay_flag_refuses_to_drive_the_device(tmp_path):
    with pytest.raises(llm.ReplayMiss):
        stage.run(new_run(tmp_path, replay=True))


def test_the_loop_stops_at_a_dialog_the_send_opened_not_at_words_in_a_reply(tmp_path, monkeypatch):
    def limited(clock):
        phone = janitor_like(clock)
        phone.replies["chat"] = ["I've hit my limits before, and pushed past them."]
        phone.screens["limit"] = capture("janitorai", "j01_launch")
        phone.taps[("chat", "sendButton")] = "limit"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=limited)
    loop = lines(ex)
    passes = [line for line in loop if line.loop_pass]
    assert passes and all(line.loop_pass >= 1 for line in passes)
    assert [line.loop_stop for line in loop if line.loop_stop] == ["dialog opened"]
    assert len(phone.typed) == 1 and ex.core_hit.startswith("dialog opened")
    tour = [line for line in loop if line.loop_pass is None and line.outcome == "ok"]
    assert tour and not any(line.loop_stop for line in tour)


BIO = "Start the story as her rival from the academy"


def detail_first(clock):
    """A feed item opens a detail page whose bio says "start" above its real New chat control."""
    phone = janitor_like(clock)
    detail = capture("luzia", "luzia-home", package=PACKAGE)
    detail.elements.insert(0, {"ref": "@bio", "type": "android.widget.TextView", "text": BIO,
                               "coordinates": {"x": 42, "y": 700, "width": 996, "height": 120}})
    phone.screens["detail"] = detail
    phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "detail"
    phone.taps[("detail", "New chat")] = "chat"
    return phone


def test_the_walk_into_an_item_passes_the_bio_and_reaches_the_composer(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=detail_first)
    assert ex.core.kind == "chat" and phone.sent
    assert ("tap", "detail", "New chat") in phone.log
    assert not any(entry[:3] == ("tap", "detail", BIO) for entry in phone.log)


def test_a_failed_capture_in_the_core_loop_keeps_the_tour(tmp_path, monkeypatch):
    from simula.device.mcp import McpReplyError

    def flaky(clock):
        phone = janitor_like(clock)
        save = phone.screenshot

        def screenshot(path, size=None):
            if phone.typed:
                raise McpReplyError("screenshot not written")
            return save(path, size)
        phone.screenshot = screenshot
        return phone
    ex, _ = explore(tmp_path, monkeypatch, phone_factory=flaky)
    explore_file = ExploreFile.model_validate_json((ex.out / "explore.json").read_text())
    assert explore_file.coverage.states_found >= 8
    assert all((ex.out / "states" / f"{s.sid}.json").exists() for s in ex.states)
    assert any("McpReplyError" in r for r in ex.core_results)
    failed = [line for line in lines(ex) if line.outcome == "error"]
    assert any("observing after the move failed" in line.change_summary for line in failed)


def test_a_cold_start_that_times_out_the_first_dumps_still_explores(tmp_path, monkeypatch):
    def slow_start(clock):
        phone = janitor_like(clock)
        phone.hung_lists = 2
        return phone
    ex, _ = explore(tmp_path, monkeypatch, phone_factory=slow_start)
    assert ex.root is not None and len(ex.states) >= 8
    notes = [line.note for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.step == "launch"]
    assert notes and "McpTimeout" in notes[0]


PRICED_REPLIES = ["Hi! I can help with stories, advice, and questions.", "The Pixel 8a at $299 is a solid pick.",
                  "Once upon a time a lighthouse keeper counted ships every night."]


def chatty(clock):
    phone = janitor_like(clock)
    phone.replies["chat"] = PRICED_REPLIES
    return phone


def test_a_growing_chat_gets_all_eight_passes_with_no_false_stop(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=chatty, budget="deep")
    assert ex.core.kind == "chat" and phone.sent == ex.core_reps == 8
    assert not ex.core_hit and not any(line.loop_stop for line in lines(ex))
    assert phone.chats["chat"][2][0] == "What's the best phone under $300?"
    phone.screen = "chat"
    assert min(y for _, _, y, _, _ in phone.bubbles(2028)) < ob.TOP_CHROME_BOTTOM_PX
    sends = [line for line in lines(ex) if line.loop_pass and "reply started" in line.change_summary]
    assert [line.loop_pass for line in sends] == list(range(1, 9))


def test_a_limit_dialog_stops_the_loop_on_the_pass_it_appears(tmp_path, monkeypatch):
    def limited(clock):
        phone = chatty(clock)
        phone.screens["limit"] = capture("janitorai", "j01_launch")
        phone.after_sends = {3: "limit"}
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=limited, budget="deep")
    stops = [(line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert phone.sent == 3 and stops == [(3, "dialog opened")] and ex.core_hit.endswith("on pass 3")
    assert "limit" in ex.checklist()[0]


def test_the_walk_asks_the_ranker_among_several_and_never_taps_one_twice(tmp_path, monkeypatch):
    def two_actions(clock):
        phone = detail_first(clock)
        dead = {"ref": "@start", "type": "android.widget.Button", "text": "Start chat icon",
                "coordinates": {"x": 42, "y": 1850, "width": 300, "height": 100}}
        phone.screens["detail"].elements.insert(1, dead)
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=two_actions)
    trace = runlog.read_trace(ex.run_dir / "trace.jsonl")
    walk = [line for line in trace if "(core loop: the item's main action)" in line.note]
    assert ex.core.kind == "chat" and phone.sent
    assert [line.note.split("'")[1] for line in walk] == ["Start chat icon", "New chat"]
    assert walk[0].decider == "jev"


def test_when_jev_says_feed_a_comment_box_inside_an_item_is_never_typed_into(tmp_path, monkeypatch):
    def news_like(clock):
        phone = janitor_like(clock)
        del phone.taps[("chats", "Kang Jun-Seo (Idol x Idol)")]
        phone.screens["comments"] = capture("luzia", "luzia-chat-thread", package=PACKAGE)
        phone.replies["comments"] = ["Nice article!"]
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "comments"
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, news_like)
    monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick="open an item"))
    stage.explore_app(ex)
    assert ex.core.kind == "feed" and not phone.typed and not phone.sent


def test_a_send_gift_button_on_the_chat_row_is_never_tapped(tmp_path, monkeypatch):
    def gifting(clock):
        phone = janitor_like(clock)
        gift = {"ref": "@gift", "type": "android.widget.Button", "text": "Send gift",
                "coordinates": {"x": 160, "y": 2133, "width": 110, "height": 110}}
        phone.screens["chat"].elements.insert(0, gift)
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=gifting)
    assert phone.sent and not any(entry[:1] == ("tap",) and entry[2] == "Send gift" for entry in phone.log)


def test_the_launch_teaser_is_never_the_paywall_and_its_call_to_action_is_followed(tmp_path, monkeypatch):
    def plans_behind_the_teaser(clock):
        phone = janitor_like(clock)
        phone.screens["plans"] = capture("luzia", "luzia-paywall", package=PACKAGE)
        phone.taps[("launch", "See janitor+")] = "plans"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=plans_behind_the_teaser)
    paywall = ex.by_id[ex.paywall]
    assert paywall.priced and not paywall.launch and ("tap", "launch", "See janitor+") in phone.log
    assert "paywall_or_membership" in ex.checklist()[0]
    assert "prices: " in (ex.run_dir / "exhibits" / "01-explore.md").read_text()


def test_teaser_words_and_content_words_answer_nothing_on_the_checklist(run):
    ex, _ = run
    answered, still_open = ex.checklist()
    assert {"paywall_or_membership", "limit"} <= set(still_open)
    assert any(s.upsell and s.launch for s in ex.states) and not ex.priced_paywall()


def test_with_no_price_anywhere_the_best_upsell_stands_marked_no_price_seen(tmp_path, monkeypatch):
    def unpriced_upgrade(clock):
        phone = janitor_like(clock)
        plans = capture("luzia", "luzia-paywall", package=PACKAGE)
        plans.elements = [{**e, "text": "Monthly membership"} if ob.PRICE.search(e.get("text") or "") else e
                          for e in plans.elements]
        phone.screens["plans"] = plans
        phone.taps[("drawer", "Upgrade to Janitor Plus")] = "plans"
        return phone
    ex, _ = explore(tmp_path, monkeypatch, phone_factory=unpriced_upgrade)
    paywall = ex.by_id[ex.paywall]
    assert paywall.upsell and not paywall.priced and not paywall.launch
    assert "no price seen" in (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    assert "paywall_or_membership" in ex.checklist()[1]


def test_a_sheet_over_the_tab_bar_is_closed_with_back_not_a_relaunch(tmp_path, monkeypatch):
    def sheet_over_tabs(clock):
        phone = janitor_like(clock)
        phone.screens["sheet"] = capture("janitorai", "j06_tab3")
        phone.taps[("chats", "996,209")] = "sheet"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=sheet_over_tabs, budget="deep")
    assert not any(why.startswith("tap toward") for why in ex.relaunch_reasons)
    trace = runlog.read_trace(ex.run_dir / "trace.jsonl")
    assert any("covers the tab bar" in line.note for line in trace)


def test_a_chosen_chat_that_cant_be_reached_again_is_reached_through_an_item(tmp_path, monkeypatch):
    unreachable = [True]
    at_core = stage.Explorer.at_core

    def first_goto_fails(self, n):
        if unreachable[0]:
            unreachable[0] = False
            return False
        return at_core(self, n)
    monkeypatch.setattr(stage.Explorer, "at_core", first_goto_fails)
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=detail_first)
    assert ex.core.kind == "chat" and phone.sent == ex.core_reps
    assert "could not get back" not in " ".join(ex.core_results)
