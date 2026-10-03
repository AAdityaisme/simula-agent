"""The scorecard: its identity rule, hidden screen changes, every section read from runs built with
tests/explore_fixture.py, a run missing stages, inventory matching, and that it never writes into a run."""

import hashlib
import json
import shutil

import pytest
from pydantic import ValidationError

from simula import cli, scorecard
from simula.contracts import (ActionLine, Device, Edge, Element, ExploreFile, Manifest, ProductModel, Provenance, Rect,
                              StateFile, TraceLine)
from simula.device import observe as ob
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

def test_hidden_screen_changes_are_same_state_moves_that_changed_the_screen():
    lines = [line(1, change_summary="+'Settings'"), line(2), line(3, to_state="s02", change_summary="+'x'"),
             line(4, loop_pass=1, change_summary="reply started 1.0 s, finished 2.0 s, 40 chars"),
             line(5, action="type", change_summary="+'hello'"), line(6, to_state=None, outcome="denied",
                                                                     change_summary="denied: delete")]
    assert [a.step for a in scorecard.hidden_changes(lines)] == [1]


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
                                     "typed actions": 2, "sent actions (a tap right after typing)": 2}


def test_product_model_and_checklist(run):
    scored = scorecard.score(run)
    golden = ProductModel.model_validate_json((run / "model" / "product_model.json").read_text())
    model = scored["Product model"]
    assert (model["states"], model["flows"], model["app category"]) == (len(golden.states), len(golden.flows), "chat")
    assert (model["model retry"], model["model needs-human"]) == (True, False)
    assert scored["Checklist"] == {"answered by explore": 0, "open in explore": "none",
                                   "answered by the product model": 4, "open in the product model": "limit"}


def test_model_checklist_reads_tabs_paywall_settings_and_limit():
    pm = ProductModel.model_validate_json((FIXTURES / "golden" / "janitorai" / "product_model.json").read_text())
    assert scorecard.model_checklist(pm) == {"root": True, "all_tabs": True, "paywall_or_membership": True,
                                             "settings": False, "limit": False}
    first, *rest = [e for e in pm.edges if e.transition == "tab"]
    one_left = pm.model_copy(update={"edges": [e for e in pm.edges if e not in rest]})
    assert scorecard.all_tabs(one_left) is False
    named = pm.model_copy(update={"states": [pm.states[0].model_copy(update={"name": "App settings"}),
                                             *pm.states[1:]]})
    assert scorecard.model_checklist(named)["settings"]


def golden(app: str) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())


def with_elements(pm: ProductModel, changed: dict[str, list[Element]]) -> ProductModel:
    return pm.model_copy(update={"states": [s.model_copy(update={"elements": changed[s.id]}) if s.id in changed else s
                                            for s in pm.states]})


def test_a_composer_row_is_no_tab_bar():
    pm = golden("luzia")
    tabs, confirmed = scorecard.tab_bar(pm)
    assert (len(tabs), confirmed) == (3, True)
    uneven = [e.model_copy(update={"rect_px": e.rect_px.model_copy(update={"x": e.rect_px.x - 100})})
              if e.rect_px == tabs[0] else e for e in pm.states[0].elements]
    assert scorecard.tab_bar(with_elements(pm, {"s01": uneven})) == ([], False)


def test_a_screens_own_pair_of_bottom_buttons_is_an_unconfirmed_tab_bar():
    """Two named buttons of one size, evenly spread at the bottom of the root: a candidate tab bar, confirmed only when
    another screen shows them there too. Unconfirmed, all_tabs stays open rather than credit tabs no one opened."""
    pm = golden("luzia")
    root, other = pm.states[0], pm.states[1]
    band = DEVICE.content_bottom_px - ob.TAB_BAND_PX
    buttons = [root.elements[0].model_copy(update={"id": f"s01.b{n}", "type": "Button", "text": words, "label": "",
                                                   "rect_px": Rect(x=x, y=2150, w=400, h=140)})
               for n, (words, x) in enumerate((("Cancel", 60), ("Save", 620)))]
    alone = with_elements(pm, {"s01": [e for e in root.elements if e.rect_px.y < band] + buttons})
    assert scorecard.tab_bar(alone) == ([b.rect_px for b in buttons], False)
    assert scorecard.all_tabs(alone) is False
    shared = with_elements(alone, {other.id: other.elements + buttons})
    assert scorecard.tab_bar(shared) == ([b.rect_px for b in buttons], True)


def test_a_root_only_tab_bar_in_a_run_that_never_left_the_root_stays_open():
    pm = golden("luzia")
    band = DEVICE.content_bottom_px - ob.TAB_BAND_PX
    root_only = with_elements(pm, {s.id: [e for e in s.elements if e.rect_px.y < band] for s in pm.states[1:]})
    assert scorecard.tab_bar(root_only)[1] is False
    assert scorecard.all_tabs(root_only.model_copy(update={"edges": []})) is False


def test_a_root_with_no_candidate_row_has_all_tabs_answered():
    pm = golden("luzia")
    band = DEVICE.content_bottom_px - ob.TAB_BAND_PX
    bare = with_elements(pm, {"s01": [e for e in pm.states[0].elements if e.rect_px.y < band]})
    assert scorecard.tab_bar(bare) == ([], False)
    assert scorecard.all_tabs(bare.model_copy(update={"edges": []}))


def test_committed_aol_two_tab_bar_is_found():
    pm = ProductModel.model_validate_json(
        (ROOT / "runs" / "aol" / "20260929-205304-1f19585" / "model" / "product_model.json").read_text())
    tabs, confirmed = scorecard.tab_bar(pm)
    assert (len(tabs), confirmed) == (2, True)


def test_a_tap_back_to_the_roots_tab_never_counts():
    """A three-tab app: the root shows its own tab (the leftmost), s05's tab was opened, then the root's tab again from
    s05; the third tab (s06's) was never opened."""
    pm = golden("luzia")
    own = min(scorecard.tab_bar(pm)[0], key=lambda r: r.x)
    s05 = next(s for s in pm.states if s.id == "s05")
    home = next(e for e in s05.elements if own.x <= ob.center(e.rect_px)[0] < own.x + own.w
                and e.rect_px.y >= DEVICE.content_bottom_px - ob.TAB_BAND_PX)
    back = Edge(id="s05.home>s01", from_state="s05", to_state="s01", element_id=home.id, action="tap",
                transition="tab", change_summary="")
    assert scorecard.all_tabs(pm)
    assert scorecard.all_tabs(pm.model_copy(update={"edges": [*(e for e in pm.edges if e.id != "s01.e43>s06"),
                                                              back]})) is False


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
    assert (cost["Jev calls"], cost["Jev seconds"]) == (1, 0.4)
    assert cost["explore $ per distinct screen"] == pytest.approx(0.251 / distinct)


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
    assert scored["Downstream"] == {"mock contract passed": True, "mock screens drawn": 15, "QA approved round": 2,
                                    "QA score": 9.563, "QA mean masked SSIM": 0.821, "QA bounds share": 1.0,
                                    "propose drafts": 8, "propose distinct ideas kept": 4}
    assert scored[scorecard.JUDGE] == {"judge accept": 2, "judge conditional": 2, "judge reject": 6,
                                       "judge needs_human": 0}
    assert scored["Filter"] == {"filter verified": "yes", "filter checks": 4, "filter checks passed": 4}


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
