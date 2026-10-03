"""The scorecard: its identity rule, hidden screen changes, every section read from runs built with
tests/explore_fixture.py, a run missing stages, inventory matching, and that it never writes into a run."""

import hashlib
import json
import shutil

import pytest
from pydantic import ValidationError

from simula import cli, scorecard
from simula.contracts import (ActionLine, Device, Edge, Element, ExploreFile, Manifest, ProductModel, Provenance, Rect,
                              State, StateFile, TraceLine)
from simula.stages import model
from tests.conftest import FIXTURES, PREFIX, ROOT
from tests.explore_fixture import add_core_loop, build

DEVICE = Device()
PACKAGE = "com.example.app"


def box(kind: str, words: str, x: int, y: int, w: int, h: int) -> dict:
    return {"ref": f"@{kind}{x}.{y}", "type": f"android.widget.{kind}", "text": words,
            "coordinates": {"x": x, "y": y, "width": w, "height": h}}


def onboarding(title: str) -> list[dict]:
    return [box("ImageView", "", 140, 400, 800, 800), box("TextView", title, 90, 1300, 900, 120),
            box("TextView", "Learn at your own pace", 90, 1450, 900, 60), box("Button", "Next", 90, 2100, 900, 140)]


def feed(items: list[str]) -> list[dict]:
    """A header over rows of one size; each row's title is as wide as its words, so only the rows repeat."""
    rows = [box("ViewGroup", "", 0, 400 + 300 * n, 1080, 280) for n in range(len(items))]
    titles = [box("TextView", t, 40, 420 + 300 * n, 30 * len(t), 60) for n, t in enumerate(items)]
    return [box("TextView", "For you", 40, 200, 400, 80), *rows, *titles]


def state(sid: str, kind: str = "screen", package: str = PACKAGE) -> StateFile:
    return StateFile(state_id=sid, kind=kind, parent_id=None, fingerprint="", foreground_package=package,
                     screenshot=f"states/{sid}.png", elements_reply=f"states/{sid}.elements.json", settled=True,
                     settle_seconds=0.0, dynamic_regions=[], captured_at="test")


def write_states(explore, screens: list[tuple[str, list[dict]]]) -> None:
    (explore / "states").mkdir(parents=True)
    for n, (kind, tree) in enumerate(screens, start=1):
        sid = f"s{n:02d}"
        (explore / "states" / f"{sid}.json").write_text(state(sid, kind).model_dump_json())
        reply = {"content": [{"type": "text", "text": PREFIX + json.dumps(tree)}]}
        (explore / "states" / f"{sid}.elements.json").write_text(json.dumps(reply))


def line(step: int, **fields) -> ActionLine:
    return ActionLine(**{"step": step, "from_state": "s01", "to_state": "s01", "action": "tap", "mcp_ref": None,
                         "tap_px": None, "transition": "push", "change_summary": "", "outcome": "ok", **fields})


def trace_line(stage: str, step: str, ts: str, **fields) -> str:
    return TraceLine(ts=f"2026-10-03T10:{ts}", stage=stage, step=step, **{"decider": "code", **fields}) \
        .model_dump_json() + "\n"


def manifest(roles: dict[str, str]) -> Manifest:
    return Manifest(run_id="test", app="luzia", created_at="", git_sha="", git_dirty=False, profile="real",
                    budget="transfer", allow_account_create=False, roles=roles, prompt_hashes={},
                    app_package=PACKAGE, app_version=None, mobile_mcp_version=None, playwright_version=None,
                    caps_usd={}, no_send=False, provenance=Provenance(source="fixture"), stages_done=[], usd_total=0.0)


def files(folder) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


# ---------- the identity rule ----------

def test_two_onboarding_pages_with_one_layout_are_two_screens():
    assert scorecard.fixed_texts(onboarding("Welcome"), DEVICE) != scorecard.fixed_texts(onboarding("Track it"), DEVICE)


def test_a_feed_reloaded_with_other_items_is_one_screen():
    first = scorecard.fixed_texts(feed(["Ada", "Grace Hopper", "Linus", "Margaret H."]), DEVICE)
    assert first == scorecard.fixed_texts(feed(["Ken", "Barbara Liskov", "Dennis", "Frances A."]), DEVICE)
    assert first == {"for you"}


def test_numbers_are_masked_and_case_folded():
    hearts = [box("TextView", "3 hearts left", 90, 300, 600, 60)]
    assert scorecard.fixed_texts(hearts, DEVICE) == scorecard.fixed_texts(
        [box("TextView", "5 Hearts left", 90, 300, 600, 60)], DEVICE) == {"# hearts left"}


def test_nested_boxes_of_one_size_are_no_list():
    nested = [box("FrameLayout", "", 0, 200, 1080, 1800) for _ in range(3)]
    assert scorecard.fixed_texts([*nested, *onboarding("Welcome")], DEVICE) == \
        scorecard.fixed_texts(onboarding("Welcome"), DEVICE)


def test_another_package_is_another_screen():
    tree = onboarding("Welcome")
    assert scorecard.identity(state("s01"), tree, DEVICE) != \
        scorecard.identity(state("s02", package="com.android.chrome"), tree, DEVICE)


def test_distinct_screens_counts_screens_only(tmp_path):
    write_states(tmp_path / "explore", [("screen", onboarding("Welcome")), ("screen", onboarding("Track it")),
                                        ("screen", feed(["Ada", "Grace", "Linus"])),
                                        ("screen", feed(["Ken", "Barbara", "Dennis"])), ("sheet", onboarding("Sheet"))])
    record = scorecard.score(tmp_path)["Explore record"]
    assert (record["states saved"], record["screens saved"], record["overlays saved (modal, sheet)"],
            record["distinct screens"]) == (5, 4, 1, 3)


def test_committed_janitorai_feed_reloads_stay_three_screens():
    """The home feed shows two cards in a grid with no card container in the tree, so no box repeats 3 times and each
    reload's card texts count as fixed: s01, s13 and s15 stay apart."""
    explore = ROOT / "runs" / "janitorai" / "20260929-203310-1f19585" / "explore"
    saved = {s.state_id: scorecard.identity(s, tree, DEVICE) for s, tree in scorecard.saved_states(explore)}
    assert len({saved["s01"], saved["s13"], saved["s15"]}) == 3
    assert saved["s09"] == saved["s10"]


# ---------- hidden screen changes and the core loop ----------

def test_hidden_screen_changes_follow_the_spec_and_the_tour_count_leaves_out_the_loop_and_typing():
    lines = [line(1, change_summary="+'Settings'"), line(2), line(3, to_state="s02", change_summary="+'x'"),
             line(4, loop_pass=1, change_summary="reply started 1.0 s, finished 2.0 s, 40 chars"),
             line(5, action="type", change_summary="+'hello'"), line(6, to_state=None, outcome="denied",
                                                                     change_summary="denied: delete")]
    assert [a.step for a in scorecard.hidden_changes(lines)] == [1, 4, 5]
    assert [a.step for a in scorecard.tour_hidden_changes(lines)] == [1]


def test_a_core_loop_move_to_another_page_is_still_a_hidden_change():
    assert len(scorecard.hidden_changes([line(1, loop_pass=1, change_summary="+'Settings page'")])) == 1


def test_committed_luzia_chat_passes_are_hidden_changes_by_the_spec():
    record = scorecard.score(ROOT / "runs" / "luzia" / "20260929-204554-1f19585")["Explore record"]
    assert (record["hidden screen changes"], record["hidden screen changes outside the core loop and typing"]) == (6, 0)


def test_fixture_typing_is_a_hidden_change(tmp_path):
    build("luzia", tmp_path / "explore")
    (tmp_path / "explore" / "actions.jsonl").write_text(
        line(1, action="type", change_summary="+'query'").model_dump_json() + "\n")
    assert scorecard.score(tmp_path)["Explore record"]["hidden screen changes"] == 1


def test_a_pass_completes_when_its_result_shows():
    assert scorecard.completed([line(1, loop_pass=1, change_summary="load started 1.0 s, finished 1.0 s, 30 chars")])
    assert scorecard.completed([line(1, loop_pass=1, loop_stop="limit banner")])
    assert not scorecard.completed([line(1, loop_pass=1, change_summary="no reply within 45 s")])
    assert not scorecard.completed([line(1, loop_pass=1)])


# ---------- the sections, on a run built from fixtures ----------

@pytest.fixture
def run(tmp_path):
    """A luzia run: its fixture explore with two chat passes (the second stopped by a limit), its golden product
    model, a manifest and a trace."""
    folder = tmp_path / "20261003-100000-test"
    build("luzia", folder / "explore")
    add_core_loop(folder / "explore", passes=3, stop_at=2)
    (folder / "model").mkdir()
    shutil.copy(FIXTURES / "golden" / "luzia" / "product_model.json", folder / "model")
    (folder / "manifest.json").write_text(manifest({"jev": "jev-latest", "explore_vision": "claude-sonnet-5-5 low",
                                                    "model_meaning": "claude-opus-5-5 high"}).model_dump_json())
    (folder / "trace.jsonl").write_text("".join([
        trace_line("explore", "icons.s01", "00:00", decider="model", model="claude-sonnet-5-5", usd=0.25),
        trace_line("explore", "rank.s01", "01:00", decider="jev", model="jev-1.13.0", usd=0.001,
                   note="typesafe picked o02 in 0.40s"),
        trace_line("explore", "rank.s02", "02:00", decider="jev", model="jev-1.13.0", cache_hit=True,
                   note="typesafe picked o01 (cached)"),
        trace_line("explore", "core", "03:00", decider="jev", note="core action: send messages"),
        *(trace_line("explore", f"act{n}", f"0{n}:30", note="tap 'Send' s01>s01 [unknown] (core loop: send)")
          for n in (3, 4)),
        trace_line("explore", "done", "06:00"),
        trace_line("model", "meaning", "07:00", decider="model", model="claude-opus-5-5", usd=1.5),
        trace_line("model", "retry", "08:30", decider="model", model="claude-opus-5-5", usd=1.0)]))
    return folder


def test_explore_record_and_core_action(run):
    scored = scorecard.score(run)
    golden = ProductModel.model_validate_json((run / "model" / "product_model.json").read_text())
    record, core = scored["Explore record"], scored["Core action"]
    assert record["states saved"] == len(golden.states)
    assert record["screens saved"] == sum(s.kind == "screen" for s in golden.states)
    assert record["actions ok"] == len((run / "explore" / "actions.jsonl").read_text().splitlines())
    assert core == {"core action found": True, "core passes attempted": 2, "core passes completed": 2}
    assert scored["Hard blocks"] == {"payment sheet in trace lines": 0, "payment sheet states": 0,
                                     "typed actions outside the core loop": 0, "core-loop typed actions": 2,
                                     SENDS: 2}


SENDS = "sends (recorded 'core loop: send')"


def act(note: str, outcome: str = "ok") -> TraceLine:
    return TraceLine(ts="2026-10-03T10:00:00", stage="explore", step="act002", decider="code", outcome=outcome,
                     note=note)


def test_sends_are_read_from_the_recorded_reason_only():
    """Typing then tapping a result, or refocusing the box, sends nothing; a recorded send counts with or without loop
    tags, a denied one never; with no explore trace the row stays unavailable."""
    typed_then_tapped = [line(1, action="type", loop_pass=1), line(2, loop_pass=1, to_state="detail")]
    assert scorecard.hard_blocks(None, typed_then_tapped, []) == {"typed actions outside the core loop": 0,
                                                                  "core-loop typed actions": 1}
    focus = act("tap s01>s01 [unknown] (core loop: focus the text box)")
    assert scorecard.hard_blocks(None, typed_then_tapped, [focus])[SENDS] == 0
    untagged = [line(1, action="type"), line(2)]
    sent, denied = act("tap 'Send' s01>s01 [unknown] (core loop: send)"), act("tap 'Send' (core loop: send)", "denied")
    assert scorecard.hard_blocks(None, untagged, [sent, denied])[SENDS] == 1


def test_product_model_and_checklist(run):
    scored = scorecard.score(run)
    golden = ProductModel.model_validate_json((run / "model" / "product_model.json").read_text())
    model = scored["Product model"]
    assert (model["states"], model["flows"], model["app category"]) == (len(golden.states), len(golden.flows), "chat")
    assert (model["model retry"], model["model needs-human"]) == (True, False)
    assert scored["Checklist"] == {"answered by explore": 0, "open in explore": "none",
                                   "answered by the product model": 3, "open in the product model": "all_tabs, limit",
                                   "settings screen (purpose/name heuristic)": True}


def golden(app: str) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())


def with_elements(pm: ProductModel, changed: dict[str, list[Element]]) -> ProductModel:
    return pm.model_copy(update={"states": [s.model_copy(update={"elements": changed[s.id]}) if s.id in changed else s
                                            for s in pm.states]})


def element(sid: str, suffix: str, x: int, words: str, kind: str = "TextView") -> Element:
    r = Rect(x=x, y=2200, w=240, h=70)
    return Element(id=f"{sid}.{suffix}", mcp_ref=None, type=kind, text=words, label="", source="mcp", rect_px=r,
                   rect_dp=r, role="button", asset_png=None, fg_hex=None, bg_hex=None, font_px=None,
                   font_guess="unknown", in_mock=True, repeat_group=None)


def screen(sid: str, name: str = "Home", purpose: str = "Show the home feed", elements=()) -> State:
    return State(id=sid, kind="screen", parent_id=None, name=name, purpose=purpose, fingerprint="",
                 canonical_png=f"{sid}.png", elements=list(elements), in_mock_scope=True, content_rating="safe",
                 dynamic_regions=[], blocked_reason=None)


def tabs(sid: str) -> list[Element]:
    return [element(sid, f"tab{n}", x, words) for n, (x, words) in enumerate([(40, "Home"), (400, "Search"),
                                                                             (760, "Profile")])]


def tap(sid: str, to: str, element_id: str) -> Edge:
    return Edge(id=f"{element_id}>{to}", from_state=sid, to_state=to, element_id=element_id, action="tap",
                transition="tab", change_summary="")


def product(states: list[State], edges: list[Edge] = ()) -> ProductModel:
    return golden("luzia").model_copy(update={"states": states, "edges": list(edges), "device": DEVICE,
                                              "mechanics": [], "value_ledger": []})


def test_model_checklist_reads_tabs_paywall_settings_and_limit():
    pm = golden("janitorai")
    assert scorecard.model_checklist(pm) == {"root": True, "all_tabs": True, "paywall_or_membership": True,
                                             "settings": False, "limit": False}
    first, *rest = [e for e in pm.edges if e.transition == "tab"]
    assert scorecard.all_tabs(pm.model_copy(update={"edges": [e for e in pm.edges if e not in rest]})) is False


@pytest.mark.parametrize("name,purpose,settings", [
    ("Preferences", "Settings page for configuring app behavior", True),
    ("Preferences", "Controls notification, language, and privacy settings", True),
    ("Settings shortcut", "Account menu containing a link to settings", False),
    ("Side menu", "Account drawer with links (Following, Settings)", False),
    ("Menu", "Main navigation menu for opening settings", False),
    ("Settings shortcut", "Account menu with links to preferences", False),
    ("Settings", "Upgrade row, memories, language, theme, account", True),
    ("Settings", "Onboarding modal; the pet can be turned off in Settings", False),
    ("Settings", "Settings page\nControls notifications", True),
    ("Settings shortcut", "Opens the links\nto settings and help", False)])
def test_settings_are_read_from_the_purpose_not_the_title(name, purpose, settings):
    assert scorecard.settings_screen(screen("s01", name, purpose)) is settings


def test_a_multiline_purpose_scores(tmp_path):
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "product_model.json").write_text(
        product([screen("s01", "Settings", "Settings page\nControls notifications")]).model_dump_json())
    checklist = scorecard.score(tmp_path)["Checklist"]
    assert (checklist["answered by the product model"], checklist["settings screen (purpose/name heuristic)"]) == \
        (2, True)


def test_tabs_are_visited_only_through_the_bars_own_controls():
    """Three tabs on three screens: a tap on Search's own control visits it, a wordless box around Profile visits it,
    Home's own control visits Home, and a named button lying where a tab is visits nothing."""
    holder = element("search", "box", 700, "").model_copy(update={"rect_px": Rect(x=700, y=2150, w=360, h=180)})
    button = element("search", "help", 760, "Help", "Button")
    states = [screen("home", elements=tabs("home")), screen("search", elements=[*tabs("search"), holder, button]),
              screen("profile", elements=tabs("profile")), screen("dialog")]
    walked = [tap("home", "search", "home.tab1"), tap("search", "profile", "search.box"),
              tap("profile", "home", "profile.tab0")]
    assert scorecard.all_tabs(product(states, walked))
    assert scorecard.all_tabs(product(states, [walked[0], walked[2], tap("search", "dialog", "search.help")])) is False


def test_a_parent_named_like_the_tab_it_holds_visits_that_tab():
    """A tab's tap target that carries the tab's own name (a content description over its label) is that tab's
    control; a box named otherwise is not."""
    def parent(sid: str, words: str) -> Element:
        return element(sid, "frame", 700, words, "FrameLayout").model_copy(
            update={"rect_px": Rect(x=700, y=2150, w=360, h=180)})
    states = [screen("home", elements=tabs("home")),
              screen("search", elements=[*tabs("search"), parent("search", "Profile")]),
              screen("profile", elements=tabs("profile"))]
    walked = [tap("home", "search", "home.tab1"), tap("search", "profile", "search.frame"),
              tap("profile", "home", "profile.tab0")]
    assert scorecard.all_tabs(product(states, walked))
    states[1] = screen("search", elements=[*tabs("search"), parent("search", "More")])
    assert scorecard.all_tabs(product(states, walked)) is False


def test_three_tabs_with_one_name_are_three_tabs():
    same = {sid: [e.model_copy(update={"text": "Navigation item"}) for e in tabs(sid)] for sid in ("home", "search")}
    states = [screen("home", elements=same["home"]), screen("search", elements=same["search"])]
    assert len(scorecard.tab_bar(product(states))[0]) == 3
    assert scorecard.all_tabs(product(states, [tap("home", "search", "home.tab1")])) is False
    assert scorecard.all_tabs(product(states, [tap("home", "search", "home.tab1"), tap("search", "home", "search.tab0"),
                                               tap("home", "search", "home.tab2")]))


def test_a_row_of_other_controls_in_the_bars_places_is_no_bar():
    """Another screen has three bottom controls of the tabs' class and size in their columns, with other names: a tap
    on one of them opens a page, not a tab."""
    form = [element("form", f"ctrl{n}", x, words) for n, (x, words) in enumerate([(40, "Cancel"), (400, "Draft"),
                                                                                 (760, "Send")])]
    states = [screen("home", elements=tabs("home")), screen("search", elements=tabs("search")),
              screen("form", elements=form), screen("sent")]
    walked = [tap("home", "search", "home.tab1"), tap("search", "home", "search.tab0"),
              tap("form", "sent", "form.ctrl2")]
    assert scorecard.all_tabs(product(states, walked)) is False
    assert scorecard.all_tabs(product([*states, screen("profile", elements=tabs("profile"))],
                                      [*walked, tap("search", "profile", "search.tab2")]))


def test_a_button_named_like_a_tab_off_the_bar_is_not_its_control():
    """A screen without the bar holds a single bottom button with a tab's name, class and size, in another tab's column
    or in its own: its tap opens a details page, not that tab."""
    states = [screen("home", elements=tabs("home")), screen("search", elements=tabs("search")),
              screen("detail", elements=[element("detail", "profile_button", 40, "Profile")]),
              screen("information", "Information", "Show more information")]
    walked = [tap("home", "search", "home.tab1"), tap("search", "home", "search.tab0"),
              tap("detail", "information", "detail.profile_button")]
    assert scorecard.all_tabs(product(states, walked)) is False
    states[2] = screen("detail", elements=[element("detail", "profile_button", 760, "Profile")])
    assert scorecard.all_tabs(product(states, walked)) is False


def test_a_tap_on_the_starting_tab_that_reloads_it_in_place_counts(tmp_path):
    """The first screen showing the bar is Home: tapping Home's own control there reloads it in place and counts. The
    edges come from stage 2's own reader of the action lines. A same-screen tap counts for the starting screen only."""
    states = [screen(sid, elements=[e.model_copy(update={"mcp_ref": e.id}) for e in tabs(sid)])
              for sid in ("home", "search", "profile")]
    (tmp_path / "actions.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in [
        line(1, from_state="home", to_state="home", mcp_ref="home.tab0", transition="tab", change_summary="reloaded"),
        line(2, from_state="home", to_state="search", mcp_ref="home.tab1", transition="tab"),
        line(3, from_state="search", to_state="profile", mcp_ref="search.tab2", transition="tab")]))
    edges, notes = model.load_edges(tmp_path, states)
    assert (len(edges), notes) == (3, [])
    assert scorecard.all_tabs(product(states, edges))
    elsewhere = [tap("search", "search", "search.tab0"), *edges[1:]]
    assert scorecard.all_tabs(product(states, elsewhere)) is False


def test_returning_to_the_first_tab_does_not_stand_in_for_an_unopened_one():
    """Search's tab then Home's: Home, Search and a reloaded Home were reached, Profile never was."""
    states = [screen("home", elements=tabs("home")), screen("search", elements=tabs("search")),
              screen("home_reload", elements=tabs("home_reload"))]
    assert scorecard.all_tabs(product(states, [tap("home", "search", "home.tab1"),
                                               tap("search", "home_reload", "search.tab0")])) is False


def test_swipes_and_overlays_never_visit_a_tab():
    states = [screen("home", elements=tabs("home")), screen("search", elements=tabs("search")),
              screen("profile", elements=tabs("profile"))]
    walked = [tap("home", "search", "home.tab1"), tap("search", "profile", "search.tab2"),
              tap("profile", "home", "profile.tab0")]
    assert scorecard.all_tabs(product(states, walked))
    swipes = [e.model_copy(update={"action": "swipe"}) for e in walked]
    assert scorecard.all_tabs(product(states, swipes)) is False
    sheet = screen("sign_in", "Sign in", "Account required").model_copy(update={"kind": "sheet", "parent_id": "home"})
    gated = [tap("home", "sign_in", "home.tab1"), tap("search", "sign_in", "search.tab2"), walked[2]]
    assert scorecard.all_tabs(product([*states, sheet], gated)) is False


def test_tabs_found_after_onboarding_are_not_reached_by_default():
    onboarding = screen("welcome", "Welcome", "Introduce the app")
    assert scorecard.all_tabs(product([onboarding, screen("home", elements=tabs("home"))])) is False
    assert scorecard.all_tabs(product([onboarding, screen("home", elements=tabs("home")),
                                       screen("search", elements=tabs("search"))])) is False


def test_no_tab_bar_seen_is_not_answered():
    pm = product([screen("welcome", "Welcome", "Introduce the app"), screen("next", "Goal", "Pick a goal")])
    assert scorecard.tab_bar(pm) == ([], False)
    assert scorecard.all_tabs(pm) is None
    assert scorecard.checklist(None, pm)["open in the product model"] == \
        "all_tabs (no tab bar seen), paywall_or_membership, settings, limit"


def test_a_composer_row_is_no_tab_bar():
    pm = golden("luzia")
    bar, confirmed = scorecard.tab_bar(pm)
    assert (len(bar), confirmed) == (3, True)
    uneven = [e.model_copy(update={"rect_px": e.rect_px.model_copy(update={"x": e.rect_px.x - 100})})
              if e.id == bar[0].id else e for e in pm.states[0].elements]
    assert scorecard.tab_bar(with_elements(pm, {"s01": uneven}))[0] != bar


def test_a_screens_own_pair_of_bottom_buttons_is_an_unconfirmed_tab_bar():
    """Two named buttons of one size, evenly spread at the bottom of one screen: a candidate tab bar, confirmed only
    when another screen shows them there too. Unconfirmed, all_tabs stays open rather than credit tabs no one opened."""
    pair = [element("form", "cancel", 60, "Cancel", "Button"), element("form", "save", 620, "Save", "Button")]
    alone = product([screen("form", elements=pair), screen("done")])
    assert scorecard.tab_bar(alone) == (pair, False)
    assert scorecard.all_tabs(alone) is False
    assert scorecard.tab_bar(product([screen("form", elements=pair), screen("done", elements=pair)])) == (pair, True)


def test_committed_aol_two_tab_bar_is_found_and_walked():
    pm = ProductModel.model_validate_json(
        (ROOT / "runs" / "aol" / "20260929-205304-1f19585" / "model" / "product_model.json").read_text())
    bar, confirmed = scorecard.tab_bar(pm)
    assert (len(bar), confirmed) == (2, True)
    assert scorecard.all_tabs(pm)


def test_filter_verdicts(run):
    assert scorecard.score(run)["Filter"] == {"filter verified": "none found", "filter checks": 0,
                                              "filter checks passed": 0}
    explore = ExploreFile.model_validate_json((run / "explore" / "explore.json").read_text())
    (run / "explore" / "explore.json").write_text(explore.model_copy(update={"content_filter": "Safe"})
                                                 .model_dump_json())
    with open(run / "trace.jsonl", "a") as f:
        f.write(trace_line("explore", "filter.check", "04:00", note="'Safe' verified by screenshot (check 1)"))
    assert scorecard.score(run)["Filter"]["filter verified"] == "yes"
    with open(run / "trace.jsonl", "a") as f:
        f.write(trace_line("explore", "filter.check", "05:00", outcome="error",
                           note="'Safe' NOT verified by screenshot (check 2)"))
    assert scorecard.score(run)["Filter"] == {"filter verified": "no", "filter checks": 2, "filter checks passed": 1}


def test_cost_and_time(run):
    cost = scorecard.score(run)["Cost and time"]
    distinct = scorecard.score(run)["Explore record"]["distinct screens"]
    assert cost["$ total"] == pytest.approx(2.751)
    assert (cost["$ explore"], cost["$ model"]) == (pytest.approx(0.251), pytest.approx(2.5))
    assert (cost["minutes explore"], cost["minutes model"]) == (6.0, 1.5)
    assert (cost["model calls explore_vision"], cost["model calls model_meaning"]) == (1, 2)
    assert (cost["Jev calls"], cost["Jev seconds"], cost["Jev adapter calls"], cost["Jev adapter seconds"]) == \
        (1, 0.4, 0, 0)
    assert cost["explore $ per distinct screen"] == pytest.approx(0.251 / distinct)


@pytest.mark.parametrize("stage,step,expected", [
    ("propose", "judge:revisions", "propose_dedupe"), ("propose", "judge:revisions:r3", "propose_dedupe"),
    ("judge", "revise:c01", "proposer"), ("judge", "judge:c01:judge_1:r1", "judge_1"),
    ("judge", "judge:c01:judge_2:r1", "judge_2"), ("qa", "critic r1 g1", "qa_critic"), ("qa", "fixer r1", "qa_fixer"),
    ("propose", "dedupe:topup", "propose_dedupe"), ("propose", "lens:utility", "proposer"),
    ("propose", "topup", "proposer"), ("explore", "agent.turn", "explore_agent"),
    ("explore", "icons.s01", "explore_vision"), ("explore", "arrival.s09", "explore_vision"),
    ("explore", "replay.s02", "explore_vision"), ("explore", "settle", "explore_vision")])
def test_roles_sharing_a_model_keep_their_own_counts(stage, step, expected):
    roles = {r: "same-model high" for rs in scorecard.ROLES.values() for r in rs if r != "jev"}
    trace = [TraceLine(ts="2026-10-03T10:00:00", stage=stage, step=step, decider="model", model="same-model")]
    assert scorecard.cost_and_time(trace, manifest(roles), None, None)[f"model calls {expected}"] == 1


def test_the_agents_planner_turns_count_as_explore_agent_when_it_shares_vision_s_model():
    """Run 5: 80 planner turns and the icon passes on one Sonnet landed in 'model calls explore claude-sonnet-5-5'."""
    roles = {"explore_vision": "claude-sonnet-5-5 low", "explore_agent": "claude-sonnet-5-5 medium"}
    steps = ["agent.turn"] * 3 + ["icons.s01", "icons.s02", "arrival.s09", "settle"]
    trace = [TraceLine(ts=f"2026-10-03T10:00:0{n}", stage="explore", step=step, decider="model",
                       model="claude-sonnet-5-5") for n, step in enumerate(steps)]
    cost = scorecard.cost_and_time(trace, manifest(roles), None, None)
    assert (cost["model calls explore_agent"], cost["model calls explore_vision"]) == (3, 4)
    assert not [k for k in cost if k.startswith("model calls explore claude")]

def test_minutes_span_the_earliest_to_the_latest_time_whatever_the_append_order():
    lines = [TraceLine(ts=f"2026-10-03T10:0{m}:00", stage="qa", step="critic", decider="code") for m in (2, 0, 1)]
    assert scorecard.cost_and_time(lines, None, None, None)["minutes qa"] == 2


def test_adapter_calls_are_not_typesafe_jev_calls():
    trace = [TraceLine(ts="2026-10-03T10:00:00", stage="explore", step="rank.s01", decider="jev",
                       model="claude-haiku-4-5-20251001", usd=0.1, note="adapter picked o01 in 1.00s"),
             TraceLine(ts="2026-10-03T10:00:30", stage="explore", step="rank.s02", decider="jev", model="jev-latest",
                       outcome="retry", note="TimeoutError: no answer")]
    cost = scorecard.cost_and_time(trace, None, None, None)
    assert (cost["Jev calls"], cost["Jev seconds"], cost["Jev adapter calls"], cost["Jev adapter seconds"]) == \
        (1, 0, 1, 1.0)


def test_an_empty_folder_scores_blank(tmp_path):
    assert all(section == {} for section in scorecard.score(tmp_path).values())


def test_a_run_missing_stages_leaves_blanks(run, tmp_path):
    bare = tmp_path / "bare"
    (bare / "explore").mkdir(parents=True)
    shutil.copytree(run / "explore" / "states", bare / "explore" / "states")
    scored = scorecard.score(bare)
    assert scored["Product model"] == scored["Downstream"] == scored[scorecard.JUDGE] == {}
    assert scored["Cost and time"] == scored["Core action"] == scored["Filter"] == {}
    assert scored["Explore record"]["states saved"] == scorecard.score(run)["Explore record"]["states saved"]
    table = scorecard.table([("full", scorecard.score(run)), ("bare", scored)])
    assert table.splitlines()[0] == "| metric | full | bare | min | max |"
    assert "| states | 7 |  | 7 | 7 |" in table.splitlines()


def test_committed_run_downstream_and_judge():
    run = ROOT / "runs" / "janitorai" / "20260929-203310-1f19585"
    scored = scorecard.score(run)
    assert scored["Downstream"] == {"mock contract passed": True, "mock screens in scope": 15, "mock screens drawn": 15,
                                    "QA approved round": 2,
                                    "QA score": 9.563, "QA mean masked SSIM": 0.821, "QA bounds share": 1.0,
                                    "propose drafts": 8, "propose distinct ideas kept": 4}
    assert scored[scorecard.JUDGE] == {"judge accept": 2, "judge conditional": 2, "judge reject": 6,
                                       "judge needs_human": 0}
    assert scored["Filter"] == {"filter verified": "yes", "filter checks": 4, "filter checks passed": 4}


def test_committed_aol_drawn_screens_leave_out_the_undrawn():
    downstream = scorecard.downstream(ROOT / "runs" / "aol" / "20260929-205304-1f19585")
    assert (downstream["mock contract passed"], downstream["mock screens in scope"],
            downstream["mock screens drawn"]) == (False, 57, 32)


# ---------- inventory ----------

INVENTORY = """
[[screens]]
name = "Welcome"
texts = ["welcome", "Next"]

[[screens]]
name = "Paywall"
texts = ["per month"]

[[screens]]
name = "Welcome paywall"
texts = ["Welcome", "per month"]
"""


def test_inventory_coverage(tmp_path):
    write_states(tmp_path / "explore", [("screen", onboarding("Welcome")), ("sheet", feed(["a", "bb", "ccc"]))])
    (tmp_path / "inventory.toml").write_text(INVENTORY)
    inventory = scorecard.load_inventory(tmp_path / "inventory.toml")
    assert scorecard.score(tmp_path, inventory)["Inventory"] == {
        "inventory screens": 3, "inventory screens matched": 1, "inventory coverage": pytest.approx(1 / 3)}


@pytest.mark.parametrize("broken", ['[[screens]]\nname = "Paywall"\ntexts = []\n',
                                    '[[screens]]\nname = "Paywall"\nwords = ["per month"]\n', 'screens = []\n'])
def test_inventory_refuses_a_malformed_file(tmp_path, broken):
    (tmp_path / "inventory.toml").write_text(broken)
    with pytest.raises(ValidationError):
        scorecard.load_inventory(tmp_path / "inventory.toml")


# ---------- the command ----------

def test_command_prints_one_column_per_run_and_never_writes_into_them(run, tmp_path, capsys):
    other = tmp_path / "other"
    shutil.copytree(run, other)
    before = files(run) | files(other)
    out = tmp_path / "scores.json"
    assert cli.main(["scorecard", str(run), str(other), "--json", str(out)]) == 0
    head = capsys.readouterr().out.splitlines()[0]
    assert head == f"| metric | luzia {run.name} | luzia other | min | max |"
    assert files(run) | files(other) == before
    assert json.loads(out.read_text())[str(run)]["Core action"]["core passes attempted"] == 2


def test_run_labels_are_escaped_in_the_header():
    assert scorecard.table([("a|b", {"Explore record": {"states saved": 1}})]).splitlines()[0] == "| metric | a/b |"


def test_command_refuses_to_write_json_into_a_run(run):
    with pytest.raises(SystemExit, match="inside a run folder"):
        cli.main(["scorecard", str(run), "--json", str(run / "scores.json")])
