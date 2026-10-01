"""The explorer end to end on a fake device made of fixture captures: tab sweep first, caps, the content
filter, Jev and Sonnet through the stage's cap and cache, externals, billing, a relaunch, dynamic regions,
the core-loop pass, and outputs in the frozen explore/ format."""

import functools
import json
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from simula import decide, llm, runlog
from simula.contracts import ActionLine, ExploreFile, StateFile
from simula.device import observe as ob
from simula.device.mcp import parse_elements
from simula.stages import explore as stage
from tests import fake_device
from tests.fake_device import PACKAGE, Clock, FakePhone, Screen, blank, capture, fake_jev, new_run
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
    assert not tapped & {"Logout", "Following", "Favorites"}
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


def on_chats(tmp_path, monkeypatch, phone_factory=janitor_like):
    """An explorer launched and on the chats tab, a screen the tour recorded from home."""
    ex, phone = new_explorer(tmp_path, monkeypatch, phone_factory)
    ex.relaunch(first=True)
    phone.go("chats")
    chats = ex.current = ex.record(ex.observe(), ex.current, stage.Move("tap"), ex.obs)
    return ex, phone, chats


def back_into_another_app(clock):
    """BACK from chats leaves the app (its task ends, as Perplexity's did) into another app, whose BACK goes deeper."""
    phone = janitor_like(clock)
    phone.screens.update(aol=capture("aol", "aol-home"), article=capture("aol", "aol-article"))
    phone.backs.update(chats="aol", aol="article")
    return phone


def test_back_out_of_the_app_relaunches_without_walking_the_other_app(tmp_path, monkeypatch):
    ex, phone, chats = on_chats(tmp_path, monkeypatch, back_into_another_app)
    away = ex.act(stage.Move("back"), purpose="nav")
    assert away.kind == "external" and chats.sid in ex.exits
    ex.leave()
    assert not [e for e in phone.log if e[0] == "back" and e[1] in ("aol", "article")]
    assert ex.relaunches == 1 and "did not find the app where it was left" in ex.relaunch_reasons[0]
    assert ex.current is ex.root and not ex.returns


def test_back_that_left_the_app_is_not_taken_from_that_screen_again(tmp_path, monkeypatch):
    ex, phone, chats = on_chats(tmp_path, monkeypatch, back_into_another_app)
    home, ex.tabs = ex.root, []  # chats as a screen without the tab bar, so back_to_root would take BACK there
    ex.edges[(chats.sid, home.sid)] = stage.Move("back")  # BACK from chats once went home
    ex.act(stage.Move("back"), purpose="nav")
    ex.leave()
    phone.go("chats")
    ex.current = ex.record(ex.observe(), None, None, None)
    backs = sum(e == ("back", "chats") for e in phone.log)
    ex.back_to_root()
    ex.act(stage.Move("back", decider="model"))
    assert ex.current is chats and sum(e == ("back", "chats") for e in phone.log) == backs
    assert ex.route(chats, home) is None


def test_a_launch_that_finds_the_app_as_it_was_is_a_return_not_a_relaunch(tmp_path, monkeypatch):
    """A tap opened another app over chats; the app's task lives on, and a launch brings chats back."""
    ex, phone, chats = on_chats(tmp_path, monkeypatch)
    link = next(c for c in chats.cands if c.point == (996, 209))
    web = ex.act(stage.Move("tap", link), purpose="nav")
    assert web.kind == "external" and ex.left is chats
    ex.leave()
    assert ex.current is chats and ex.relaunches == 0 and ex.returns == [f"from {web.fg} back to {chats.sid}"]
    assert ("back", "web") not in phone.log
    last = lines(ex)[-1]
    assert (last.action, last.from_state, last.to_state) == ("launch", web.sid, chats.sid)
    assert all(to != web.sid for _, _, to in ex.segments[-1])
    assert "Returns to the app as it was left, by a launch (not relaunches): 1" in stage.exhibit(ex, None)


def test_a_route_hop_that_lands_in_another_app_returns_by_a_launch_and_goes_on(tmp_path, monkeypatch):
    """The recorded tap from chats to home now opens another app over chats: goto takes no BACK inside it (its
    back_to link), and gets back without a relaunch."""
    ex, phone, chats = on_chats(tmp_path, monkeypatch)
    home = ex.root
    ex.edges[(chats.sid, home.sid)] = stage.Move("tap", next(c for c in chats.cands if c.point == (996, 209)))
    assert ex.goto(home) and ("back", "web") not in phone.log
    assert ex.relaunches == 0 and ex.returns == [f"from {phone.screens['web'].package} back to {chats.sid}"]


def test_a_system_dialog_over_the_app_that_a_launch_leaves_in_front_gets_back(tmp_path, monkeypatch):
    def dialog_over_chats(clock):
        phone = janitor_like(clock)
        phone.screens["dialog"] = blank("com.google.android.permissioncontroller")
        phone.taps[("chats", "996,209")] = "dialog"
        phone.in_task = {"dialog"}
        return phone
    ex, phone, chats = on_chats(tmp_path, monkeypatch, dialog_over_chats)
    ex.act(stage.Move("tap", next(c for c in chats.cands if c.point == (996, 209))), purpose="nav")
    ex.leave()
    launched = max(n for n, e in enumerate(phone.log) if e == ("launch",))
    assert ("back", "dialog") in phone.log[launched:]
    assert ex.current is chats and ex.relaunches == 0 and not ex.returns


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


def test_a_device_lost_in_the_core_loop_keeps_the_tour_and_fails_the_explore(tmp_path, monkeypatch):
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
    ex, _ = new_explorer(tmp_path, monkeypatch, flaky)
    with pytest.raises(stage.ExploreFailed, match="^device error in core_loop"):
        stage.explore_app(ex)
    explore_file = ExploreFile.model_validate_json((ex.out / "explore.json").read_text())
    assert explore_file.coverage.states_found >= 8
    assert all((ex.out / "states" / f"{s.sid}.json").exists() for s in ex.states)
    assert any("McpReplyError" in r for r in ex.core_results)
    exhibit = (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    assert "0 completed." in exhibit and "Not stopped by the app, but only 0 of" in exhibit
    assert "which doesn't show the app has none" in exhibit and "met no limit" not in exhibit
    failed = [line for line in lines(ex) if line.outcome == "error"]
    assert any("observing after the move failed" in line.change_summary for line in failed)
    assert "device failed after the tour" in (ex.run_dir / "needs-human.md").read_text()


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
    exhibit = (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    assert "8 of 8 passes attempted, 8 completed." in exhibit and "All 8 passes ran and met no limit." in exhibit


def test_sends_that_bring_no_reply_are_attempted_passes_and_the_exhibit_claims_no_clean_run(tmp_path, monkeypatch):
    def silent(clock):
        phone = janitor_like(clock)

        def send():
            phone.draft, phone.sent = "", phone.sent + 1
        phone.send = send
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=silent)
    assert ex.core.kind == "chat" and phone.sent == ex.core_reps and not ex.core_hit
    exhibit = (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    assert f"{ex.core_reps} of {ex.core_reps} passes attempted, 0 completed." in exhibit
    assert "no reply within" in exhibit and "met no limit" not in exhibit
    assert "which doesn't show the app has none" in exhibit


def test_a_limit_dialog_stops_the_loop_on_the_pass_it_appears(tmp_path, monkeypatch):
    def limited(clock):
        phone = chatty(clock)
        phone.screens["limit"] = capture("janitorai", "j01_launch")
        phone.after_sends = {3: "limit"}
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=limited, budget="deep")
    stops = [(line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert phone.sent == 3 and stops == [(3, "dialog opened")] and ex.core_hit.endswith("on pass 3")
    assert "limit" in ex.checklist()[1]
    exhibit = (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    assert "3 of 8 passes attempted, 3 completed." in exhibit and "Stopped by the app: dialog opened" in exhibit


def test_the_walk_asks_the_model_for_the_start_control_and_never_taps_one_twice(tmp_path, monkeypatch):
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
    assert {line.decider for line in walk} == {"model"}


def test_when_jev_says_feed_a_comment_box_inside_an_item_is_never_typed_into(tmp_path, monkeypatch):
    def news_like(clock):
        phone = janitor_like(clock)
        del phone.taps[("chats", "Kang Jun-Seo (Idol x Idol)")]
        phone.screens["comments"] = capture("luzia", "luzia-chat-thread", package=PACKAGE)
        phone.replies["comments"] = ["Nice article!"]
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "comments"
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, news_like)
    monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick="open and read items"))
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
    assert any("control covered on the live screen" in line.note for line in trace)


def tall_sheet(tmp_path, monkeypatch, moved: str = ""):
    """A list row's tall sheet over its parent, whose controls stay listed behind it, recorded from the parent. The
    parent control whose words start with moved sat 3 cells lower in the parent's capture (a list still loading)."""
    def screen(name: str) -> Screen:
        fix = Path(__file__).parent / "fixtures" / "invariants"
        image = Image.new("RGB", (1080, 2400), (30, 30, 30))
        image.paste(Image.open(fix / f"{name}-top.png").convert("RGB"), (0, 0))
        return Screen(parse_elements(json.loads((fix / f"{name}.elements.json").read_text())), image, PACKAGE)
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screens.update(parent=screen("sheet-parent"), sheet=screen("sheet-over"))
    phone.screen = "parent"
    parent = ex.current = ex.record(ex.observe(), None, None, None)
    before = ex.obs
    for c in before.cands:
        if moved and c.tree_label.startswith(moved):
            c.rect = c.rect.model_copy(update={"y": c.rect.y + 63})
    phone.screen = "sheet"
    sheet = ex.current = ex.record(ex.observe(), parent, stage.Move("tap", before.cands[0]), before)
    return ex, parent, sheet, before


def test_a_tall_sheet_is_recorded_over_its_parent_with_its_box(tmp_path, monkeypatch):
    ex, parent, sheet, _ = tall_sheet(tmp_path, monkeypatch)
    assert (sheet.kind, sheet.parent) == ("sheet", parent.sid) and sheet.box
    ex.write(None)
    saved = {s.sid: StateFile.model_validate_json((ex.out / "states" / f"{s.sid}.json").read_text()) for s in ex.states}
    assert saved[sheet.sid].box == sheet.box and saved[parent.sid].box is None


def scrolled_feed(tmp_path, monkeypatch):
    """A feed (j04) and the view a swipe brought (j11): the same frame around other rows."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screens.update(first=capture("janitorai", "j04_tab1"), other=capture("janitorai", "j11_home_relaunched"))
    phone.screen = "first"
    feed = ex.current = ex.record(ex.observe(), None, None, None)
    before = ex.obs
    phone.screen = "other"
    scrolled = ex.current = ex.record(ex.observe(), feed, stage.Move("swipe"), before)
    return ex, phone, feed, scrolled


def test_a_list_with_other_rows_after_a_swipe_is_a_scrolled_view(tmp_path, monkeypatch):
    ex, _, feed, scrolled = scrolled_feed(tmp_path, monkeypatch)
    assert scrolled is not feed and scrolled.unscroll_to == feed.sid
    assert ex.record(ex.observe(), None, None, None) is scrolled


def test_a_relaunch_that_lands_on_home_with_its_list_reloaded_re_records_home(tmp_path, monkeypatch):
    """The committed run's s01 and its relaunched home s13 (j04, j11): home again, with j11's rows and capture, and
    the reloaded list is no region that moves on its own."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screens.update(first=capture("janitorai", "j04_tab1"), other=capture("janitorai", "j11_home_relaunched"))
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    home.dynamic = [stage.Rect(x=0, y=900, w=1080, h=300)]  # an ad the tour saw move, gone with the old capture
    phone.screen, ex.home = "other", home
    obs = ex.observe()
    assert ex.record(obs, None, None, None) is home and len(ex.states) == 1
    assert home.fp == obs.fp and {c.key for c in home.cands} == {c.key for c in obs.cands} and not home.dynamic


def test_back_to_a_scrolled_view_is_the_scrolled_view(tmp_path, monkeypatch):
    ex, phone, _, scrolled = scrolled_feed(tmp_path, monkeypatch)
    phone.screen = "chats"
    child = ex.current = ex.record(ex.observe(), scrolled, stage.Move("tap", ex.obs.cands[0]), ex.obs)
    phone.screen = "other"
    assert ex.record(ex.observe(), child, stage.Move("back"), None) is scrolled


def test_arrival_at_the_scrolled_view_is_not_claimed_from_the_top_of_the_feed(tmp_path, monkeypatch):
    ex, phone, _, scrolled = scrolled_feed(tmp_path, monkeypatch)
    phone.screen = "first"
    ex.observe()
    monkeypatch.setattr(ex, "judge", lambda *a, **k: stage.Landing(False, None, "", 0.0))
    assert not ex.arrived(scrolled)


def test_a_parent_row_that_moved_is_still_behind_the_sheet_not_its_own(tmp_path, monkeypatch):
    _, _, sheet, _ = tall_sheet(tmp_path, monkeypatch, moved="The Former Husband")
    assert sheet.kind == "sheet" and not [c for c in sheet.cands if c.tree_label.startswith("The Former Husband")]


def test_a_sheet_offers_only_its_own_controls_never_the_screen_behind_it(tmp_path, monkeypatch):
    ex, _, sheet, before = tall_sheet(tmp_path, monkeypatch)
    behind = {c.key for c in before.cands}
    assert "Start new chat" in {c.label for c in sheet.cands} and not behind & {c.key for c in sheet.cands}
    assert not any(c.kind == "EditText" for c in [*sheet.cands, *ex.surface()])
    assert {c.key for c in ex.surface()} == {c.key for c in sheet.cands}
    assert not [line for line in lines(ex) if line.from_state == sheet.sid and line.outcome == "denied"]


def sheet_first(clock):
    """A feed item opens a sheet in the feed's window (the feed stays listed behind it); the sheet's own New chat
    control opens the conversation."""
    phone = janitor_like(clock)
    feed = phone.screens["limited"]
    image = feed.image.copy()
    ImageDraw.Draw(image).rectangle((0, 1250, 1080, 2400), fill=(40, 40, 48))
    own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": "Your chats with this character",
            "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
           {"ref": "@s2", "type": "android.view.ViewGroup", "text": "New chat",
            "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
    phone.screens["sheet"] = Screen(feed.elements + own, image, PACKAGE)
    phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "sheet"
    phone.taps[("sheet", "New chat")] = "chat"
    return phone


def test_the_walk_follows_a_sheet_an_item_opens_to_the_conversation(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=sheet_first)
    trace = runlog.read_trace(ex.run_dir / "trace.jsonl")
    walk = [line for line in trace if "(core loop: the item's main action)" in line.note]
    assert any(s.kind == "sheet" for s in ex.states) and [line.note.split("'")[1] for line in walk] == ["New chat"]
    assert ex.core.name.startswith("open an item and send messages") and phone.sent


def reloaded_after_the_tour(tmp_path, monkeypatch, phone_factory=janitor_like):
    """The first launch after the tour shows the feed with every item replaced, as a feed that reloads does; the new
    first item opens the chat."""
    ex, phone = new_explorer(tmp_path, monkeypatch, phone_factory)
    paywall_pass, launch = ex.paywall_pass, phone.launch

    def reload():
        launch()
        phone.launch = launch
        feed = phone.screens["limited"]
        items = [c.tree_label for c in ob.feed_items(ob.controls(feed.elements, ex.device), ex.device)]
        today = {item: f"Today: {item}" for item in items}
        feed.elements[:] = [{**e, **{k: today[e[k]] for k in ("text", "label") if e.get(k) in today}}
                            for e in feed.elements]
        phone.taps[("limited", today[items[0]])] = "chat"

    def after_the_tour():
        phone.launch = reload
        paywall_pass()
    monkeypatch.setattr(ex, "paywall_pass", after_the_tour)
    return ex, phone


def test_the_walk_and_the_feed_pass_take_the_rows_home_was_re_recorded_with(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screens.update(first=capture("janitorai", "j04_tab1"), other=capture("janitorai", "j11_home_relaunched"))
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    feed = stage.CoreAction("feed", home, ob.feed_items(home.cands, ex.device, ex.tab_keys())[:2], "read items")
    assert ex.rows(feed) == feed.controls
    phone.screen, ex.home = "other", home
    ex.record(ex.observe(), None, None, None)
    now = ex.rows(feed)
    assert now and not {c.key for c in now} & {c.key for c in feed.controls} and all(c in home.cands for c in now)


def test_a_tab_feed_that_reloaded_after_the_tour_is_never_tapped_by_rows_it_did_not_record(tmp_path, monkeypatch):
    """Only a relaunch's landing is home by definition: a tab root whose list reloaded keeps its recorded rows, which
    are gone, so the walk and the feed pass tap none of the new ones."""
    for sub, pick in (("walk", None), ("feed", "open and read items")):
        ex, phone = reloaded_after_the_tour(tmp_path / sub, monkeypatch)
        if pick:
            monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick=pick))
        stage.explore_app(ex)
        taps = [t.note for t in runlog.read_trace(ex.run_dir / "trace.jsonl") if t.note.startswith("tap 'Today: ")]
        assert "reload" not in phone.launch.__name__ and not taps, (taps, ex.core_results)


# The red team's card over the reloaded feed (rt-pr29-90a0839): one clickable container (no words of its own, so
# controls() drops it as a layout), its title, its price line and its button. None of them sits on a row's tap point,
# yet the card's body lies over four rows.
CARD_BOX = (40, 1100, 1040, 2000)
CARD = [{"ref": "@c0", "type": "android.view.ViewGroup", "text": "", "clickable": True,
         "coordinates": {"x": 40, "y": 1100, "width": 1000, "height": 900}},
        {"ref": "@c1", "type": "android.widget.TextView", "text": "Janitor Plus",
         "coordinates": {"x": 90, "y": 1110, "width": 900, "height": 70}},
        {"ref": "@c2", "type": "android.widget.TextView", "text": "Unlimited chats for $4.99/month",
         "coordinates": {"x": 90, "y": 1400, "width": 900, "height": 70}},
        {"ref": "@c3", "type": "android.widget.Button", "text": "Subscribe",
         "coordinates": {"x": 330, "y": 1880, "width": 420, "height": 100}}]


def under_a_card(tmp_path, monkeypatch, core_pick=None):
    """The first launch after the tour: the feed reloaded other items and the card lies over the list. Records every
    tap point from then on."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    if core_pick:
        monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick=core_pick))
    paywall_pass, launch, tap = ex.paywall_pass, phone.launch, phone.tap
    points = []

    def reload():
        launch()
        phone.launch = launch
        feed = phone.screens["limited"]
        items = [c.tree_label for c in ob.feed_items(ob.controls(feed.elements, ex.device), ex.device)]
        today = {item: f"Today: {item}" for item in items}
        feed.elements[:] = [{**e, **{k: today[e[k]] for k in ("text", "label") if e.get(k) in today}}
                            for e in feed.elements] + CARD
        ImageDraw.Draw(feed.image).rectangle(CARD_BOX, fill=(52, 52, 60))
        phone.taps[("limited", today[items[0]])] = "chat"
        points.append("reload")

    def tapped(x, y):
        points.append((phone.screen, x, y))
        tap(x, y)

    def after_the_tour():
        phone.launch, phone.tap = reload, tapped
        paywall_pass()
    monkeypatch.setattr(ex, "paywall_pass", after_the_tour)
    return ex, points


def under(points):
    x0, y0, x1, y1 = CARD_BOX
    after = points[points.index("reload") + 1:] if "reload" in points else []
    return [p for p in after if p[0] == "limited" and x0 <= p[1] <= x1 and y0 <= p[2] <= y1]


def test_the_walk_never_taps_a_row_under_the_card_body(tmp_path, monkeypatch):
    ex, points = under_a_card(tmp_path, monkeypatch)
    stage.explore_app(ex)
    assert "reload" in points and not under(points), (under(points), [s.sid for s in ex.states])


def test_the_feed_pass_never_taps_a_row_under_the_card_body(tmp_path, monkeypatch):
    ex, points = under_a_card(tmp_path, monkeypatch, core_pick="open and read items")
    stage.explore_app(ex)
    assert "reload" in points and ex.core.kind == "feed" and not under(points), (under(points), ex.core_results)


def sign_in_sheet(clock):
    """A feed item opens a sheet in the feed's window that asks the guest to sign in."""
    phone = janitor_like(clock)
    feed = phone.screens["limited"]
    image = feed.image.copy()
    ImageDraw.Draw(image).rectangle((0, 1250, 1080, 2400), fill=(40, 40, 48))
    own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": "Sign in to read this character",
            "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
           {"ref": "@s2", "type": "android.widget.Button", "text": "Sign in with Google",
            "coordinates": {"x": 240, "y": 1950, "width": 600, "height": 120}},
           {"ref": "@s3", "type": "android.widget.Button", "text": "Not now",
            "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
    phone.screens["sheet"] = Screen(feed.elements + own, image, PACKAGE)
    phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "sheet"
    phone.taps[("limited", "Adrian & Eleanor || Your Dad & Stepmom's Hometown Fling")] = "sheet"
    return phone


def test_a_sign_in_sheet_in_the_feed_pass_stops_it(tmp_path, monkeypatch):
    ex, _ = new_explorer(tmp_path, monkeypatch, sign_in_sheet)
    monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick="open and read items"))
    stage.explore_app(ex)
    opened = [line for line in lines(ex)
              if line.loop_pass and line.to_state and ex.by_id[line.to_state].kind == "sheet"]
    assert ex.core.kind == "feed" and opened and opened[0].loop_stop == "sign-in wall"


def test_a_sheet_the_feed_pass_opens_is_its_result_not_a_stop(tmp_path, monkeypatch):
    ex, _ = new_explorer(tmp_path, monkeypatch, sheet_first)
    monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick="open and read items"))
    stage.explore_app(ex)
    opened = [line for line in lines(ex) if line.loop_pass == 1 and line.to_state
              and ex.by_id[line.to_state].kind == "sheet"]
    assert ex.core.kind == "feed" and opened and not opened[0].loop_stop and not ex.core_hit
    assert sum(r.startswith("pass ") for r in ex.core_results) == ex.core_reps


def test_a_stop_that_is_not_a_limit_leaves_the_limit_open(run):
    ex, _ = run
    assert not ex.core_hit and "limit" in ex.checklist()[1]
    assert stage.LIMIT_STOPS == ("counter", "input disabled", "paywall", "limit")


def test_a_sheet_over_the_chat_in_its_own_window_stops_the_loop(tmp_path, monkeypatch):
    def sheet_after_two(clock):
        phone = chatty(clock)
        chat = phone.screens["chat"]
        sheet = [{"ref": "@sheet", "type": "android.view.ViewGroup", "text": "You're out of free messages",
                  "coordinates": {"x": 0, "y": 1700, "width": 1080, "height": 637}},
                 {"ref": "@more", "type": "android.widget.Button", "text": "Get more messages",
                  "coordinates": {"x": 100, "y": 2150, "width": 880, "height": 120}}]
        phone.screens["chat_sheet"] = type(chat)(chat.elements + sheet, chat.image, chat.package)
        phone.after_sends = {2: "chat_sheet"}
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=sheet_after_two, budget="deep")
    stops = [(line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert phone.sent == 2 and stops == [(2, "limit")] and "limit" in ex.checklist()[0]


def test_jev_sees_what_repeating_each_option_means_and_the_conversation_itself(tmp_path, monkeypatch):
    ex, _ = new_explorer(tmp_path, monkeypatch, detail_first)
    asked = []

    def recording(state, instructions, labels, backend):
        if instructions == stage.CORE_QUESTION:
            asked.append((state, labels))
        return fake_jev(state, instructions, labels, backend)
    monkeypatch.setattr(decide, "ask_choice", recording)
    stage.explore_app(ex)
    (state, labels), = asked
    inside = next(label for label in labels if label.startswith("open an item and send messages in its conversation"))
    assert "'Teacher'" in inside and "text box + send inside the item" in inside
    assert any(label.startswith("open and read items from the list") for label in labels)
    assert state.startswith(f"screen {ex.core.state.sid} is a conversation titled 'Teacher'; its last messages:")
    assert "Get a ping when Luzia replies" in state


def test_the_walk_scrolls_to_a_main_action_below_the_fold_and_takes_a_long_one_line_label(tmp_path, monkeypatch):
    from PIL import Image
    from tests.fake_device import Screen
    back = {"ref": "@back", "type": "android.widget.Button", "text": "", "identifier": "x:id/back",
            "coordinates": {"x": 42, "y": 168, "width": 84, "height": 84}}
    bio = {"ref": "@bio", "type": "android.widget.TextView", "text": BIO,
           "coordinates": {"x": 42, "y": 700, "width": 996, "height": 120}}
    long_chat = {"ref": "@go", "type": "android.widget.TextView", "text": "Chat with Vac the Kinectic",
                 "coordinates": {"x": 399, "y": 2200, "width": 400, "height": 41}}

    def below_the_fold(clock):
        phone = janitor_like(clock)
        phone.screens["detail"] = Screen([back, bio], Image.new("RGB", (1080, 2400), (60, 20, 80)), PACKAGE)
        phone.screens["detail_end"] = Screen([back, bio, long_chat], Image.new("RGB", (1080, 2400), (90, 40, 20)),
                                             PACKAGE)
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "detail"
        phone.swipes["detail"] = "detail_end"
        phone.taps[("detail_end", "Chat with Vac the Kinectic")] = "chat"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=below_the_fold)
    assert ex.core.kind == "chat" and phone.sent == ex.core_reps
    assert ("tap", "detail_end", "Chat with Vac the Kinectic") in phone.log
    assert not any(entry[:3] == ("tap", "detail", BIO) for entry in phone.log)


def test_a_conversation_inside_a_feed_item_comes_first_and_the_tour_chat_stays_a_choice(tmp_path, monkeypatch):
    ex, _ = new_explorer(tmp_path, monkeypatch, janitor_like)
    asked = []

    def recording(state, instructions, labels, backend):
        if instructions == stage.CORE_QUESTION:
            asked.append(labels)
        return fake_jev(state, instructions, labels, backend)
    monkeypatch.setattr(decide, "ask_choice", recording)
    stage.explore_app(ex)
    labels, = asked
    assert labels[0].startswith("open an item and send messages in its conversation")
    assert any(label.startswith("send messages in a conversation") for label in labels)


def test_a_reply_still_being_written_neither_ends_the_chat_nor_leaves_it(tmp_path, monkeypatch):
    def slow_replies(clock):
        phone = chatty(clock)
        phone.generating = 30.0
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=slow_replies, budget="deep")
    assert phone.sent == ex.core_reps == 8 and not ex.core_hit
    assert not any("could not get back" in r for r in ex.core_results)
    assert not any(entry[:1] == ("tap",) and entry[2] == "Cancel" for entry in phone.log)


def test_a_walk_into_an_item_that_runs_out_of_relaunches_still_lets_jev_choose(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, chatty, budget="deep")

    def stopped(feed):
        raise stage.Stop("relaunch cap")
    monkeypatch.setattr(ex, "walk_to_input", stopped)
    stage.explore_app(ex)
    assert ex.core.kind == "chat" and phone.sent == 8
    assert not any("core_loop stopped" in r for r in ex.core_results)


def test_the_new_reply_never_names_what_stopped_the_chat(tmp_path, monkeypatch):
    def voice_sheet_after_two(clock):
        phone = chatty(clock)
        phone.replies["chat"] = ["Hi! Ask me anything.", "I've hit my limits before, so I pace myself."]
        chat = phone.screens["chat"]
        sheet = [{"ref": "@sheet", "type": "android.view.ViewGroup", "text": "Choose a voice for this character",
                  "coordinates": {"x": 0, "y": 1700, "width": 1080, "height": 637}},
                 {"ref": "@voice", "type": "android.widget.Button", "text": "Voice one",
                  "coordinates": {"x": 100, "y": 2150, "width": 880, "height": 120}}]
        phone.screens["chat_sheet"] = type(chat)(chat.elements + sheet, chat.image, chat.package)
        phone.replies["chat_sheet"] = phone.replies["chat"]
        phone.chats["chat_sheet"] = phone.chats.setdefault("chat", [])
        phone.after_sends = {2: "chat_sheet"}
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=voice_sheet_after_two, budget="deep")
    stops = [(line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert phone.sent == 2 and stops == [(2, "input gone")] and "limit" not in ex.checklist()[0]


def test_the_walk_tries_the_next_item_when_the_first_has_no_main_action(tmp_path, monkeypatch):
    from PIL import Image
    from tests.fake_device import Screen
    back = {"ref": "@back", "type": "android.widget.Button", "text": "", "identifier": "x:id/back",
            "coordinates": {"x": 42, "y": 168, "width": 84, "height": 84}}
    bio = {"ref": "@bio", "type": "android.widget.TextView", "text": "She lives down the hall and needs a hero",
           "coordinates": {"x": 42, "y": 700, "width": 996, "height": 120}}

    def bare_first(clock):
        phone = detail_first(clock)
        phone.screens["bare"] = Screen([back, bio], Image.new("RGB", (1080, 2400), (60, 20, 80)), PACKAGE)
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "bare"
        phone.taps[("limited", "Adrian & Eleanor || Your Dad & Stepmom")] = "detail"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=bare_first)
    assert ex.core.kind == "chat" and phone.sent
    assert ("back", "bare") in phone.log and ("tap", "detail", "New chat") in phone.log


def test_a_chosen_chat_that_cant_be_reached_gives_way_to_jevs_next_chat(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, detail_first, budget="deep")
    monkeypatch.setattr(decide, "ask_choice",
                        functools.partial(fake_jev, core_pick="send messages in a conversation and read"))
    at_core, tour_chat = ex.at_core, []

    def the_tour_chat_is_gone(n):
        if ex.core.name.startswith("send messages in"):
            tour_chat.append(ex.core.state.sid)
            return False
        return at_core(n)
    monkeypatch.setattr(ex, "at_core", the_tour_chat_is_gone)
    stage.explore_app(ex)
    assert tour_chat and ex.core.name.startswith("open an item and send messages") and phone.sent == ex.core_reps
    assert any(r.startswith(f"could not get to {tour_chat[0]}") for r in ex.core_results)


def test_the_walk_scrolls_a_long_item_page_to_its_main_action_at_the_end(tmp_path, monkeypatch):
    from PIL import Image
    from tests.fake_device import Screen
    back = {"ref": "@back", "type": "android.widget.Button", "text": "", "identifier": "x:id/back",
            "coordinates": {"x": 42, "y": 168, "width": 84, "height": 84}}
    go = {"ref": "@go", "type": "android.view.ViewGroup", "text": "Chat with Avarus",
          "coordinates": {"x": 88, "y": 2198, "width": 904, "height": 102}}

    def long_page(clock):
        phone = janitor_like(clock)
        pages = [f"page{n}" for n in range(6)]
        for n, name in enumerate(pages):
            text = {"ref": "@p", "type": "android.widget.TextView", "text": f"Paragraph {n} of a long description",
                    "coordinates": {"x": 42, "y": 700, "width": 996, "height": 120}}
            shade = (40 * n % 255, 20, 200 - 30 * n)
            phone.screens[name] = Screen([back, text] + ([go] if n == 5 else []),
                                         Image.new("RGB", (1080, 2400), shade), PACKAGE)
        phone.swipes.update(zip(pages, pages[1:]))
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "page0"
        phone.taps[("page5", "Chat with Avarus")] = "chat"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=long_page)
    assert ex.core.kind == "chat" and phone.sent
    assert ("tap", "page5", "Chat with Avarus") in phone.log


def test_a_stop_control_right_of_the_box_is_never_taken_for_send_while_a_reply_is_written(tmp_path, monkeypatch):
    def stop_where_send_goes(clock):
        phone = chatty(clock)
        phone.generating, phone.busy_label = 30.0, "Stop"
        for e in phone.screens["chat"].elements:
            if (e.get("identifier") or "").endswith("sendButton"):
                e["coordinates"] = {**e["coordinates"], "x": 1014, "width": 66}
            if "Audio Button" in (e.get("label") or ""):
                e["coordinates"] = {**e["coordinates"], "x": 1020}
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=stop_where_send_goes, budget="deep")
    assert phone.sent == ex.core_reps == 8 and not ex.core_hit
    assert not any(entry[:1] == ("tap",) and entry[2] == "Stop" for entry in phone.log)


def test_a_send_that_opens_another_screen_with_a_text_box_is_not_taken_for_the_chat(tmp_path, monkeypatch):
    from PIL import Image
    from tests.fake_device import Screen

    def other_box_after_two(clock):
        phone = chatty(clock)
        chat = phone.screens["chat"]
        elements = [{**e, "text": "Report a problem"} if e.get("text") == "Teacher" else e for e in chat.elements]
        phone.screens["other"] = Screen(elements, Image.new("RGB", (1080, 2400), (200, 230, 200)), PACKAGE)
        phone.after_sends = {2: "other"}
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=other_box_after_two, budget="deep")
    assert not any(entry[0] == "type" and entry[1] == "other" for entry in phone.log)


def test_the_walk_stops_scrolling_a_page_that_no_longer_moves(tmp_path, monkeypatch):
    from PIL import Image
    from tests.fake_device import Screen
    back = {"ref": "@back", "type": "android.widget.Button", "text": "", "identifier": "x:id/back",
            "coordinates": {"x": 42, "y": 168, "width": 84, "height": 84}}
    bio = {"ref": "@bio", "type": "android.widget.TextView", "text": "A short page with nothing to start",
           "coordinates": {"x": 42, "y": 700, "width": 996, "height": 120}}

    def short_page(clock):
        phone = janitor_like(clock)
        phone.screens["short"] = Screen([back, bio], Image.new("RGB", (1080, 2400), (60, 20, 80)), PACKAGE)
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "short"
        return phone
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=short_page)
    assert sum(entry == ("swipe", "short", "up") for entry in phone.log) == 2


def test_the_model_ends_a_screen_only_when_the_code_has_nothing_left(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screen = "limited"
    s = ex.current = ex.record(ex.observe(), None, None, None)
    assert ex.options(s)
    ex.model_done(s, "the model said done")
    assert not s.done and ex.counts["model done refused while options were left"] == 1
    s.noop_run = stage.NOOPS_BEFORE_SONNET
    ex.model_done(s, "the model said done")
    assert s.done == "the model said done"
    s.done, s.noop_run, s.taps = "", 0, stage.TAPS_PER_STATE
    ex.model_done(s, "the model went back")
    assert s.done == "the model went back"


def test_the_exhibit_says_why_each_screen_ended_and_what_it_left_untried(run):
    ex, _ = run
    exhibit = (ex.run_dir / "exhibits" / "01-explore.md").read_text()
    rows = [line.split(" | ") for line in exhibit.splitlines() if line.startswith("| s")]
    assert "| id | kind | over | depth | untried | ended by | first words |" in exhibit
    screens = [row for row in rows if row[1] in ("screen", "modal", "sheet")]
    assert screens and all(row[4].isdigit() and row[5] for row in screens)


def test_a_main_screen_button_in_the_apps_own_words_can_be_the_core_action(tmp_path, monkeypatch):
    def lessons(clock):
        phone = janitor_like(clock)
        limited = phone.screens["limited"]
        start = {"ref": "@lesson", "type": "android.widget.Button", "text": "Start lesson",
                 "coordinates": {"x": 140, "y": 1700, "width": 800, "height": 140}}
        phone.screens["limited"] = Screen([*limited.elements, start], limited.image, limited.package)
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, lessons, budget="deep")
    monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick="Start lesson"))
    stage.explore_app(ex)
    assert ex.core.kind == "action" and ex.core.controls[0].label == "Start lesson"
    assert ("tap", "limited", "Start lesson") in phone.log


def test_the_core_question_stays_within_one_choice_of_options(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like, budget="deep")
    asked, choose = [], decide.choose

    def spying(trace_path, stage_name, step, state, instructions, labels, **kwargs):
        if step == "core":
            asked.append(labels)
        return choose(trace_path, stage_name, step, state, instructions, labels, **kwargs)
    monkeypatch.setattr(decide, "choose", spying)
    stage.explore_app(ex)
    assert asked and all(len(labels) <= decide.CHUNK for labels in asked) and stage.NO_CORE in asked[0]


def switch_filter(label: str, shipped_on: bool):
    """The janitor-like app with its filter chip swapped for a settings-style switch row that a tap flips; a launch
    shows it as the app ships it."""
    def factory(clock):
        phone = janitor_like(clock)
        base = phone.screens["root"]

        def variant(on):
            row = {"ref": "@sw", "type": "android.widget.Switch", "text": label, "label": "",
                   "coordinates": {"x": 42, "y": 1300, "width": 996, "height": 126}, **({"checked": True} if on else {})}
            image = base.image.copy()
            ImageDraw.Draw(image).ellipse((900, 1320, 986, 1406), fill=(40, 160, 80) if on else (120, 120, 120))
            return Screen([e for e in base.elements if "Limited Only" not in (e.get("text"), e.get("label"))] + [row],
                          image, base.package)
        phone.screens["root"], phone.screens["root_flipped"] = variant(shipped_on), variant(not shipped_on)
        phone.taps[("root", label)], phone.taps[("root_flipped", label)] = "root_flipped", "root"
        return phone
    return factory


@pytest.mark.parametrize("label, shipped_on, restrictive", [("Hide NSFW", True, True),
                                                             ("Show NSFW content", False, False),
                                                             ("Hide NSFW", False, True)])
def test_a_switch_filter_is_kept_in_its_restrictive_state_never_flipped_blind(tmp_path, monkeypatch, label,
                                                                              shipped_on, restrictive):
    monkeypatch.setattr(fake_device, "RESTRICTIVE", re.compile(r"\bnsfw\b", re.IGNORECASE))
    ex, phone = new_explorer(tmp_path, monkeypatch, switch_filter(label, shipped_on))
    phone.screen = "root"
    ex.current = ex.record(ex.observe(), None, None, None)

    def on() -> bool:
        return bool(next(e for e in phone.current_elements() if e.get("text") == label).get("checked"))
    ex.filter_taps = ex.find_filter()
    assert [t.label for t in ex.filter_taps] == [label] and ex.filter_on is restrictive and on() is restrictive
    for relaunched in (False, True, False):
        if relaunched:
            phone.screen = "root"
        ex.observe()
        ex.apply_filter(first=False)
        assert on() is restrictive
    assert [ok for _, ok, _ in ex.filter_checks] == [True, True, True]
    assert sum(entry == ("tap", "root", label) or entry == ("tap", "root_flipped", label) for entry in phone.log) \
        == (0 if shipped_on is restrictive else 2)


def playing_ad(loads: bool):
    """The janitor-like app with an ad that repaints every frame and a feed item on the root; its tap loads a page
    or does nothing."""
    def factory(clock):
        phone = janitor_like(clock)
        item = {"ref": "@item", "type": "android.widget.TextView", "text": "Top story of the day",
                "coordinates": {"x": 42, "y": 1850, "width": 996, "height": 120}}
        phone.screens["root"].elements.append(item)
        if loads:
            phone.taps[("root", "Top story of the day")] = "chats"
        image, frames = phone.image, iter(range(10**6))

        def frame():
            out = image()
            ImageDraw.Draw(out).rectangle((100, 1500, 400, 1700), fill=(40 * next(frames) % 255, 90, 200))
            return out
        phone.image = frame
        return phone
    return factory


@pytest.mark.parametrize("loads, completed", [(False, 0), (True, 3)])
def test_a_feed_pass_counts_only_a_result_outside_what_moves_on_its_own(tmp_path, monkeypatch, loads, completed):
    ex, phone = new_explorer(tmp_path, monkeypatch, playing_ad(loads), budget="transfer")
    ex.touring = False
    phone.screen = "root"
    ex.current = ex.record(ex.observe(), None, None, None)
    for _ in range(3):  # the tour came back to the screen, as it does to a tab root, and saw the ad move
        ex.revisit(ex.current, ex.observe())
    assert ex.current.dynamic
    item = next(c for c in ex.current.cands if c.label == "Top story of the day")
    monkeypatch.setattr(ex, "choose_core", lambda: [stage.CoreAction("feed", ex.current, [item], "open an item")])
    monkeypatch.setattr(ex, "at_core", lambda n: True)
    ex.core_loop()
    attempted = sum(r.startswith("pass ") for r in ex.core_results)
    assert (attempted, ex.core_completed) == (ex.core_reps, completed)
    assert ("met no limit" in stage.loop_end(ex, attempted)) is loads


@pytest.mark.parametrize("how, seen", [("stalled", False), ("progressing", True)])
def test_a_result_the_model_calls_stalled_is_never_a_completed_pass(tmp_path, monkeypatch, how, seen):
    ex, phone = new_explorer(tmp_path, monkeypatch, playing_ad(True))
    phone.screen = "root"
    ex.current = ex.record(ex.observe(), None, None, None)
    monkeypatch.setattr(ex, "idle_again", lambda *args: False)  # never settles by itself, so the model ends it
    monkeypatch.setattr(ex, "progress", lambda *args: how)
    before = ob.texts(ex.obs.elements, ex.device)
    phone.screen = "chats"  # the tap loaded a page
    ex.watch(before, "load")
    assert ex.settles[-1][1] == how and ex.last_seen is seen


def sheet_over_feed(own, header=False):
    """A feed item opens a sheet in the feed's window with these own controls; with header, the guest feed shows its
    own "Log in" button up in its header."""
    def factory(clock):
        phone = janitor_like(clock)
        feed = phone.screens["limited"]
        if header:
            feed.elements.append({"ref": "@h1", "type": "android.widget.Button", "text": "Log in",
                                  "coordinates": {"x": 700, "y": 160, "width": 200, "height": 90}})
        image = feed.image.copy()
        ImageDraw.Draw(image).rectangle((0, 1250, 1080, 2400), fill=(40, 40, 48))
        phone.screens["sheet"] = Screen(feed.elements + own, image, PACKAGE)
        phone.taps[("limited", "JJK - GOJO’S RELATIVE")] = "sheet"
        phone.taps[("limited", "Adrian & Eleanor || Your Dad & Stepmom's Hometown Fling")] = "sheet"
        return phone
    return factory


def feed_pass_sheets(tmp_path, monkeypatch, factory):
    ex, _ = new_explorer(tmp_path, monkeypatch, factory)
    monkeypatch.setattr(decide, "ask_choice", functools.partial(fake_jev, core_pick="open and read items"))
    stage.explore_app(ex)
    opened = [line for line in lines(ex)
              if line.loop_pass and line.to_state and ex.by_id[line.to_state].kind == "sheet"]
    assert ex.core.kind == "feed" and opened, ex.core_results
    return ex, opened


def test_a_sign_in_sheet_whose_log_in_matches_the_guest_headers_stops_the_feed_pass(tmp_path, monkeypatch):
    own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": "Want to chat with this character?",
            "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
           {"ref": "@s2", "type": "android.widget.Button", "text": "Log in",
            "coordinates": {"x": 240, "y": 1950, "width": 600, "height": 120}},
           {"ref": "@s3", "type": "android.widget.Button", "text": "Not now",
            "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
    ex, opened = feed_pass_sheets(tmp_path, monkeypatch, sheet_over_feed(own, header=True))
    assert opened[0].loop_stop == "sign-in wall", ([c.label for c in ex.by_id[opened[0].to_state].cands],
                                                   ex.core_results)


def test_a_chat_list_sheet_whose_greeting_mentions_money_is_the_feed_pass_result(tmp_path, monkeypatch):
    """The committed JanitorAI sheets s19/s20 list chats labelled with the character's opening message."""
    own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": "Kang Jun-Seo (Idol x Idol), 2 chats",
            "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
           {"ref": "@s2", "type": "android.view.ViewGroup",
            "text": "Kang Jun-Seo (Idol x Idol), Mon, The livestream starts out casual. He grins at the camera: "
                    "'Don't forget to subscribe, and check out the merch!'",
            "coordinates": {"x": 0, "y": 1400, "width": 1080, "height": 300}},
           {"ref": "@s3", "type": "android.view.ViewGroup", "text": "Start new chat",
            "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
    ex, opened = feed_pass_sheets(tmp_path, monkeypatch, sheet_over_feed(own))
    assert not opened[0].loop_stop and not ex.core_hit, (opened[0].loop_stop, ex.core_hit)


# rt-pr29-b938d29: a card over home after a relaunch, every row under it, its body a wordless box.
PROMO = [{"ref": "@promo", "type": "android.view.ViewGroup", "text": "",
          "coordinates": {"x": 200, "y": 1100, "width": 700, "height": 950}},
         {"ref": "@title", "type": "android.widget.TextView", "text": "What's new",
          "coordinates": {"x": 250, "y": 1120, "width": 600, "height": 50}},
         {"ref": "@more", "type": "android.widget.TextView", "text": "Characters now remember more",
          "coordinates": {"x": 250, "y": 1410, "width": 600, "height": 50}},
         {"ref": "@ok", "type": "android.widget.Button", "text": "Got it",
          "coordinates": {"x": 320, "y": 1950, "width": 400, "height": 60}}]


def test_a_relaunch_that_lands_on_a_money_card_over_home_keeps_home(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    base = capture("janitorai", "j11_home_relaunched")
    money = [dict(e, text={"What's new": "Plus", "Got it": "Subscribe"}.get(e["text"], e["text"])) for e in PROMO]
    image = base.image.copy()
    ImageDraw.Draw(image).rectangle((200, 1100, 900, 2050), fill=(52, 52, 60))
    phone.screens.update(first=base, covered=Screen(base.elements + money, image, base.package))
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    kept = [c.key for c in home.cands]
    phone.screen, ex.home = "covered", home
    assert ex.record(ex.observe(), None, None, None) is not home and [c.key for c in home.cands] == kept


def test_a_long_control_label_that_starts_with_a_sign_in_or_money_command_is_a_wall():
    def control(label):
        return ob.Candidate(label, "ViewGroup", stage.Rect(x=0, y=0, w=500, h=100), "@c", label)
    assert ob.walled([control("Log in to continue chatting")]) == "account"
    assert ob.walled([control("Subscribe now to unlock conversations")]) == "money"
    greeting = "Kang Jun-Seo, Mon, He grins: 'Don't forget to subscribe, and check out the merch!'"
    assert not ob.walled([control(greeting)])


def test_a_relaunch_that_lands_on_a_sign_in_wall_keeps_home(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screen = "limited"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    ex.root = ex.launch_root = home
    kept = [c.key for c in home.cands]
    phone.screens["wall"] = Screen([
        {"ref": "@title", "type": "android.widget.TextView", "text": "Sign in to continue",
         "coordinates": {"x": 0, "y": 200, "width": 1080, "height": 100}},
        {"ref": "@sign", "type": "android.widget.Button", "text": "Sign in",
         "coordinates": {"x": 180, "y": 950, "width": 720, "height": 100}},
        {"ref": "@more", "type": "android.widget.Button", "text": "Learn more",
         "coordinates": {"x": 0, "y": 2200, "width": 1080, "height": 100}}],
        Image.new("RGB", (1080, 2400), (15, 15, 15)), PACKAGE)
    phone.start = "wall"
    ex.relaunch()
    assert [c.key for c in home.cands] == kept and any(s.kind == "screen" and s is not home for s in ex.states)


def test_a_relaunch_that_restores_a_changed_deeper_screen_goes_back_and_keeps_home(tmp_path, monkeypatch):
    """Home has the tab bar the first launch recorded; the restored chat changed since the tour and shows none."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screen = "limited"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    ex.root = ex.launch_root = home
    ex.tabs = ob.tab_bar(home.cands, ex.device)
    kept = [c.key for c in home.cands]
    phone.screen = "chat"
    detail = ex.current = ex.record(ex.observe(), home, stage.Move("tap", home.cands[0]), ex.obs)
    image = phone.screens["chat"].image.copy()
    ImageDraw.Draw(image).rectangle((40, 450, 1040, 2100), fill=(160, 80, 80))
    phone.screens["chat"].image, phone.start = image, "chat"
    ex.relaunch()
    assert ex.tabs and detail is not home and [c.key for c in home.cands] == kept


def test_a_guest_home_with_a_log_in_header_is_still_home_after_its_feed_reloads(tmp_path, monkeypatch):
    """rt-pr29-7ef1473: home's own "Log in" is no wall; a wall home didn't show still is (the sign-in wall test)."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    login = {"ref": "@login", "type": "android.widget.Button", "text": "Log in",
             "coordinates": {"x": 700, "y": 160, "width": 200, "height": 90}}
    first, other = capture("janitorai", "j04_tab1"), capture("janitorai", "j11_home_relaunched")
    for screen in (first, other):
        screen.elements.append(login)
        ImageDraw.Draw(screen.image).rectangle((700, 160, 900, 250), fill=(70, 70, 70))
    phone.screens.update(first=first, other=other)
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    ex.root = ex.launch_root = home
    ex.tabs = ob.tab_bar(home.cands, ex.device)
    assert ex.tabs and ob.walled(home.cands) == "account"
    phone.start = "other"
    ex.relaunch()
    assert ex.current is home and len(ex.states) == 1, (ex.current.sid, len(ex.states))


def test_a_relaunch_records_a_new_sign_in_wall_even_when_its_button_matches_the_guest_header(tmp_path, monkeypatch):
    """rt-pr29-00b167c: home's own "Log in" sits in its header; the wall's "Log in" is elsewhere, so it is new."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    base = capture("janitorai", "j04_tab1")
    base.elements.append(
        {"ref": "@header-login", "type": "android.widget.Button", "text": "Log in",
         "coordinates": {"x": 700, "y": 160, "width": 200, "height": 90}})
    ImageDraw.Draw(base.image).rectangle((700, 160, 900, 250), fill=(70, 70, 70))
    phone.screens["first"] = base
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    ex.root = ex.launch_root = home
    ex.tabs = ob.tab_bar(home.cands, ex.device)
    assert len(ex.tabs) == 5 and ob.walled(home.cands) == "account"
    original = {c.key for c in home.cands}

    tab_refs = {c.ref for c in ex.tabs}
    tabs = [e for e in base.elements if e["ref"] in tab_refs]
    wall = [
        {"ref": "@welcome", "type": "android.widget.TextView", "text": "Welcome back",
         "coordinates": {"x": 0, "y": 350, "width": 1080, "height": 120}},
        {"ref": "@message", "type": "android.widget.TextView", "text": "Continue to your characters",
         "coordinates": {"x": 80, "y": 800, "width": 920, "height": 100}},
        {"ref": "@wall-login", "type": "android.widget.Button", "text": "Log in",
         "coordinates": {"x": 240, "y": 1200, "width": 600, "height": 120}},
    ] + tabs
    image = base.image.copy()
    ImageDraw.Draw(image).rectangle((0, 300, 1080, 2100), fill=(25, 25, 30))
    phone.screens["wall"] = Screen(wall, image, base.package)
    phone.start = "wall"
    phone.screen = "wall"
    landed = ex.observe()
    assert not ob.same_state(home.fp, landed.fp)
    assert ob.walled(landed.cands) == "account"
    assert not ex.blocked(landed)
    assert all(ob.find(landed.cands, t) for t in ex.tabs)

    ex.relaunch()
    assert {c.key for c in home.cands} == original and ex.current is not home, (home.sid, ex.current.sid)


def test_the_fake_phone_encodes_each_picture_once_and_writes_what_a_plain_save_would(tmp_path, monkeypatch):
    """An explore captures the same few screens ~150 times; encoding every full-size capture again was most of what
    an offline explore cost."""
    square, tall = (Screen([], Image.new("RGB", size, "red"), PACKAGE) for size in ((2, 2), (1, 4)))  # same bytes
    phone = FakePhone(screens={"home": capture("janitorai", "j02_home"), "tab": capture("janitorai", "j04_tab1"),
                               "square": square, "tall": tall}, start="home", taps={}, clock=Clock())
    encodes, save = [], Image.Image.save
    monkeypatch.setattr(Image.Image, "save", lambda image, *args, **kwargs: encodes.append(image.size)
                        or save(image, *args, **kwargs))
    homes = [phone.screenshot(tmp_path / f"home-{n}.png") for n in range(3)]
    phone.go("tab")
    tab = phone.screenshot(tmp_path / "tab.png")
    assert len(encodes) == 2
    for shape in ("square", "tall"):
        phone.go(shape)
        assert Image.open(phone.screenshot(tmp_path / f"{shape}.png")).size == phone.screens[shape].image.size
    assert len(encodes) == 4
    save(phone.screens["tab"].image, tmp_path / "plain.png")
    assert {path.read_bytes() for path in homes} != {tab.read_bytes()} == {(tmp_path / "plain.png").read_bytes()}


def home_from(ex, phone, name="first"):
    """The explorer's first launch recorded on the named screen: home, the launch root and its tab bar."""
    phone.screen = name
    home = ex.current = ex.record(ex.observe(), None, None, None)
    ex.root = ex.launch_root = home
    ex.tabs = ob.tab_bar(home.cands, ex.device)
    return home


def test_aol_keeps_its_tab_bar(tmp_path, monkeypatch):
    """Fable E1: the bottom bar's container starts below the Inbox tab's box, and once dropped the whole bar."""
    def aol(clock):
        return FakePhone(screens={"home": capture("aol", "aol-home", package=PACKAGE)}, start="home", taps={},
                         clock=clock)
    ex, _ = new_explorer(tmp_path, monkeypatch, aol)
    ex.relaunch(first=True)
    assert [c.label for c in ex.tabs] == ["Inbox", "Home"] and ex.shows_tabs(ex.root)


def test_no_tap_reaches_a_sign_in_button_a_card_draws_over_a_row(tmp_path, monkeypatch):
    """Fable E2: a relaunch lands on the reloaded feed under a card listed after the rows; its body holds a whole row
    and its "Continue with Google" lies inside the row's box."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    first, base = capture("janitorai", "j04_tab1"), capture("janitorai", "j11_home_relaunched")
    card = [{"ref": "@card", "type": "android.view.ViewGroup", "text": "",
             "coordinates": {"x": 40, "y": 1300, "width": 1000, "height": 800}},
            {"ref": "@title", "type": "android.widget.TextView", "text": "Keep your chats",
             "coordinates": {"x": 90, "y": 1320, "width": 900, "height": 70}},
            {"ref": "@google", "type": "android.widget.Button", "text": "Continue with Google",
             "coordinates": {"x": 80, "y": 1640, "width": 410, "height": 120}}]
    image = base.image.copy()
    ImageDraw.Draw(image).rectangle((40, 1300, 1040, 2100), fill=(52, 52, 60))
    phone.screens.update(first=first, covered=Screen(base.elements + card, image, base.package),
                         google=Screen([], Image.new("RGB", (1080, 2400)), "com.google.android.gms"))
    phone.taps[("covered", "Continue with Google")] = "google"
    home = home_from(ex, phone)
    feed = stage.CoreAction("feed", home, ob.feed_items(home.cands, ex.device, ex.tab_keys()), "read items")
    phone.start = "covered"
    ex.relaunch()
    assert ex.current is not home
    ex.touring = False
    ex.walk_into(feed, 1)
    assert phone.foreground() != "com.google.android.gms"


def test_a_denied_word_drawn_over_a_rows_tap_point_refuses_the_tap(tmp_path, monkeypatch):
    """The row was recorded with nothing over it; a sign-in button listed after it now sits at its tap point and draws
    nothing the crop check sees. The tap is refused and logged as denied, and never counted as executed."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screen = "limited"
    feed = ex.current = ex.record(ex.observe(), None, None, None)
    row = ob.feed_items(feed.cands, ex.device)[0]
    x, y = row.point
    phone.screens["limited"].elements.append({"ref": "@sign", "type": "android.widget.Button", "text": "Sign in",
                                              "coordinates": {"x": x - 100, "y": y - 30, "width": 200, "height": 60}})
    ex.observe()
    taps = len([e for e in phone.log if e[0] == "tap"])
    assert ex.act(stage.Move("tap", row)) is feed and len([e for e in phone.log if e[0] == "tap"]) == taps
    last = lines(ex)[-1]
    assert last.outcome == "denied" and "sign in" in last.change_summary and ex.denied_executed == 0


def tabs_shown_under_paint(tmp_path, monkeypatch, refresh):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    first, other = capture("janitorai", "j04_tab1"), capture("janitorai", "j11_home_relaunched")
    phone.screens.update(first=first, other=other)
    home = home_from(ex, phone, "first" if refresh else "other")
    if refresh:
        phone.start = "other"
        ex.relaunch()
        assert ex.current is home and len(ex.states) == 1
    image = other.image.copy()
    ImageDraw.Draw(image).rectangle((0, 2000, 1080, 2337), fill=(200, 40, 40))  # something opaque over the tab bar
    phone.screens["painted"] = Screen(other.elements, image, other.package)  # the tabs stay listed behind it
    phone.screen = "painted"
    obs = ex.observe()
    return [ex.shows(tab, ob.find(obs.cands, tab), obs) for tab in ex.tabs]


def test_a_tab_painted_over_is_not_shown_after_a_relaunch_re_recorded_home(tmp_path, monkeypatch):
    """Fable E3: the tabs, the filter taps and home's moves point at home's new capture, so none is waved through."""
    assert not any(tabs_shown_under_paint(tmp_path / "never", monkeypatch, refresh=False))
    assert not any(tabs_shown_under_paint(tmp_path / "after", monkeypatch, refresh=True))


def test_a_relaunch_that_lands_on_another_tab_records_it_apart_from_home(tmp_path, monkeypatch):
    """Fable E4: the profile tab shows the same tab bar, not home's top chrome."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screens["first"] = capture("janitorai", "j04_tab1")
    home = home_from(ex, phone)
    kept = [c.key for c in home.cands]
    phone.start = "profile"
    ex.relaunch()
    assert ex.current is not home and [c.key for c in home.cands] == kept


def test_a_relaunch_onto_home_reloaded_in_the_same_shapes_refreshes_its_rows_and_the_feed_pass_taps_a_new_one(
        tmp_path, monkeypatch):
    """Sol 1: the same cards with new titles keep home's fingerprint (pixels and layout); home is re-recorded with
    the new rows rather than kept with rows the screen no longer has."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    first = capture("janitorai", "j04_tab1")
    phone.screens["first"] = first
    home = home_from(ex, phone)
    feed = stage.CoreAction("feed", home, ob.feed_items(home.cands, ex.device, ex.tab_keys()), "read items")
    today = {c.tree_label: f"Today: {c.tree_label}" for c in feed.controls}
    phone.screens["reloaded"] = Screen([{**e, **{k: today[e[k]] for k in ("text", "label") if e.get(k) in today}}
                                        for e in first.elements], first.image, first.package)
    phone.taps.update({("reloaded", title): "chats" for title in today.values()})
    phone.start = "reloaded"
    assert ob.same_state(home.fp, ob.fingerprint(PACKAGE, phone.screens["reloaded"].elements, first.image, ex.device))
    ex.relaunch()
    assert ex.current is home and len(ex.states) == 1 and set(today.values()) <= {c.tree_label for c in home.cands}
    ex.touring = False
    monkeypatch.setattr(ex, "choose_core", lambda: [feed])
    monkeypatch.setattr(ex, "at_core", lambda n: True)
    ex.core_loop()
    taps = [t.note for t in runlog.read_trace(ex.run_dir / "trace.jsonl") if t.note.startswith("tap 'Today: ")]
    assert taps and all("(core loop: feed)" in note for note in taps), ex.core_results


def test_an_arrival_that_is_no_relaunch_keeps_the_recorded_state(tmp_path, monkeypatch):
    """Only a relaunch's landing is decided anew: a revisit of the same layout with other titles is the state as
    recorded, with nothing re-recorded."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    first = phone.screens["first"] = capture("janitorai", "j04_tab1")
    home = home_from(ex, phone)
    kept = [c.key for c in home.cands]
    retitled = [{**e, "text": f"Today: {e['text']}"} if e.get("text") and len(e["text"]) > 20 else e
                for e in first.elements]
    phone.screens["reloaded"] = Screen(retitled, first.image, first.package)
    phone.screen = "reloaded"
    assert ex.record(ex.observe(), None, None, None) is home and [c.key for c in home.cands] == kept
