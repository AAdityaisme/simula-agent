"""The explorer end to end on a fake device made of fixture captures: tab sweep first, caps, the content
filter, Jev and Sonnet through the stage's cap and cache, externals, billing, a relaunch, dynamic regions,
the core-loop pass, and outputs in the frozen explore/ format."""

import functools
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from simula import decide, llm, runlog
from simula.contracts import (ActionLine, ExploreFile, IconLabel, IconName, IconPass, Rect, State, StateFile,
                              Unlisted, VisionElement)
from simula.device import observe as ob
from simula.device.mcp import parse_elements
from simula.stages import explore as stage
from simula.stages import model as model_stage
from tests import fake_device
from tests.conftest import ROOT
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


PERMISSION = "com.google.android.permissioncontroller"


def with_permission_dialog(clock):
    """An app with no content filter whose launch screen is chats. A tap there opens a permission dialog in the app's
    own task (in_task): a launch leaves it in front, and BACK dismisses it to chats, as in the committed Perplexity
    run (steps 10-13: a tap on s01 opened permissioncontroller's s05, and BACK landed on s06, in the app)."""
    phone = janitor_like(clock)
    phone.start, phone.splash, phone.screen = "chats", 0, "chats"
    phone.screens["perm"] = blank(PERMISSION)
    phone.in_task = {"perm"}
    phone.backs["perm"] = "chats"
    phone.taps[("chats", "996,209")] = "perm"
    return phone


def asks_at_each_start(phone):
    """The app asks again at each fresh start: a dismissal without an answer isn't a denial."""
    launch = phone.launch

    def asks():
        fresh = not phone.alive
        launch()
        if fresh:
            phone.go("perm")
    phone.launch = asks


def test_a_permission_dialog_a_tap_opened_is_dismissed_by_back_not_a_counted_relaunch(tmp_path, monkeypatch):
    """rt-pr33-a1a9ba5 MEDIUM 1: a launch can't displace a window in the app's own task, so the same foreign screen
    still in front after it gets BACK, as the spec keeps."""
    ex, phone = new_explorer(tmp_path, monkeypatch, with_permission_dialog)
    ex.relaunch(first=True)
    chats = ex.current
    dialog = ex.act(stage.Move("tap", next(c for c in chats.cands if c.point == (996, 209))), purpose="tour")
    assert dialog.kind == "external" and dialog.fg == PERMISSION
    ex.leave()
    assert ex.current is chats and ex.relaunches == 0 and ("back", "perm") in phone.log


def test_a_permission_prompt_at_each_start_does_not_spend_every_relaunch(tmp_path, monkeypatch):
    """rt-pr33-a1a9ba5 MEDIUM 1: the tour's next relaunch lands on the prompt, which BACK dismisses."""
    ex, phone = new_explorer(tmp_path, monkeypatch, with_permission_dialog)
    ex.relaunch(first=True)
    asks_at_each_start(phone)
    ex.relaunch(why="a NeedRelaunch in the tour")
    for _ in range(4):
        if ex.current.kind in stage.AWAY:
            ex.leave()
    assert ex.current.kind not in stage.AWAY and ex.relaunches == 1


def test_another_foreign_screen_after_the_launch_gets_a_relaunch_not_back(tmp_path, monkeypatch):
    """Greptile on #33: the launch sends the app straight on to another app's screen. BACK there could walk that app's
    history, so a counted relaunch follows."""
    def link_and_redirect(clock):
        phone = janitor_like(clock)
        phone.screens["article"] = capture("aol", "aol-home")
        return phone
    ex, phone, chats = on_chats(tmp_path, monkeypatch, link_and_redirect)
    web = ex.act(stage.Move("tap", next(c for c in chats.cands if c.point == (996, 209))), purpose="nav")
    launch, redirected = phone.launch, []

    def redirects_once():
        launch()
        if not redirected:
            redirected.append(phone.go("article"))
    phone.launch = redirects_once
    ex.leave()
    assert not [e for e in phone.log if e[0] == "back"] and ex.current is ex.root and not ex.returns
    assert ex.relaunch_reasons == [f"a launch from {web.fg} left {phone.screens['article'].package} in front"]


def arrival_model_says_back(monkeypatch):
    """The arrival model answers what its prompt allows on any screen: one action, BACK."""
    real = fake_device.fake_sonnet

    def sonnet(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        if schema.__name__ != "Arrival":
            return real(model, system, messages, effort, schema, max_tokens, total_timeout)
        body = {"identifying_text": "", "verdict": "one_action", "confidence": 0.8, "action": "back",
                "element_id": None, "direction": None, "side_effect": False,
                "reason": "the app opened a browser; back returns to it"}
        return llm.Reply(text=json.dumps(body), model=model, tokens_in=1500, tokens_out=60)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", sonnet)


def test_a_route_hop_into_another_app_gets_no_arrival_judgment_or_action_there(tmp_path, monkeypatch):
    """rt-pr33-fc08e1b MEDIUM 1: the hop's link opens another app whose BACK goes deeper into its own history. Its
    screen is never judged against the target, so the model's BACK never runs there, and the replay path keeps no
    move into it."""
    def link_opens_an_app_with_history(clock):
        phone = janitor_like(clock)
        phone.screens["article"] = capture("aol", "aol-home")
        phone.backs["web"] = "article"
        return phone
    ex, phone, chats = on_chats(tmp_path, monkeypatch, link_opens_an_app_with_history)
    arrival_model_says_back(monkeypatch)
    ex.edges[(chats.sid, ex.root.sid)] = stage.Move("tap", next(c for c in chats.cands if c.point == (996, 209)))
    ex.goto(ex.root)
    assert ("back", "web") not in phone.log and ex.counts["arrival shots"] == 0
    assert not [to for _, _, to in ex.segments[-1] if ex.by_id[to].kind in stage.AWAY]


def test_a_launch_that_lands_on_the_launch_screen_is_a_relaunch_not_a_return(tmp_path, monkeypatch):
    """rt-pr33-fc08e1b LOW 1: BACK from the launch screen ends the task (emulator fact 2), and a fresh start lands on
    the launch screen too, so a launch that finds the app there counts, whether it restarted or not."""
    def starts_on_chats(clock):
        phone = janitor_like(clock)
        phone.start, phone.splash, phone.screen = "chats", 0, "chats"
        return phone
    ex, _ = new_explorer(tmp_path, monkeypatch, starts_on_chats)
    ex.relaunch(first=True)
    root = ex.current
    assert ex.act(stage.Move("back", decider="model"), purpose="tour").kind == "external"
    ex.leave()
    assert ex.current is root and (ex.relaunches, ex.returns) == (1, [])


def test_a_tap_from_the_launch_screen_into_another_app_returns_without_a_relaunch(tmp_path, monkeypatch):
    """rt-pr33-a1a9ba5 MEDIUM 2: with no content filter the root is the launch screen. A tap there opens another app
    and the task lives on (emulator fact 1); only a BACK out of the launch screen makes a restart look the same."""
    def starts_on_chats(clock):
        phone = janitor_like(clock)
        phone.start, phone.splash, phone.screen = "chats", 0, "chats"
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, starts_on_chats)
    ex.relaunch(first=True)
    root = ex.current
    assert root is ex.launch_root and not ex.filter_taps
    web = ex.act(stage.Move("tap", next(c for c in root.cands if c.point == (996, 209))), purpose="tour")
    assert web.kind == "external" and phone.alive
    ex.leave()
    assert ex.current is root and (ex.relaunches, len(ex.returns)) == (0, 1)


def test_an_outbound_ad_on_the_launch_screen_returns_without_a_relaunch(tmp_path, monkeypatch):
    """w-guard's AOL run on a1a9ba5 (runs/aol/20261001-034507-5bcff37, act008-act009): the tour tapped a Taboola ad on
    the launch screen, Chrome opened, and the launch brought AOL's home back with its ad slot changed (aol-home, then
    aol-home-repeat). No BACK left the launch screen, so that is a return, not a relaunch."""
    words = "PetsDailyHealth in Taboola advertising section · Sponsored"

    def aol(clock):
        home = capture("aol", "aol-home", package=PACKAGE)
        ad = next(e for e in home.elements if (e.get("label") or "").startswith(words))
        return FakePhone(screens={"home": home, "again": capture("aol", "aol-home-repeat", package=PACKAGE),
                                  "web": capture("aol", "aol-article-external")},
                         start="home", taps={("home", fake_device.element_key(ad)): "web"}, clock=clock)
    ex, phone = new_explorer(tmp_path, monkeypatch, aol)
    ex.relaunch(first=True)
    root = ex.current
    assert root is ex.launch_root and not ex.filter_taps
    ad = next(c for c in root.cands if c.label.startswith(words))
    assert ex.act(stage.Move("tap", ad), purpose="tour").fg == "com.android.chrome"
    launch = phone.launch

    def reloads_its_ads():
        launch()
        phone.screen = "again"
    phone.launch = reloads_its_ads
    ex.leave()
    assert ex.current is root and (ex.relaunches, len(ex.returns)) == (0, 1)


def test_a_relaunch_that_lands_on_an_away_screen_leaves_it_before_walking_home_to_the_filter(tmp_path, monkeypatch):
    """rt-pr33-a1a9ba5 LOW 1: in an app with a content filter (janitor_like's "Limited Only"), the prompt at each start
    is left first, by BACK, then the recorded way home leads to the filter, with no second relaunch."""
    def filtered_app_with_permission_dialog(clock):
        phone = janitor_like(clock)
        phone.screens["perm"] = blank(PERMISSION)
        phone.in_task = {"perm"}
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, filtered_app_with_permission_dialog)
    ex.relaunch(first=True)
    assert ex.filter_taps
    asks_at_each_start(phone)
    ex.relaunch(why="a NeedRelaunch in the tour")
    assert ex.current is ex.root and ex.filter_checks[-1][1] and ex.relaunches == 1


def relaunch_restores_an_exit(tmp_path, monkeypatch, way_home: bool):
    """A relaunch that lands on chats, a recorded screen whose BACK left the app (and no tab bar to stop at), with
    home's content filter to re-apply. way_home records a tap from chats to the launch screen."""
    def chats_links_home(clock):
        phone = janitor_like(clock)
        phone.taps[("chats", "84,209")] = "root"
        return phone
    ex, phone, chats = on_chats(tmp_path, monkeypatch, chats_links_home)
    assert ex.filter_taps and ex.root is ex.launch_root
    ex.tabs = []
    ex.exits.add(chats.sid)
    if way_home:
        link = next(c for c in chats.cands if c.point == (84, 209))
        ex.edges[(chats.sid, ex.launch_root.sid)] = stage.Move("tap", link)
    phone.start = "chats"
    return ex, chats


def test_a_relaunch_that_restores_an_exit_walks_a_recorded_route_home_before_the_filter(tmp_path, monkeypatch):
    """Greptile on #33: back_to_root stops on a screen whose BACK left the app; the filter is re-applied only on the
    launch screen, reached by a recorded route without that BACK."""
    ex, chats = relaunch_restores_an_exit(tmp_path, monkeypatch, way_home=True)
    checks = len(ex.filter_checks)
    ex.relaunch(why="test")
    assert ex.current is ex.launch_root and ex.relaunches == 1
    assert len(ex.filter_checks) == checks + 1 and ex.filter_checks[-1][1]


def test_a_relaunch_that_restores_an_exit_with_no_way_home_relaunches_again_and_never_filters_off_home(
        tmp_path, monkeypatch):
    ex, chats = relaunch_restores_an_exit(tmp_path, monkeypatch, way_home=False)
    checks = len(ex.filter_checks)
    with pytest.raises(stage.Stop, match="relaunch cap"):
        ex.relaunch(why="test")
    assert ex.relaunches == stage.MAX_RELAUNCHES and len(ex.filter_checks) == checks
    assert all("no recorded way led there from" in why for why in ex.relaunch_reasons[1:])


def reloaded_home(tmp_path, monkeypatch, first: Screen, other: Screen, pick):
    """Home recorded from first, the controls pick(ex, home) names tapped on it, then a relaunch that re-records home
    from other. Returns the explorer and the model's states, edges with their notes, and elements by id."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screens.update(first=first, other=other)
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    taps = pick(ex, home)
    phone.screen = "chat"
    chat = ex.record(ex.observe(), home, stage.Move("tap", taps[0]), ex.obs)
    for c in taps:
        ex.log(home, chat, stage.Move("tap", c), c, "push", "", "ok")
    phone.screen, ex.home = "other", home
    assert ex.record(ex.observe(), None, None, None) is home and len(home.replaced) == 1
    ex.write(None)
    states, _, _ = model_stage.load_states(ex.out, ex.device)
    edges, notes = model_stage.load_edges(ex.out, states, model_stage.load_replaced(ex.out, ex.device))
    return ex, states, edges, notes, {e.id: e for s in states for e in s.elements}


def test_a_tap_on_home_before_its_list_reloaded_resolves_on_the_capture_it_was_taken_on(tmp_path, monkeypatch):
    """Greptile on #32: the committed run's home before and after a relaunch reloaded it (j04, j11). The replaced
    capture stays, and the model binds an earlier tap on a row the reload replaced to no element, never to the row now
    in its place, and an earlier tap on a chip both captures show to that chip."""
    ex, states, edges, notes, elements = reloaded_home(
        tmp_path, monkeypatch, capture("janitorai", "j04_tab1"), capture("janitorai", "j11_home_relaunched"),
        lambda ex, home: [next(c for c in home.cands if c.label.startswith("Wavemaker Academy: Pokemon")),
                          next(c for c in home.cands if c.label == "Trending")])
    assert sorted((elements[e.element_id].label if e.element_id else None) or "" for e in edges) == ["", "Trending"]
    assert any("a relaunch replaced" in n for n in notes)
    stale, _ = model_stage.load_edges(ex.out, states)
    assert all(e.element_id for e in stale)  # on the new capture alone, the row's tap binds to the row now in its place


def test_a_wordless_tile_tapped_before_the_reload_binds_to_no_element_when_it_shows_another_picture(tmp_path,
                                                                                                    monkeypatch):
    """rt-pr33-a1a9ba5 MEDIUM 3: one image-only tile, as a thumbnail grid has, in the same place on both captures, with
    another item's picture after the reload: a control without text is the same one only if it also looks the same."""
    first, other = capture("janitorai", "j04_tab1"), capture("janitorai", "j11_home_relaunched")
    for screen, color in ((first, (30, 60, 200)), (other, (200, 40, 40))):
        screen.elements.append({"ref": "@e900", "type": "android.widget.ImageView", "text": "",
                                "coordinates": {"x": 40, "y": 1600, "width": 120, "height": 120}})
        ImageDraw.Draw(screen.image).rectangle((40, 1600, 160, 1720), fill=color)
    _, _, edges, _, _ = reloaded_home(tmp_path, monkeypatch, first, other,
                                      lambda ex, home: [next(c for c in home.cands if c.ref == "@e900")])
    assert edges and all(e.element_id is None for e in edges)


def test_a_vision_tap_on_home_before_the_reload_never_binds_to_a_tree_container(tmp_path, monkeypatch):
    """rt-pr33-a1a9ba5 MEDIUM 3: the replaced capture keeps its vision elements (Luzia's committed home has one), so a
    vision tap resolves to the vision control, never to the wordless container around it."""
    def play(ex, home):
        ex.add_vision(home, unlisted(ex, 0, 2188, 58, 2312, "control", "Play"))
        assert home.cands[-1].kind == "vision" and home.cands[-1].ref is None
        return [home.cands[-1]]
    _, _, edges, _, elements = reloaded_home(tmp_path, monkeypatch, capture("janitorai", "j04_tab1"),
                                             capture("janitorai", "j11_home_relaunched"), play)
    assert all(elements[e.element_id].source == "vision" for e in edges if e.element_id)


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


def icon_pass_phone(clock) -> FakePhone:
    """One screen: a header "Back" button (so the screen is no dialog), a listed "Go" button and a listed icon with
    no words."""
    listed = [{"ref": "@back", "type": "android.widget.Button", "text": "Back",
               "coordinates": {"x": 42, "y": 150, "width": 200, "height": 100}},
              {"ref": "@go", "type": "android.widget.Button", "text": "Go",
               "coordinates": {"x": 42, "y": 1850, "width": 300, "height": 100}},
              {"ref": "@icon", "type": "android.widget.ImageButton",
               "coordinates": {"x": 900, "y": 1850, "width": 126, "height": 126}}]
    return FakePhone(screens={"home": Screen(listed, Image.new("RGB", (1080, 2400), (240, 240, 240)), PACKAGE)},
                     start="home", taps={}, clock=clock)


def icon_pass_screen(tmp_path, monkeypatch):
    """An explorer and the icon_pass_phone screen it recorded."""
    ex, _ = new_explorer(tmp_path, monkeypatch, icon_pass_phone)
    return ex, ex.record(ex.observe(), None, None, None)


def unlisted(ex, x0, y0, x1, y1, kind, name):
    """An icon-pass item from device px: the pass sees the content area at half size."""
    top = ex.device.content_top_px
    return Unlisted(left=x0 // 2, top=(y0 - top) // 2, right=x1 // 2, bottom=(y1 - top) // 2, kind=kind, name=name)


def test_the_icon_pass_keeps_each_unlisted_things_box_and_kind(tmp_path, monkeypatch):
    """A box is clamped to the content area; a thing whose center is off it or inside a listed box is dropped. A
    control on a picture keeps both, even one at its center, and a picture whose box mostly overlaps an earlier one's
    is the same one. Each is a candidate at its box's center, marked with its kind."""
    ex, s = icon_pass_screen(tmp_path, monkeypatch)
    top = ex.device.content_top_px
    things = [unlisted(ex, 100, 300, 900, 800, "picture", "photo"), unlisted(ex, 800, 320, 880, 400, "control", "like"),
              unlisted(ex, 440, 490, 560, 610, "control", "play"),
              unlisted(ex, 120, 320, 880, 780, "picture", "the photo again"),
              unlisted(ex, -40, 36, 400, 256, "picture", "banner"),
              unlisted(ex, 60, 1860, 320, 1940, "picture", "logo"),
              unlisted(ex, 100, 1860, 200, 1940, "control", "in go"),
              unlisted(ex, 100, 2340, 200, 2400, "control", "under the content")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=things))
    ex.name_icons(s)
    assert s.vision == [VisionElement(name="like", rect_px=Rect(x=800, y=320, w=80, h=80), kind="control"),
                        VisionElement(name="play", rect_px=Rect(x=440, y=490, w=120, h=120), kind="control"),
                        VisionElement(name="photo", rect_px=Rect(x=100, y=300, w=800, h=500), kind="picture"),
                        VisionElement(name="banner", rect_px=Rect(x=0, y=top, w=400, h=256 - top), kind="picture")]
    assert [(c.label, c.kind, c.rect, c.point) for c in s.cands if not c.ref] == [
        (v.name, "vision" if v.kind == "control" else "picture", v.rect_px, ob.center(v.rect_px)) for v in s.vision]


def test_an_avatar_on_a_banner_keeps_both_whichever_comes_first(tmp_path, monkeypatch):
    for n, first_banner in enumerate((True, False)):
        ex, s = icon_pass_screen(tmp_path / str(n), monkeypatch)
        banner, avatar = (unlisted(ex, 100, 300, 900, 800, "picture", "banner"),
                          unlisted(ex, 150, 350, 350, 550, "picture", "avatar"))
        things = [banner, avatar] if first_banner else [avatar, banner]
        monkeypatch.setattr(ex, "ask", lambda *a, things=things: IconPass(names=[], unlisted=things))
        ex.name_icons(s)
        assert [v.name for v in s.vision] == [t.name for t in things], first_banner


LUZIA, AOL = "luzia/20260929-204554-1f19585", "aol/20260929-205304-1f19585"


def committed(run: str, sid: str) -> Screen:
    """A committed state's capture, as the fake phone shows it."""
    states = ROOT / "runs" / run / "explore" / "states"
    return Screen(parse_elements(json.loads((states / f"{sid}.elements.json").read_text())),
                  Image.open(states / f"{sid}.png").convert("RGB"), PACKAGE)


def recorded_dialog(tmp_path, monkeypatch, parent: Screen, dialog: Screen, things) -> tuple:
    """An explorer that recorded `dialog` through record(), over `parent`, with the icon pass answering
    `things(ex)` for the dialog."""
    def phone(clock):
        return FakePhone(screens={"parent": parent, "dialog": dialog}, start="parent", taps={}, clock=clock)
    ex, phone_ = new_explorer(tmp_path, monkeypatch, phone)
    before = ex.observe()
    under = ex.record(before, None, None, None)
    phone_.screen = "dialog"
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=things(ex)))
    s = ex.record(ex.observe(), under, stage.Move("tap", under.cands[0]), before)
    assert s.kind in ("modal", "sheet") and s.box, (s.kind, s.box)
    return ex, s


def luzia_toki(tmp_path, monkeypatch) -> tuple:
    """Luzia's Toki dialog: its listed controls start at x=162, while its card and header art start at x=120. The art
    at its own extent, the parent's avatar behind the scrim, and the parent's notifications control behind it."""
    return recorded_dialog(tmp_path, monkeypatch, committed(LUZIA, "s01"), committed(LUZIA, "s05"), lambda ex: [
        unlisted(ex, 120, 582, 960, 1244, "picture", "header art"),
        unlisted(ex, 396, 176, 684, 572, "picture", "assistant avatar"),
        unlisted(ex, 860, 160, 944, 242, "control", "notifications")])


def test_a_dialogs_art_reaching_past_its_listed_controls_is_kept_whole(tmp_path, monkeypatch):
    """The red team's repros on 5afd89c: a dialog's box is its listed controls' extent, so art clamped to it was a
    fragment. Membership is judged by the dialog's own controls; only the content area clamps."""
    ex, s = luzia_toki(tmp_path, monkeypatch)
    assert s.box == Rect(x=162, y=581, w=798, h=1267)
    assert s.vision == [VisionElement(name="notifications", rect_px=Rect(x=860, y=160, w=84, h=82), kind="control"),
                        VisionElement(name="header art", rect_px=Rect(x=120, y=582, w=840, h=662), kind="picture")]
    ex.current = s
    assert "picture" not in {c.kind for c in ex.surface()}, "the walk never takes a picture"


def test_a_dialogs_art_is_cropped_whole_by_the_model_stage(tmp_path, monkeypatch):
    ex, s = luzia_toki(tmp_path / "explore", monkeypatch)
    image = Image.open(ex.out / "states" / f"{s.sid}.png").convert("RGB")
    elements = model_stage.build_elements(s.sid, [], [], s.vision, np.asarray(image), ex.device)
    state = State(id=s.sid, kind=s.kind, parent_id=None, name="", purpose="", fingerprint="", canonical_png="",
                  elements=elements, in_mock_scope=False, content_rating="safe", dynamic_regions=[],
                  blocked_reason=None)
    (tmp_path / "assets").mkdir()
    art = model_stage.finish_elements(state, {s.sid}, set(), image, tmp_path, ex.device).elements[-1]
    crop = np.asarray(Image.open(tmp_path / art.asset_png).convert("RGB")).astype(int)
    green = (np.abs(crop - np.array([166, 227, 170])).sum(axis=2) < 30).any(axis=0)
    assert crop.shape[:2] == (662, 840) and green.any() and not green[0], "the emoji bubble is whole, not cut"


def test_a_dimmed_parent_photo_beside_a_drawer_is_not_the_drawers(tmp_path, monkeypatch):
    """AOL's drawer ends at x=814, but its box reaches x=972 over the parent's controls left listed behind the scrim.
    The parent's hero photo right of the drawer is centered in that box, yet outside the drawer's own controls. The
    parent is the feed as the drawer's capture lists it under the drawer (the drawer's own subtree starts at element
    136): an ad moved the feed after s01's canonical capture, and from that capture the moved rows would read as new."""
    drawer = committed(AOL, "s03")
    parent = Screen(drawer.elements[:136], committed(AOL, "s01").image, PACKAGE)
    ex, s = recorded_dialog(tmp_path, monkeypatch, parent, drawer, lambda ex: [
        unlisted(ex, 814, 624, 1080, 1236, "picture", "dimmed article photo")])
    own = ob.bbox([c.rect for c in s.cands if c.ref])
    assert s.box == Rect(x=0, y=168, w=972, h=1409) and own.x + own.w == 814
    assert not s.vision


def test_a_picture_is_never_tapped_at_a_denied_control_drawn_at_its_center(tmp_path, monkeypatch):
    """Greptile on 8c9ef5b: a picture's tap lands on what lies at its center, so a denied control drawn there denies
    the picture's tap too. Neither is tapped, and the refusal names what lies there."""
    ex, s = icon_pass_screen(tmp_path, monkeypatch)
    things = [unlisted(ex, 100, 300, 900, 800, "picture", "sunset over a lake"),
              unlisted(ex, 440, 490, 560, 610, "control", "delete")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=things))
    ex.name_icons(s)
    ex.current = s
    picture = next(c for c in s.cands if c.kind == "picture")
    assert all(c.label != "delete" for c in ex.options(s))
    taps = len([entry for entry in ex.phone.log if entry[0] == "tap"])
    assert ex.act(stage.Move("tap", picture)) is s
    assert len([entry for entry in ex.phone.log if entry[0] == "tap"]) == taps
    assert lines(ex)[-1].change_summary == "denied: delete ('delete' lies at its tap point)"


def test_a_states_last_tap_goes_to_a_picture_its_controls_would_use_up(tmp_path, monkeypatch):
    """Greptile on 8c9ef5b: with more controls than taps a state allows, a tile that leads on was never tried."""
    ex, s = icon_pass_screen(tmp_path, monkeypatch)
    controls = [unlisted(ex, 120 * n, 600, 120 * n + 100, 700, "control", f"c{n}") for n in range(5)]
    things = [*controls, unlisted(ex, 100, 900, 1000, 1400, "picture", "featured tile")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=things))
    ex.name_icons(s)
    assert len(ex.options(s)) > stage.TAPS_PER_STATE and all(c.kind != "picture" for c in ex.options(s))
    picked = []
    while s.taps < stage.TAPS_PER_STATE and (move := ex.next_move(s)) and move.cand:
        picked.append(move.cand.kind)
        s.tried.add(move.cand.key)
        s.taps += 1
    assert len(picked) == stage.TAPS_PER_STATE and picked[-1] == "picture" and "picture" not in picked[:-1], picked


def test_a_picture_low_on_home_is_never_a_tab(tmp_path, monkeypatch):
    """Two pictures side by side at the bottom look like a tab bar; the tab sweep would tap them before any control."""
    ex, _ = new_explorer(tmp_path, monkeypatch, icon_pass_phone)
    tiles = [unlisted(ex, 40, 2150, 500, 2330, "picture", "left tile"),
             unlisted(ex, 580, 2150, 1040, 2330, "picture", "right tile")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=tiles))
    ex.relaunch(first=True)
    assert [c.label for c in ex.root.cands if c.kind == "picture"] == ["left tile", "right tile"]
    assert ex.tabs == []


def test_the_filter_menu_never_hands_jev_a_picture(tmp_path, monkeypatch):
    """The red team's repro on 8c9ef5b, inverted: the filter menu's options are the sheet's controls, never a
    picture the icon pass found between them."""
    def button(ref, text, x, y, w, h):
        return {"ref": ref, "type": "android.widget.Button", "text": text,
                "coordinates": {"x": x, "y": y, "width": w, "height": h}}
    home = [button("@back", "Back", 42, 150, 200, 100), button("@filter", "Content filter", 42, 400, 400, 100),
            button("@go", "Go", 42, 1850, 300, 100)]
    sheet = home + [button("@all", "Show everything", 100, 1700, 880, 120),
                    button("@safe", "Hide mature content", 100, 1900, 880, 120)]

    def phone(clock):
        ramp = Image.linear_gradient("L").rotate(90).resize((1080, 2400)).convert("RGB")
        return FakePhone(screens={"home": Screen(home, ramp, PACKAGE),
                                  "sheet": Screen(sheet, ramp.transpose(Image.FLIP_LEFT_RIGHT), PACKAGE)},
                         start="home", taps={("home", "Content filter"): "sheet"}, clock=clock)
    ex, _ = new_explorer(tmp_path, monkeypatch, phone)
    ex.obs = ex.observe()
    ex.current = ex.record(ex.obs, None, None, None)
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=[
        unlisted(ex, 300, 1824, 780, 1896, "picture", "mature content badge")]))
    asked = {}

    def pick(s, opts, question, step, none_label=None):
        asked[step] = (s, opts)
        return next(c for c in opts if c.label == "Content filter") if step == "filter" else None
    monkeypatch.setattr(ex, "pick", pick)
    monkeypatch.setattr(ex, "stands_out", lambda c: False)
    ex.find_filter()
    opened, opts = asked["filter.menu"]
    assert opened.kind in ("modal", "sheet") and any(c.kind == "picture" for c in opened.cands)
    assert opts and all(c.kind != "picture" for c in opts)


def test_a_launch_dialog_is_never_dismissed_by_a_close_up_picture(tmp_path, monkeypatch):
    """The red team's repro on 8c9ef5b, inverted: "close-up of a dog" matches the dismiss words, but a picture's name
    says what it shows. A launch dialog with no dismiss control of its own is left with BACK."""
    def button(ref, text, y):
        return {"ref": ref, "type": "android.widget.Button", "text": text,
                "coordinates": {"x": 100, "y": y, "width": 880, "height": 120}}
    home = [button("@back", "Back", 150), button("@go", "Go", 1850)]

    def phone(clock):
        blank_rgb = Image.new("RGB", (1080, 2400), (240, 240, 240))
        return FakePhone(screens={"home": Screen(home, blank_rgb, PACKAGE),
                                  "dialog": Screen([button("@c", "Continue", 700), button("@m", "Learn more", 1700)],
                                                   blank_rgb, PACKAGE)},
                         start="dialog", taps={}, backs={"dialog": "home"}, clock=clock)
    ex, phone_ = new_explorer(tmp_path, monkeypatch, phone)
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=[
        unlisted(ex, 100, 900, 980, 1500, "picture", "close-up of a dog")]))
    ex.obs = ex.observe()
    ex.normalize()
    dialog = ex.states[0]
    assert dialog.launch and [c.label for c in dialog.cands if c.kind == "picture"] == ["close-up of a dog"]
    assert ("back", "dialog") in phone_.log and not [entry for entry in phone_.log if entry[0] == "tap"]


def test_controls_and_pictures_each_keep_eight_slots(tmp_path, monkeypatch):
    """A screen full of pictures never pushes out the controls the tour needs, whichever the model lists first."""
    ex, s = icon_pass_screen(tmp_path, monkeypatch)
    pictures = [unlisted(ex, 120 * n, 300, 120 * n + 100, 400, "picture", f"p{n}") for n in range(9)]
    controls = [unlisted(ex, 120 * n, 600, 120 * n + 100, 700, "control", f"c{n}") for n in range(9)]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=pictures + controls))
    ex.name_icons(s)
    assert [v.name for v in s.vision] == [f"c{n}" for n in range(8)] + [f"p{n}" for n in range(8)]


def test_a_picture_is_a_tour_option_only_after_every_control_and_never_the_upsell_entry(tmp_path, monkeypatch):
    """A tile the tree doesn't list may be the way on (the pass called the tiles that lead to a committed run's s13
    pictures in 3 of 3 tries), so a picture stays a tap, after the state's controls (its last tap aside). Its name
    says what it shows, not what a tap does, so a "premium banner" is never the upsell entry."""
    ex, s = icon_pass_screen(tmp_path, monkeypatch)
    pictures = ["premium banner", "Kelly Osbourne and Sid Wilson photo"]
    things = [unlisted(ex, 100, 300, 900, 800, "picture", pictures[0]),
              unlisted(ex, 100, 900, 1000, 1400, "picture", pictures[1]),
              unlisted(ex, 800, 1500, 880, 1580, "control", "more")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=things))
    ex.name_icons(s)
    assert [(c.label, c.kind) for c in s.cands if c.kind == "picture"] == [(name, "picture") for name in pictures]
    assert ex.entry(s) is None
    picked = []
    for _ in range(stage.TAPS_PER_STATE):
        kinds = {c.kind for c in ex.options(s)}
        assert kinds == {"picture"} or "picture" not in kinds, kinds
        move = ex.next_move(s)
        if not (move and move.cand):
            break
        picked.append(move.cand.kind == "picture")
        s.tried.add(move.cand.key)
        s.taps += 1
    assert True in picked and picked == sorted(picked), "every control is tapped before any picture"


def test_a_re_recorded_home_keeps_only_the_icon_pass_of_its_new_capture(tmp_path, monkeypatch):
    ex, s = icon_pass_screen(tmp_path, monkeypatch)
    old = [unlisted(ex, 100, 300, 900, 800, "picture", "old photo"), unlisted(ex, 800, 900, 880, 980, "control", "old")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=old))
    ex.name_icons(s)
    assert [v.name for v in s.vision] == ["old", "old photo"] and s.icon_labels
    new = [unlisted(ex, 100, 1000, 900, 1500, "picture", "new photo")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[IconName(box_id=3, name="new name")], unlisted=new))
    obs = ex.observe()
    ex.refresh(s, obs, obs.cands)
    assert [(v.name, v.kind) for v in s.vision] == [("new photo", "picture")]
    assert s.icon_labels == [IconLabel(mcp_ref="@icon", name="new name")]


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
    assert phone.chats["chat"][2][0] == stage.CORE_MESSAGES[2]
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


def test_a_conversation_that_talks_prices_and_plans_is_never_the_paywall(tmp_path, monkeypatch):
    """Fable E7: a chat holding an earlier run's talk of prices and plans was that run's paywall."""
    def talked(clock):
        phone = janitor_like(clock)
        phone.chats["chat"] = [("What's the best phone under $300?",
                                "The Pixel 8a at $299, or a subscription at $15 per month.")]
        return phone
    ex, _ = explore(tmp_path, monkeypatch, phone_factory=talked, no_send=True)
    chats = {s.sid for s in ex.states if any("$299" in t for t in ob.texts(s.elements, ex.device))}
    assert chats and ex.paywall not in chats and not ex.priced_paywall(), (chats, ex.paywall)
    assert "paywall_or_membership" in ex.checklist()[1]


def voice_sheet_over_chat(clock):
    phone = janitor_like(clock)
    chat = phone.screens["chat"]
    sheet = [{"ref": "@sheet", "type": "android.view.ViewGroup", "text": "",
              "coordinates": {"x": 0, "y": 1500, "width": 1080, "height": 837}},
             {"ref": "@title", "type": "android.widget.TextView", "text": "Choose a voice for this character",
              "coordinates": {"x": 20, "y": 1540, "width": 1040, "height": 80}},
             {"ref": "@voice", "type": "android.widget.Button", "text": "Voice one",
              "coordinates": {"x": 100, "y": 2150, "width": 880, "height": 120}}]
    phone.screens["chat_sheet"] = Screen(chat.elements + sheet, chat.image, chat.package)
    phone.replies["chat_sheet"] = phone.replies["chat"]
    phone.chats["chat_sheet"] = phone.chats.setdefault("chat", [])
    phone.screen, phone.splash_left = "chat", 0
    return phone


def test_a_reply_that_arrived_after_the_chat_was_recorded_never_prices_a_sheet_over_it(tmp_path, monkeypatch):
    """Greptile on 459b030: the chat is recorded, a reply quoting a price arrives, then an unpriced sheet opens over
    it. The paywall pass's fallback and the exhibit read the sheet against the screen it opened over right before,
    as record() does, not against the chat's first capture."""
    ex, phone = new_explorer(tmp_path, monkeypatch, voice_sheet_over_chat)
    chat = ex.record(ex.observe(), None, None, None)
    reply = "The student plan is $4.99 a month with the first week free."
    phone.chats["chat"].append(("Which plan is best for students?", reply))
    before = ex.observe()
    phone.go("chat_sheet")
    sheet = ex.record(ex.observe(), chat, stage.Move("tap", why="a voice for the chat"), before)
    assert sheet.box and sheet.upsell and not sheet.priced
    ex.paywall_pass()
    assert ex.paywall != sheet.sid and "$4.99" not in stage.paywall_line(ex), stage.paywall_line(ex)
    assert reply not in ex.wall_texts(sheet)


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


def test_a_row_something_lies_over_is_no_candidate(tmp_path, monkeypatch):
    """A card across the list: its body is no control (a wordless layout holding texts), yet it lies over the first
    row's tap point."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    phone.screen = "limited"
    rows = ob.feed_items(ob.controls(phone.screens["limited"].elements, ex.device), ex.device)
    x, y = rows[0].point
    phone.screens["limited"].elements.extend(
        {"ref": f"@card{n}", "type": kind, "text": text, "coordinates": {"x": 40 + 50 * bool(n), "y": y - 60 + dy,
                                                                         "width": 1000 - 100 * bool(n),
                                                                         "height": 200 if n == 0 else 40}}
        for n, (kind, text, dy) in enumerate([("android.view.ViewGroup", "", 0), ("android.widget.TextView", "Plus", 0),
                                              ("android.widget.TextView", "$4.99/month", 80)]))
    labels = {c.tree_label for c in ex.record(ex.observe(), None, None, None).cands}
    assert rows[0].tree_label not in labels and rows[1].tree_label in labels


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


# an item's page of new text: the chats a character's sheet lists, each with its last reply
CHAT_ROWS = [{"ref": f"@r{n}", "type": "android.view.ViewGroup", "text": text,
              "coordinates": {"x": 0, "y": 1380 + 260 * n, "width": 1080, "height": 240}}
             for n, text in enumerate((
                 ("Mon, 14 messages: The rain had not stopped for three days, and the old inn by the river was full "
                  "of travellers who had nowhere else to go. She poured the tea slowly and asked where you were "
                  "headed."),
                 ("Sun, 6 messages: He looked up from the map spread across the table, traced the coastline with "
                  "one finger, and said the lighthouse was still two days away if the weather held."),
                 ("Fri, 3 messages: The market square was loud with bells and bargaining, and somewhere behind the "
                  "fruit stalls a fiddler was playing the song your grandmother used to hum.")))]


def sheet_first(clock):
    """A feed item opens a sheet in the feed's window (the feed stays listed behind it); the sheet's own New chat
    control opens the conversation."""
    phone = janitor_like(clock)
    feed = phone.screens["limited"]
    image = feed.image.copy()
    ImageDraw.Draw(image).rectangle((0, 1250, 1080, 2400), fill=(40, 40, 48))
    own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": "Your chats with this character",
            "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
           *CHAT_ROWS,
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


# rt-pr41 M2: an item's page in a sheet often has a close control; closing asks nothing of the user.
@pytest.mark.parametrize("close", [None, ("ImageButton", "Close"), ("ImageView", "Dismiss")],
                         ids=["plain", "close", "dismiss"])
def test_a_sheet_the_feed_pass_opens_is_its_result_not_a_stop(tmp_path, monkeypatch, close):
    def with_close(clock):
        phone = sheet_first(clock)
        if close:
            sheet = phone.screens["sheet"]
            control = {"ref": "@close", "type": f"android.widget.{close[0]}", "label": close[1],
                       "coordinates": {"x": 960, "y": 2168, "width": 84, "height": 84}}
            phone.screens["sheet"] = type(sheet)(sheet.elements + [control], sheet.image, sheet.package)
        return phone
    ex, _ = new_explorer(tmp_path, monkeypatch, with_close)
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


# rt-pr41 H1: a tree may list a bottom sheet before the screen it covers, right after the status bar.
@pytest.mark.parametrize("first", [False, True], ids=["listed-after", "listed-first"])
def test_a_sheet_over_the_chat_in_its_own_window_stops_the_loop(tmp_path, monkeypatch, first):
    def sheet_after_two(clock):
        phone = chatty(clock)
        chat = phone.screens["chat"]
        sheet = [{"ref": "@sheet", "type": "android.view.ViewGroup", "text": "You're out of free messages",
                  "coordinates": {"x": 0, "y": 1700, "width": 1080, "height": 637}},
                 {"ref": "@more", "type": "android.widget.Button", "text": "Get more messages",
                  "coordinates": {"x": 100, "y": 2150, "width": 880, "height": 120}}]
        at = next(n for n, e in enumerate(chat.elements) if "systemui" not in (e.get("identifier") or "")) \
            if first else len(chat.elements)
        phone.screens["chat_sheet"] = type(chat)(chat.elements[:at] + sheet + chat.elements[at:], chat.image,
                                                 chat.package)
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
    assert phone.sent == 2 and stops == [(2, "sheet opened")] and "limit" not in ex.checklist()[0]


LIFT_PX = 800


def keyboard_over_the_reply(clock):
    """A docked keyboard lifts the composer over the conversation, which keeps its place: the last reply, a message
    container listed before the box like every message, now lies under the lifted composer, its bounds around the
    send (JanitorAI's false "sheet opened" and "input disabled"); a send drops the keyboard."""
    phone = chatty(clock)
    chat = phone.screens["chat"]
    box = next(e for e in chat.elements if e["type"].endswith("EditText"))
    floor = box["coordinates"]["y"] - 32
    lifted = [{**e, "coordinates": {**e["coordinates"], "y": e["coordinates"]["y"] - LIFT_PX}}
              if e["coordinates"]["y"] >= floor else e for e in chat.elements]
    reply = {"ref": "@reply", "type": "android.view.ViewGroup",
             "text": "He kept counting ships long after the harbour went dark.",
             "coordinates": {"x": 21, "y": floor - 300, "width": 1038, "height": 260}}
    under = {**reply, "coordinates": {**reply["coordinates"], "y": floor - LIFT_PX + 60}}
    at = next(n for n, e in enumerate(chat.elements) if e["coordinates"]["y"] >= floor)
    image = chat.image.copy()
    image.paste(chat.image.crop((0, floor, 1080, 2400)), (0, floor - LIFT_PX))
    ImageDraw.Draw(image).rectangle((0, 2400 - LIFT_PX, 1080, 2400), fill=(60, 60, 70))
    phone.screens["chat"] = Screen(chat.elements[:at] + [reply] + chat.elements[at:], chat.image, chat.package)
    phone.screens["keyboard"] = Screen(lifted[:at] + [under] + lifted[at:], image, chat.package)
    phone.replies["keyboard"] = phone.replies["chat"]
    phone.chats["keyboard"] = phone.chats.setdefault("chat", [])
    phone.taps[("chat", "inputBar")] = "keyboard"
    phone.after_sends = {n: "chat" for n in range(1, 9)}
    return phone


def test_a_reply_under_a_composer_the_keyboard_lifted_is_no_sheet(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=keyboard_over_the_reply, budget="deep")
    assert ("type", "keyboard", stage.CORE_MESSAGES[0]) in phone.log
    assert phone.sent == ex.core_reps == 8 and not ex.core_hit, ex.core_results
    assert not any(line.loop_stop for line in lines(ex))


def late_send(ready_s: float):
    def factory(clock):
        phone = chatty(clock)
        phone.send_ready_s = ready_s
        return phone
    return factory


def test_send_enabled_once_the_composer_settles_is_not_input_disabled(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=late_send(5.0))
    assert phone.sent == ex.core_reps and not ex.core_hit, ex.core_results


def test_a_send_that_stays_disabled_stops_the_loop_with_its_capture(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=late_send(float("inf")))
    stops = [(line.step, line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert phone.sent == 0 and [stop[1:] for stop in stops] == [(1, "input disabled")]
    name = ex.out / "stops" / f"act{stops[0][0]:03d}"
    saved = parse_elements(json.loads(Path(f"{name}.elements.json").read_text()))
    assert any(e.get("enabled") is False and e.get("identifier", "").endswith("sendButton") for e in saved)
    assert Path(f"{name}.png").exists() and Path(f"{name}.before.elements.json").exists()
    trace = runlog.read_trace(ex.run_dir / "trace.jsonl")
    assert any(line.step == "stop" and f"explore/stops/{name.name}.png" in line.note for line in trace)


LIMIT_SHEET_S = 4.0


def sheet_while_send_waits(clock):
    """Pass 2: right after typing send is disabled; a few seconds later a limit sheet is up over the composer and send
    under it is enabled again in the tree (rt-pr41 H2)."""
    phone = chatty(clock)
    plain = phone.current_elements
    sheet = [{"ref": "@sheet", "type": "android.view.ViewGroup", "text": "You're out of free messages",
              "coordinates": {"x": 0, "y": 1600, "width": 1080, "height": 420}},
             {"ref": "@more", "type": "android.widget.Button", "text": "Get more messages",
              "coordinates": {"x": 100, "y": 1990, "width": 880, "height": 140}}]

    def current_elements():
        elements = plain()
        if phone.screen != "chat" or phone.sent < 1 or len(phone.typed) < 2:
            return elements
        if phone.clock.t - phone.typed_at < LIMIT_SHEET_S and phone.draft:
            return [{**e, "enabled": False} if "send" in (e.get("identifier") or "").lower() + ob.words(e).lower()
                    else e for e in elements]
        return elements + sheet
    phone.current_elements = current_elements
    return phone


def test_a_sheet_that_comes_up_while_send_settles_stops_the_chat(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=sheet_while_send_waits, budget="deep")
    stops = [(line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert [stop[0] for stop in stops] == [2] and phone.sent == 1, (stops, phone.sent)


def log_in_after_the_box_tap(clock):
    """Pass 2: the tap on the text box shows a "one moment" screen, then the app's log-in screen with its email
    field focused (rt-pr41 H2)."""
    phone = chatty(clock)
    plain, tap, typed = phone.current_elements, phone.tap, phone.type_text
    phone.wall_at, phone.typed_on_wall = None, []
    header = [e for e in phone.screens["chat"].elements if "systemui" in (e.get("identifier") or "")]
    bar = [{"ref": "@bar", "type": "android.widget.TextView", "text": "Chats",
            "coordinates": {"x": 0, "y": 150, "width": 1080, "height": 120}},
           {"ref": "@nav", "type": "android.widget.TextView", "text": "Home",
            "coordinates": {"x": 0, "y": 2150, "width": 1080, "height": 150}}]
    loading = header + bar + [{"ref": "@wait", "type": "android.widget.TextView", "text": "One moment",
                               "coordinates": {"x": 340, "y": 1150, "width": 400, "height": 60}}]
    log_in = header + bar + [{"ref": "@title", "type": "android.widget.TextView", "text": "Log in to keep chatting",
                              "coordinates": {"x": 60, "y": 300, "width": 960, "height": 80}},
                             {"ref": "@email", "type": "android.widget.EditText", "text": "Email", "focused": True,
                              "coordinates": {"x": 60, "y": 520, "width": 960, "height": 130}},
                             {"ref": "@go", "type": "android.widget.Button", "text": "Continue",
                              "coordinates": {"x": 60, "y": 720, "width": 960, "height": 130}}]

    def on_tap(x, y):
        tap(x, y)
        if phone.log[-1] == ("tap", "chat", "inputBar") and phone.sent >= 1 and phone.wall_at is None:
            phone.wall_at = phone.clock.t

    def on_type(text):
        typed(text)
        if phone.wall_at is not None:
            phone.typed_on_wall.append(text)

    def current_elements():
        if phone.wall_at is None:
            return plain()
        return loading if phone.clock.t - phone.wall_at < LIMIT_SHEET_S else log_in
    phone.tap, phone.type_text, phone.current_elements = on_tap, on_type, current_elements
    return phone


def test_the_chat_message_is_never_typed_into_a_log_in_screen(tmp_path, monkeypatch):
    ex, phone = explore(tmp_path, monkeypatch, phone_factory=log_in_after_the_box_tap, budget="deep")
    stops = [(line.loop_pass, line.loop_stop) for line in lines(ex) if line.loop_stop]
    assert not phone.typed_on_wall and [stop[0] for stop in stops] == [2], (phone.typed_on_wall, stops)


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
           *({**row, "coordinates": {"x": 0, "y": 1710 + 220 * n, "width": 1080, "height": 210}}
             for n, row in enumerate(CHAT_ROWS[:2])),
           {"ref": "@s3", "type": "android.view.ViewGroup", "text": "Start new chat",
            "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
    ex, opened = feed_pass_sheets(tmp_path, monkeypatch, sheet_over_feed(own))
    assert not opened[0].loop_stop and not ex.core_hit, (opened[0].loop_stop, ex.core_hit)


# Fable E6: a sheet asking for an upgrade or a registration holds no item's page, so it stops the feed pass.
def test_a_sheet_that_asks_for_an_upgrade_or_a_registration_stops_the_feed_pass(tmp_path, monkeypatch):
    for title, button in (("Upgrade to keep chatting", "See plans"), ("Register to continue", "Get started")):
        own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": title,
                "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
               {"ref": "@s2", "type": "android.widget.Button", "text": button,
                "coordinates": {"x": 240, "y": 1950, "width": 600, "height": 120}},
               {"ref": "@s3", "type": "android.widget.Button", "text": "Not now",
                "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
        ex, opened = feed_pass_sheets(tmp_path / button.replace(" ", "-"), monkeypatch, sheet_over_feed(own))
        assert opened[0].loop_stop == "sheet opened" and ex.core_hit.startswith("sheet opened"), ex.core_results
        assert stage.loop_end(ex, 1).startswith("Stopped by the app: sheet opened")


# rt-pr41 M1: an upgrade sheet's feature list makes it as long as an item's page; its controls still ask.
def test_an_upgrade_sheet_with_a_long_feature_list_stops_the_feed_pass(tmp_path, monkeypatch):
    features = ("Unlimited conversations with every character, any time of day",
                "Faster and longer replies from our most capable model",
                "Characters remember four times more of your story",
                "Skip the queue when the servers are busy at night",
                "Early access to new features before anyone else gets them",
                "Custom chat themes and an ad-free experience on all your devices",
                "Priority support from our team whenever something goes wrong")
    own = [{"ref": "@s1", "type": "android.view.ViewGroup", "text": "Upgrade to keep chatting",
            "coordinates": {"x": 0, "y": 1250, "width": 1080, "height": 110}},
           *({"ref": f"@f{n}", "type": "android.widget.TextView", "text": text,
              "coordinates": {"x": 60, "y": 1380 + 70 * n, "width": 960, "height": 60}}
             for n, text in enumerate(features)),
           {"ref": "@s2", "type": "android.widget.Button", "text": "See plans",
            "coordinates": {"x": 240, "y": 1950, "width": 600, "height": 120}},
           {"ref": "@s3", "type": "android.widget.Button", "text": "Not now",
            "coordinates": {"x": 240, "y": 2150, "width": 600, "height": 120}}]
    assert sum(len(ob.words(e)) for e in own) >= stage.PAGE_CHARS
    ex, opened = feed_pass_sheets(tmp_path, monkeypatch, sheet_over_feed(own))
    assert opened[0].loop_stop == "sheet opened", (opened[0].loop_stop, ex.core_results[:2])


# rt-pr29-b938d29: a card over home after a refresh, every row under it; the card's body is a clickable wordless box.
# Its words ask for nothing, so the landing is home (a card asking for money is a wall, not home: see below).
PROMO = [{"ref": "@promo", "type": "android.view.ViewGroup", "text": "", "clickable": True,
          "coordinates": {"x": 200, "y": 1100, "width": 700, "height": 950}},
         {"ref": "@title", "type": "android.widget.TextView", "text": "What's new",
          "coordinates": {"x": 250, "y": 1120, "width": 600, "height": 50}},
         {"ref": "@more", "type": "android.widget.TextView", "text": "Characters now remember more",
          "coordinates": {"x": 250, "y": 1410, "width": 600, "height": 50}},
         {"ref": "@ok", "type": "android.widget.Button", "text": "Got it",
          "coordinates": {"x": 320, "y": 1950, "width": 400, "height": 60}}]


def home_under_a_promo(tmp_path, monkeypatch, overlay=PROMO, before_rows=False):
    """Home recorded from j11, then re-recorded by a relaunch with the promo over its list. Every tap lands on the
    promo's own app from then on."""
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    base = capture("janitorai", "j11_home_relaunched")
    image = base.image.copy()
    ImageDraw.Draw(image).rectangle((200, 1100, 900, 2050), fill=(52, 52, 60))
    elements = overlay + base.elements if before_rows else base.elements + overlay
    phone.screens.update(first=base, covered=Screen(elements, image, base.package),
                         promo=Screen([], Image.new("RGB", (1080, 2400)), "com.example.promo"))
    phone.screen = "first"
    home = ex.current = ex.record(ex.observe(), None, None, None)
    feed = stage.CoreAction("feed", home, ob.feed_items(home.cands, ex.device, ex.tab_keys()), "read items")
    phone.screen, ex.home = "covered", home
    assert ex.record(ex.observe(), None, None, None) is home
    ex.home, tapped = None, []

    def trap(x, y):
        tapped.append((x, y))
        phone.screen = "promo"
    phone.tap = trap
    return ex, feed, tapped


def test_the_walk_never_taps_a_stale_row_through_a_card_after_a_refresh(tmp_path, monkeypatch):
    ex, feed, tapped = home_under_a_promo(tmp_path, monkeypatch)
    assert feed.controls and ex.rows(feed) == []
    ex.walk_into(feed, 0)
    assert not tapped and any("the walk ends" in t.note for t in runlog.read_trace(ex.run_dir / "trace.jsonl"))


def test_the_feed_pass_never_taps_a_stale_row_through_a_card_after_a_refresh(tmp_path, monkeypatch):
    ex, feed, tapped = home_under_a_promo(tmp_path, monkeypatch)
    ex.touring = False
    monkeypatch.setattr(ex, "choose_core", lambda: [feed])
    monkeypatch.setattr(ex, "at_core", lambda n: True)
    ex.core_loop()
    assert not tapped and ex.core_results == [f"pass 1: no recorded rows left on {feed.state.sid}"]


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


def test_a_clickable_wordless_overlay_leaves_no_covered_row_a_candidate(tmp_path, monkeypatch):
    ex, phone = new_explorer(tmp_path, monkeypatch, janitor_like)
    row = {"ref": "@row", "type": "android.view.ViewGroup", "text": "Read item",
           "coordinates": {"x": 50, "y": 1100, "width": 800, "height": 200}}
    box = {"ref": "@blank", "type": "android.view.ViewGroup", "text": "", "clickable": True,
           "coordinates": {"x": 300, "y": 1130, "width": 400, "height": 150}}
    phone.screens["wordless"] = Screen([row, box], Image.new("RGB", (1080, 2400)), PACKAGE)
    phone.screen = "wordless"
    assert "Read item" not in {c.label for c in ex.record(ex.observe(), None, None, None).cands}


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


def test_a_clickable_card_listed_before_the_rows_still_covers_them(tmp_path, monkeypatch):
    ex, feed, tapped = home_under_a_promo(tmp_path, monkeypatch, before_rows=True)
    assert ex.rows(feed) == []
    ex.walk_into(feed, 0)
    assert not tapped


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
