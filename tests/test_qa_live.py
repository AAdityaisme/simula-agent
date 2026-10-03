"""simula qa-live on a fake phone and the offline builder's mock: each hop is taken on both sides and paired, the walk
stops at the first divergence and names the side, a target it can't verify is never tapped, setup is never evidence,
and the caps, the lock and the source run hold. No model call and no real device anywhere."""

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from simula import cli, decide, llm, qa_live, runfolder, runlog
from simula.contracts import (ActionLine, ContractError, ContractReport, Coverage, Device, ExploreFile, FilterControl,
                              Flow, Point, ProductModel, Provenance, Rect, StageOutcome, StateFile)
from simula.device import mcp
from simula.device import observe as ob
from simula.stages import model as model_stage
from simula.stages import qa
from tests.fake_device import PACKAGE, PREFIX, Clock, FakePhone, Screen, capture, element_key
from tests.mock_fake import skeleton_html

DEVICE = Device()
CAPTURES = {"s01": "j04_tab1", "s02": "j05_tab2", "s03": "j07_tab4", "s04": "j08_tab5"}
TAP, BACK, SWIPE = "tap", "back", "swipe"


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the live walk made a model call")
    monkeypatch.setattr(llm, "call", refuse)
    monkeypatch.setattr(decide, "ask_choice", refuse)


def screens() -> dict[str, Screen]:
    return {sid: capture("janitorai", name) for sid, name in CAPTURES.items()}


def tab(screen: Screen, n: int) -> ob.Candidate:
    return ob.tab_bar(ob.controls(screen.elements, DEVICE), DEVICE)[n]


def labeled(screen: Screen, label: str) -> ob.Candidate:
    return next(c for c in ob.controls(screen.elements, DEVICE) if c.label == label)


def line(step: int, src: str, to: str, action: str, cand: ob.Candidate | None = None, transition: str = "push",
         summary: str = "") -> ActionLine:
    return ActionLine(step=step, from_state=src, to_state=to, action=action, mcp_ref=cand.ref if cand else None,
                      tap_px=Point(x=cand.point[0], y=cand.point[1]) if cand else None, transition=transition,
                      change_summary=summary, outcome="ok")


def source_run(runs: Path, recorded: dict[str, Screen], lines: list[ActionLine],
               flows: list[list[tuple[str, str, str]]], content_filter: str | None = None,
               filter_controls: list[FilterControl] = (), filter_on: bool | None = None) -> Path:
    """A run folder built the way explore and the model stage build one: explore/states and actions.jsonl from
    captures, the product model's code facts with every state in scope and one flow per list of (from, to, action)
    hops, and an approved mock the offline builder drew."""
    run_dir = runs / "janitorai" / "r1"
    explore_dir, model_dir = run_dir / "explore", run_dir / "model"
    (explore_dir / "states").mkdir(parents=True)
    (model_dir / "assets").mkdir(parents=True)
    for sid, screen in recorded.items():
        reply = {"content": [{"type": "text", "text": PREFIX + json.dumps(screen.elements)}]}
        (explore_dir / "states" / f"{sid}.elements.json").write_text(json.dumps(reply))
        screen.image.save(explore_dir / "states" / f"{sid}.png")
        fp = ob.fingerprint(PACKAGE, screen.elements, screen.image, DEVICE)
        (explore_dir / "states" / f"{sid}.json").write_text(StateFile(
            state_id=sid, kind="screen", parent_id=None, fingerprint=str(fp), foreground_package=PACKAGE,
            screenshot=f"states/{sid}.png", elements_reply=f"states/{sid}.elements.json", settled=True,
            settle_seconds=2.0, dynamic_regions=[], captured_at="now").model_dump_json())
    (explore_dir / "actions.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in lines))
    coverage = Coverage(states_found=len(recorded), actions_taken=len(lines), stop_reason="done", checklist_answered=[],
                        checklist_open=[])
    (explore_dir / "explore.json").write_text(ExploreFile(
        app_package=PACKAGE, app_version="1", budget="transfer", relaunches=0, content_filter=content_filter,
        filter_controls=list(filter_controls), filter_on=filter_on, blocked_state_ids=[],
        coverage=coverage).model_dump_json())
    states, images, _ = model_stage.load_states(explore_dir, DEVICE)
    edges, _ = model_stage.load_edges(explore_dir, states)
    ids, tapped = [s.id for s in states], {e.element_id for e in edges if e.element_id}
    states = [model_stage.finish_elements(s, set(ids), tapped, images[s.id], model_dir, DEVICE) for s in states]
    by_hop = {(e.from_state, e.to_state, e.action): e.id for e in edges}
    model = ProductModel(
        app="janitorai", app_version="1", app_category="chat", run_id="r1", device=DEVICE, states=states, edges=edges,
        flows=[Flow(id=f"f{n}", name=f"flow {n}", purpose="", edge_ids=[by_hop[h] for h in hops], evidence_ids=[])
               for n, hops in enumerate(flows, 1)],
        mechanics=[], cross_screen_values=[], value_ledger=[], open_questions=[],
        coverage=Coverage(states_found=len(states), actions_taken=len(lines), stop_reason="done",
                          checklist_answered=[], checklist_open=[]),
        provenance=Provenance(source="explorer_run"), mock_order=ids)
    (model_dir / "product_model.json").write_text(model.model_dump_json())
    approved = run_dir / "qa" / "approved"
    shutil.copytree(model_dir / "assets", approved / "assets")
    (approved / "index.html").write_text(qa.rebuild(skeleton_html(model), model, ids))
    (run_dir / "mock").mkdir()
    (run_dir / "mock" / "contract_report.json").write_text(
        ContractReport(passed=True, screens=ids, errors=[]).model_dump_json())
    runfolder.write_done(model_dir, run_dir, [explore_dir], [], {}, [model_dir], Provenance(source="explorer_run"))
    approve(run_dir)
    return run_dir


def approve(run_dir: Path, outcome: StageOutcome | None = None) -> None:
    """QA's marker over the page it approved, as the stage writes one: the model and mock it read, and qa/ it wrote."""
    runfolder.write_done(run_dir / "qa", run_dir, [run_dir / "mock", run_dir / "model"], [], {}, [run_dir / "qa"],
                         Provenance(source="explorer_run"), outcome=outcome)


def scroll_back(run_dir: Path, a: ActionLine) -> None:
    """The trace line explore writes for a move it made to scroll back (Explorer.log, Move why "scroll back")."""
    runlog.run_trace(run_dir, stage="explore", step=f"act{a.step:03d}", decider="code",
                     note=f"swipe {a.from_state}>{a.to_state} [push]  (scroll back)")


def key_at(screen: Screen, point: tuple[int, int]) -> str:
    """The fake phone's name for what a tap at point hits: the smallest element there."""
    x, y = point
    holding = [e for e in screen.elements if e["coordinates"]["x"] <= x < e["coordinates"]["x"] + e["coordinates"]
               ["width"] and e["coordinates"]["y"] <= y < e["coordinates"]["y"] + e["coordinates"]["height"]]
    return element_key(min(holding, key=lambda e: e["coordinates"]["width"] * e["coordinates"]["height"]))


def phone_for(live: dict[str, Screen], taps: list[tuple[str, ob.Candidate, str]], cls=FakePhone,
              **kwargs) -> FakePhone:
    """A fake phone on these screens, starting on s01; taps: (screen, control, where a tap on it goes)."""
    mapping = {(sid, key_at(live[sid], cand.point)): to for sid, cand, to in taps}
    return cls(screens=dict(live), start="s01", taps=mapping, clock=Clock(), **kwargs)


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(folder)).encode() + path.read_bytes())
    return h.hexdigest()


class FakeServer:
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def walk(runs, tmp_path, monkeypatch):
    """Runs the audit on a fake phone, with a fake server where mobile-mcp would start and a lock that records being
    held and released."""
    held, servers = [], []

    @contextlib.contextmanager
    def lock(serial, wait_s):
        held.append("held")
        try:
            yield
        finally:
            held.append("released")

    def go(phone: FakePhone, out: Path | None = None) -> dict:
        monkeypatch.setattr(qa_live, "Server", lambda cwd: servers.append(FakeServer()) or servers[-1])
        monkeypatch.setattr(qa_live, "Phone", lambda *args, **kwargs: phone)
        return qa_live.run("janitorai", "r1", out or tmp_path / "audit", None, clock=phone.clock,
                           sleep=phone.clock.sleep)
    monkeypatch.setattr(qa_live, "emulator_lock", lock)
    go.held, go.servers = held, servers
    return go


def taps(phone: FakePhone) -> list[tuple]:
    return [entry for entry in phone.log if entry[0] == "tap"]


def tab_back_run(runs: Path, recorded: dict[str, Screen], content_filter: str | None = None, **filter_) -> Path:
    """One flow: tap s01's second tab to s02, then BACK to s01."""
    return source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
                                       line(2, "s02", "s01", BACK, transition="back")],
                      [[("s01", "s02", TAP), ("s02", "s01", BACK)]], content_filter, **filter_)


def test_a_tap_and_back_flow_is_walked_on_both_sides_with_a_redacted_paired_checkpoint_per_hop(runs, walk, tmp_path,
                                                                                               monkeypatch):
    monkeypatch.setenv("SIMULA_REDACT", "Trending")
    recorded = screens()
    run_dir = tab_back_run(runs, recorded)
    before = digest(run_dir)
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])

    report = walk(phone)

    flow = report["flows"][0]
    assert (flow["status"], flow["reason"]) == ("matched", None)
    assert [(c["action"], c["expected"], c["live"]["verdict"], c["mock"]["landed"], c["mock"]["problem"])
            for c in flow["checkpoints"]] == [("start", "s01", "same", "s01", None),
                                              ("tap", "s02", "same", "s02", None),
                                              ("back", "s01", "same", "s01", None)]
    out = tmp_path / "audit"
    for c in flow["checkpoints"]:
        assert c["ssim"] is not None and 0 < c["coverage"] <= 1
        assert all((out / path).exists() for path in c["files"].values())
    assert flow["checkpoints"][1]["control"]["within"]
    start_tree = (out / flow["checkpoints"][0]["files"]["tree"]).read_text()
    assert "Trending" not in start_tree and ob.REDACTED in start_tree
    coverage = [c["coverage"] for c in flow["checkpoints"]]
    assert report["summary"] | {"by_status": None} == {"total": 1, "supported": 1, "completed": 1, "by_status": None,
                                                        "checkpoints": 3,
                                                        "mean_coverage": round(sum(coverage) / len(coverage), 3)}
    assert report["actions"] == {"total": 4, "setup": 2} and [s["action"] for s in report["setup"]] == ["terminate",
                                                                                                         "launch"]
    assert len(taps(phone)) == 1 and phone.log.count(("launch",)) == 1
    assert "| f1: flow 1 | matched | 3 |" in (out / "report.md").read_text()
    assert json.loads((out / "report.json").read_text())["inputs"].keys() == set(qa_live.INPUTS)
    assert digest(run_dir) == before and not (out / ".scratch").exists()
    assert walk.held == ["held", "released"] and walk.servers[0].closed


def test_a_live_tap_that_lands_elsewhere_is_the_app_diverging_even_though_the_captures_are_identical(runs, walk):
    recorded = screens()
    tab_back_run(runs, recorded)
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s03")])

    flow = walk(phone)["flows"][0]

    assert flow["status"] == "real_diverged" and "the app went to s03, not s02" in flow["reason"]
    assert flow["checkpoints"][-1]["live"]["landed"] == "s03" and flow["checkpoints"][-1]["mock"]["landed"] == "s02"
    assert ("back", "s03") not in phone.log


def test_a_flow_that_starts_where_the_last_one_stopped_judges_that_look_against_its_own_start(runs, walk):
    """f1 diverges onto s03, where f2 starts: f2 starts there with no launch, and its start checkpoint is judged
    against s03, not against the s02 f1 wanted."""
    recorded = screens()
    lines = [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
             line(2, "s03", "s01", TAP, tab(recorded["s03"], 0), "tab")]
    source_run(runs, recorded, lines, [[("s01", "s02", TAP)], [("s03", "s01", TAP)]])
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s03"), ("s03", tab(recorded["s03"], 0), "s01")])

    report = walk(phone)

    assert [f["status"] for f in report["flows"]] == ["real_diverged", "matched"]
    start = report["flows"][1]["checkpoints"][0]
    assert (start["expected"], start["live"]["expected"], start["live"]["verdict"]) == ("s03", "s03", "same")
    assert phone.log.count(("launch",)) == 1


def test_a_state_with_no_fingerprint_is_never_walked_to_and_the_rest_of_the_run_still_is(runs, walk):
    """A state file converted from #33's format has an empty fingerprint: the walker can't tell that screen, so a flow
    through it is unsupported and no route passes it, and every other flow is still walked."""
    recorded = screens()
    lines = [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
             line(2, "s02", "s01", BACK, transition="back"),
             line(3, "s01", "s03", TAP, tab(recorded["s01"], 3), "tab"),
             line(4, "s03", "s04", TAP, tab(recorded["s03"], 4), "tab"),
             line(5, "s04", "s01", TAP, tab(recorded["s04"], 0), "tab")]
    run_dir = source_run(runs, recorded, lines, [[("s01", "s02", TAP), ("s02", "s01", BACK)], [("s04", "s01", TAP)],
                                                 [("s01", "s03", TAP)]])
    path = run_dir / "model" / "product_model.json"
    model = ProductModel.model_validate_json(path.read_text())
    path.write_text(model.model_copy(update={"states": [s.model_copy(update={"fingerprint": ""}) if s.id == "s03" else s
                                                        for s in model.states]}).model_dump_json())
    approve(run_dir)
    through = next(e.id for e in model.edges if (e.from_state, e.to_state) == ("s01", "s03"))
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02"), ("s01", tab(recorded["s01"], 3), "s03"),
                                  ("s03", tab(recorded["s03"], 4), "s04"), ("s04", tab(recorded["s04"], 0), "s01")])

    report = walk(phone)

    assert [(f["status"], f["reason"]) for f in report["flows"]] == [
        ("matched", None),
        ("blocked", "setup: no recorded route of at most 4 hops from s01 to s04"),
        ("unsupported", f"{through} passes a screen explore recorded no fingerprint for")]
    assert len(taps(phone)) == 1


def test_a_mock_that_lost_the_hops_tag_is_the_mock_failing(runs, walk):
    recorded = screens()
    run_dir = tab_back_run(runs, recorded)
    page = run_dir / "qa" / "approved" / "index.html"
    edge = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text()).flows[0].edge_ids[0]
    assert f'data-edge="{edge}"' in page.read_text()
    page.write_text(page.read_text().replace(f'data-edge="{edge}"', ""))
    approve(run_dir)  # QA approved the page as it is
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])

    flow = walk(phone)["flows"][0]

    assert flow["status"] == "mock_failed" and flow["reason"] == f"{edge}: mock: no data-edge tag on its screen"
    assert flow["checkpoints"][-1]["live"]["verdict"] == "same"


def without(screen: Screen, c: ob.Candidate) -> Screen:
    return Screen([e for e in screen.elements if not ob.inside(ob.rect(e), c.rect)], screen.image, screen.package)


def with_extra(screen: Screen, element: dict) -> Screen:
    return Screen([*screen.elements, element], screen.image, screen.package)


def painted(screen: Screen, c: ob.Candidate) -> Screen:
    image = screen.image.copy()
    r = c.rect
    ImageDraw.Draw(image).rectangle((r.x, r.y, r.x + r.w, r.y + r.h), fill=(250, 0, 200))
    return Screen(screen.elements, image, screen.package)


def box(ref: str, x: float, y: float, w: float, h: float, kind: str = "ViewGroup", text: str = "") -> dict:
    return {"ref": ref, "type": f"android.view.{kind}", "text": text, "label": "",
            "coordinates": {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}}


@pytest.mark.parametrize("change, why", [
    (lambda s, c: without(s, c), "is not on the live screen"),
    (lambda s, c: with_extra(s, box("@x1", c.rect.x + 20, c.rect.y, c.rect.w, c.rect.h)), "2 live controls fit"),
    (lambda s, c: with_extra(s, box("@x1", c.rect.x + 40, c.rect.y + 40, 300, 300, "TextView", "Promo")),
     "something lies over"),
    (lambda s, c: painted(s, c), "looks different from its recorded crop"),
], ids=["missing", "ambiguous", "covered", "looks-different"])
def test_a_target_the_walker_cant_verify_is_never_tapped(runs, walk, change, why):
    recorded = screens()
    tab_back_run(runs, recorded)
    target = tab(recorded["s01"], 1)
    live = screens() | {"s01": change(screens()["s01"], target)}
    phone = phone_for(live, [("s01", target, "s02")])

    flow = walk(phone)["flows"][0]

    assert flow["status"] == "unsupported" and why in flow["reason"]
    assert [c["action"] for c in flow["checkpoints"]] == ["start"] and taps(phone) == []


def with_send() -> dict[str, Screen]:
    """The captures with s01's "Trending" chip relabeled "Send", as a send recorded in explore's core loop."""
    found = screens()
    s01 = found["s01"]
    found["s01"] = Screen([{k: "Send" if v == "Trending" else v for k, v in e.items()} for e in s01.elements],
                          s01.image, s01.package)
    return found


def test_a_control_on_the_deny_list_is_never_tapped(runs, walk):
    """A send recorded in explore's core loop is denied outside it, as on every tour tap."""
    recorded = with_send()
    send = labeled(recorded["s01"], "Send")
    source_run(runs, recorded, [line(1, "s01", "s02", TAP, send)], [[("s01", "s02", TAP)]])
    phone = phone_for(with_send(), [("s01", send, "s02")])

    flow = walk(phone)["flows"][0]

    assert (flow["status"], flow["reason"].split(": ", 1)[1]) == ("blocked", "denied: send") and taps(phone) == []


def test_a_swipe_is_taken_up_and_flagged_as_assumed_and_typing_is_unsupported(runs, walk):
    recorded = screens()
    source_run(runs, recorded, [line(1, "s01", "s02", SWIPE), line(2, "s01", "s03", "type", summary="typed")],
               [[("s01", "s02", SWIPE)], [("s01", "s03", "type")]])
    phone = phone_for(screens(), [], swipes={"s01": "s02"})

    swiped, typed = walk(phone)["flows"]

    assert swiped["status"] == "matched" and swiped["assumed"] == [f"{swiped['edges'][0]}: swipe direction assumed up"]
    assert swiped["checkpoints"][-1]["mock"]["gesture"] and ("swipe", "s01", "up") in phone.log
    assert (typed["status"], typed["supported"]) == ("unsupported", False)
    assert "typing is not supported" in typed["reason"]
    assert phone.typed == []


@pytest.mark.parametrize("launch_on", ["s01", "s03"], ids=["home", "restored"])
def test_setup_reaches_the_start_over_recorded_edges_and_is_never_a_checkpoint(runs, walk, launch_on):
    """The flow starts on s02. A launch lands on home, or on a deeper screen the app restored, named by what it shows;
    either way recorded edges lead to the start, and only then does the evidence begin."""
    recorded = screens()
    lines = [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
             line(2, "s02", "s04", TAP, tab(recorded["s02"], 4), "tab"),
             line(3, "s03", "s01", TAP, tab(recorded["s03"], 0), "tab")]
    source_run(runs, recorded, lines, [[("s02", "s04", TAP)]])
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02"), ("s02", tab(recorded["s02"], 4), "s04"),
                                  ("s03", tab(recorded["s03"], 0), "s01")])
    phone.start = launch_on

    report = walk(phone)

    flow = report["flows"][0]
    assert flow["status"] == "matched" and [c["expected"] for c in flow["checkpoints"]] == ["s02", "s04"]
    assert report["setup"][1]["landed"] == launch_on
    assert [s["action"] for s in report["setup"]] == ["terminate", "launch", *[TAP] * (2 if launch_on == "s03" else 1)]
    assert report["actions"] == {"total": len(report["setup"]) + 1, "setup": len(report["setup"])}


@pytest.mark.parametrize("cap, value, why, start", [("MAX_ACTIONS", 3, "action cap (3", ["start", "tap"]),
                                                     ("MAX_MINUTES", 0, "time cap (0", [])], ids=["actions", "time"])
def test_a_cap_ends_the_audit_and_every_flow_left_says_why(runs, walk, monkeypatch, cap, value, why, start):
    monkeypatch.setattr(qa_live, cap, value)
    recorded = screens()
    source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
                                line(2, "s02", "s01", BACK, transition="back")],
               [[("s01", "s02", TAP), ("s02", "s01", BACK)], [("s01", "s02", TAP)]])
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])

    report = walk(phone)

    cut, left = report["flows"]
    assert cut["status"] == "blocked" and why in cut["reason"] and [c["action"] for c in cut["checkpoints"]] == start
    assert left["status"] == "blocked" and left["reason"].startswith("not visited: ") and why in left["reason"]
    assert report["stop"] == cut["reason"] and report["actions"]["total"] == value * (cap == "MAX_ACTIONS")
    assert walk.held == ["held", "released"]


class LoadsSlowly(FakePhone):
    """Opens on the feed's loading placeholders, which give way to the feed six seconds later."""

    def launch(self, retry: bool = True) -> None:
        super().launch(retry)
        self.screen, self.loaded_at = "loading", self.clock.t + 6

    def tick(self, seconds: float = 0.3) -> None:
        super().tick(seconds)
        if self.screen == "loading" and self.clock.t >= self.loaded_at:
            self.screen = "s01"


def test_a_launch_is_looked_at_once_the_feed_has_loaded_not_on_its_placeholders(runs, walk):
    recorded = screens()
    tab_back_run(runs, recorded)
    phone = phone_for(screens() | {"loading": capture("janitorai", "j02_home")},
                      [("s01", tab(recorded["s01"], 1), "s02")], cls=LoadsSlowly)

    report = walk(phone)

    assert report["setup"][1]["verdict"] == "same"
    assert report["flows"][0]["checkpoints"][0]["live"]["verdict"] == "same"


class HangingLaunch(FakePhone):
    def launch(self, retry: bool = True) -> None:
        self.log.append(("launch attempt", retry))
        raise mcp.McpTimeout("mobile_launch_app took over 30s")


def test_a_launch_that_times_out_is_never_sent_again_and_the_device_is_let_go(runs, walk, tmp_path):
    recorded = screens()
    source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab")],
               [[("s01", "s02", TAP)], [("s01", "s02", TAP)]])
    phone = phone_for(screens(), [], cls=HangingLaunch)

    report = walk(phone)

    assert [e for e in phone.log if e[0] == "launch attempt"] == [("launch attempt", False)]
    assert [f["status"] for f in report["flows"]] == ["blocked", "blocked"]
    assert "failed to launch the app (McpTimeout); not retried" in report["stop"]
    assert report["actions"]["total"] == 2 and taps(phone) == []
    assert walk.held == ["held", "released"] and walk.servers[0].closed and (tmp_path / "audit" / "report.md").exists()


def test_the_cli_walks_a_fake_device_and_never_reaches_mobile_mcp(runs, tmp_path, monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise AssertionError("a real mobile-mcp session was opened")
    monkeypatch.setattr(mcp, "stdio_client", refuse)
    recorded = screens()
    tab_back_run(runs, recorded)
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])
    servers = []
    monkeypatch.setattr(qa_live, "Server", lambda cwd: servers.append(FakeServer()) or servers[-1])
    monkeypatch.setattr(qa_live, "Phone", lambda *args, **kwargs: phone)
    monkeypatch.setattr(qa_live, "run", partial(qa_live.run, clock=phone.clock, sleep=phone.clock.sleep))
    out = tmp_path / "audit"

    assert cli.main(["qa-live", "janitorai", "--run", "r1", "--out", str(out), "--device", "emulator-5554"]) == 0

    assert "1 of 1 supported flows completed, 1 matched, of 1" in capsys.readouterr().out
    assert json.loads((out / "report.json").read_text())["summary"]["by_status"]["matched"] == 1
    assert len(servers) == 1 and servers[0].closed


def test_the_offline_suite_keeps_the_walk_off_any_device(runs, tmp_path):
    recorded = screens()
    tab_back_run(runs, recorded)

    with pytest.raises(NotImplementedError, match="never start mobile-mcp"):
        qa_live.run("janitorai", "r1", tmp_path / "audit", None)
    assert not (tmp_path / "audit").exists()


@pytest.mark.parametrize("where", ["inside", "through-a-symlink", "existing"])
def test_the_evidence_goes_only_to_a_new_folder_outside_the_run(runs, tmp_path, where):
    recorded = screens()
    run_dir = tab_back_run(runs, recorded)
    before = digest(run_dir)
    (tmp_path / "link").symlink_to(run_dir)
    (tmp_path / "existing").mkdir()
    out = {"inside": run_dir / "audit", "through-a-symlink": tmp_path / "link" / "audit",
           "existing": tmp_path / "existing"}[where]

    with pytest.raises(SystemExit, match="already exists" if where == "existing" else "inside the run"):
        qa_live.run("janitorai", "r1", out, None)
    assert digest(run_dir) == before


def test_a_device_of_another_size_is_refused_before_any_action(runs, walk, tmp_path):
    class Bigger(FakePhone):
        def screen_size(self) -> tuple[int, int]:
            return 1440, 3120
    recorded = screens()
    tab_back_run(runs, recorded)
    phone = phone_for(screens(), [], cls=Bigger)

    with pytest.raises(SystemExit, match="the device is .*w_px=1440"):
        walk(phone)
    assert phone.log == [] and walk.held == ["held", "released"] and not (tmp_path / "audit").exists()


# ---------- the red team's probes on 9d6a8cf, each failing there ----------

def swap_captures(run_dir: Path, a: str, b: str) -> None:
    """An explore rerun that recorded a's screen under b's id and b's under a's."""
    states = run_dir / "explore" / "states"
    for suffix in (".json", ".png", ".elements.json"):
        shutil.move(states / f"{a}{suffix}", states / f"tmp{suffix}")
        shutil.move(states / f"{b}{suffix}", states / f"{a}{suffix}")
        shutil.move(states / f"tmp{suffix}", states / f"{b}{suffix}")
    for sid in (a, b):
        sf = json.loads((states / f"{sid}.json").read_text())
        sf.update(state_id=sid, screenshot=f"states/{sid}.png", elements_reply=f"states/{sid}.elements.json")
        (states / f"{sid}.json").write_text(json.dumps(sf))


@pytest.mark.parametrize("stale, why", [
    ("model", r"model/product_model.json changed since QA approved the mock: rerun `simula qa janitorai`"),
    ("contract", r"mock/contract_report.json changed since QA approved the mock: rerun `simula qa janitorai`"),
    ("page", r"qa/approved/index.html changed since QA approved the mock: rerun `simula qa janitorai`"),
    ("asset", r"qa/approved/assets/page.css changed since QA approved the mock: rerun `simula qa janitorai`"),
    ("new asset", r"qa/approved/assets/added.png changed since QA approved the mock: rerun `simula qa janitorai`"),
    ("explore", r"explore changed since the model was built \(explore/states/s01.elements.json, .* and 3 more\): "
                r"rerun `simula run janitorai --run r1 --from model`"),
    ("no-marker", r"QA never finished on r1: run `simula run janitorai --run r1`"),
])
def test_an_approved_mock_older_than_the_files_it_was_made_from_is_refused_before_any_device_work(runs, walk, stale,
                                                                                                 why):
    """A model, mock or explore rerun alone leaves qa/ as it was: walking the old page against new inputs would blame
    the mock, or tap by captures the model wasn't built on. The stages' own markers say what changed."""
    recorded = screens()
    run_dir = tab_back_run(runs, recorded)
    model_path = run_dir / "model" / "product_model.json"
    if stale == "model":
        old = json.loads(model_path.read_text())["flows"][0]["edge_ids"][0]
        model_path.write_text(model_path.read_text().replace(old, old + "x"))  # the rerun model names the hop anew
    elif stale == "contract":
        (run_dir / "mock" / "contract_report.json").write_text(
            ContractReport(passed=True, screens=["s01", "s02"], errors=[]).model_dump_json())
    elif stale == "page":
        page = run_dir / "qa" / "approved" / "index.html"
        page.write_text(page.read_text() + "<!-- edited after approval -->")
    elif stale == "asset":  # Greptile on #42 (item 45): the page's own files can change after QA too
        css = run_dir / "qa" / "approved" / "assets" / "page.css"
        css.write_text("body { color: black }")
        approve(run_dir)
        css.write_text("body { color: red }")
    elif stale == "new asset":
        (run_dir / "qa" / "approved" / "assets" / "added.png").write_bytes(b"not approved")
    elif stale == "explore":
        swap_captures(run_dir, "s01", "s02")
    else:
        (run_dir / "qa" / "done.json").unlink()
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])

    with pytest.raises(SystemExit, match=why):
        walk(phone)
    assert phone.log == [] and walk.held == []


SAFE = {"ref": "@safe", "type": "android.widget.Switch", "text": "Safe mode",
        "coordinates": {"x": 700, "y": 1400, "width": 300, "height": 100}}
SAFE_CONTROL = FilterControl(label="Safe mode", kind="Switch", rect=Rect(x=700, y=1400, w=300, h=100),
                             tree_label="Safe mode")


def safe_mode(screen: Screen, on: bool) -> Screen:
    """The screen with the run's content filter, a "Safe mode" switch, on or off."""
    return with_extra(screen, {**SAFE, "checked": True} if on else SAFE)


@pytest.mark.parametrize("comes_on", [True, False], ids=["put back", "won't come on"])
def test_a_filtered_run_is_walked_only_once_each_launch_put_the_filter_back(runs, walk, comes_on):
    """Spec item 3: every launch can leave the app unfiltered, so after each one the walker puts the run's filter back
    as explore does and judges it with explore's own check (explore.filter_holds). One that won't come on ends the
    audit before any flow's tap."""
    recorded = screens()
    recorded["s01"] = safe_mode(recorded["s01"], on=True)
    tab_back_run(runs, recorded, "Safe mode (on)", filter_controls=[SAFE_CONTROL], filter_on=True)
    live = screens()
    live["s01"], live["on"] = safe_mode(live["s01"], on=False), safe_mode(live["s01"], on=True)
    phone = phone_for(live, [("s01", labeled(live["s01"], "Safe mode"), "on" if comes_on else "s01"),
                             ("on", tab(live["on"], 1), "s02")])

    report = walk(phone)
    setup = [s["action"] for s in report["setup"]]
    assert setup[:4] == ["terminate", "launch", "filter", "filter check"], report["setup"]
    assert report["setup"][3]["verified"] is comes_on
    if comes_on:
        assert report["flows"][0]["status"] == "matched" and report["stop"] is None, report["flows"][0]
    else:
        assert report["stop"] == ("the content filter (Safe mode (on)) isn't verified after the launch: nothing is "
                                  "walked without it")
        assert report["flows"][0]["status"] == "blocked" and taps(phone) == [taps(phone)[0]]


def test_a_run_that_recorded_a_filter_but_not_its_controls_is_refused_before_any_device_work(runs, walk):
    """A run explored before explore.json recorded the filter's controls can't have its filter put back."""
    recorded = screens()
    tab_back_run(runs, recorded, "Limited Only")
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])

    with pytest.raises(SystemExit, match=r"the run recorded a content filter \('Limited Only'\) but not its controls"):
        walk(phone)
    assert phone.log == [] and walk.held == [] and walk.servers == []


def test_a_partial_qa_on_current_files_is_walked_and_says_so_and_an_undrawn_screen_is_left_out(runs, walk, tmp_path):
    """QA finishes partial on an undrawn screen or a flow that still fails, and its page is still the current one:
    the walk goes ahead, reports QA's status and reasons, and leaves out a flow through the undrawn screen."""
    recorded = screens()
    run_dir = source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
                                          line(2, "s02", "s01", BACK, transition="back"),
                                          line(3, "s01", "s03", TAP, tab(recorded["s01"], 3), "tab")],
                         [[("s01", "s02", TAP), ("s02", "s01", BACK)], [("s01", "s03", TAP)]])
    (run_dir / "mock" / "contract_report.json").write_text(ContractReport(
        passed=False, screens=["s01", "s02", "s03", "s04"],
        errors=[ContractError(kind="undrawn_screen", detail="screen not drawn: $ cap reached", screen="s03")],
    ).model_dump_json())
    approve(run_dir, StageOutcome(status="partial", reasons=["1 screen not drawn", "core flows that still fail: f2"]))
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")])

    report = walk(phone)

    assert report["qa"] == {"status": "partial", "reasons": ["1 screen not drawn", "core flows that still fail: f2"]}
    walked, undrawn = report["flows"]
    assert walked["status"] == "matched"
    assert (undrawn["status"], undrawn["reason"]) == ("unsupported",
                                                      f"{undrawn['edges'][0]} passes a screen the mock didn't draw")
    assert ("QA approved it partial: 1 screen not drawn; core flows that still fail: f2."
            in (tmp_path / "audit" / "report.md").read_text())


def test_a_route_to_the_start_never_counts_on_a_denied_control_when_a_permitted_one_exists(runs, walk):
    recorded = with_send()
    send = labeled(recorded["s01"], "Send")
    source_run(runs, recorded, [line(1, "s01", "s03", TAP, send),
                                line(2, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
                                line(3, "s02", "s03", TAP, tab(recorded["s02"], 3), "tab"),
                                line(4, "s03", "s01", TAP, tab(recorded["s03"], 0), "tab")],
               [[("s03", "s01", TAP)]])
    phone = phone_for(with_send(), [("s01", tab(recorded["s01"], 1), "s02"), ("s02", tab(recorded["s02"], 3), "s03"),
                                    ("s03", tab(recorded["s03"], 0), "s01")])

    report = walk(phone)

    assert report["flows"][0]["status"] == "matched"
    assert [s.get("edge", "").split(">")[-1] for s in report["setup"][2:]] == ["s02", "s03"]
    assert all(key != "Send" for _, _, key in taps(phone))


def timed_starts(monkeypatch) -> list[float]:
    """The second, since the audit started, of every device action's start."""
    starts, real = [], qa_live.Audit.mutate

    def timed(self, call, *args, **kwargs):
        starts.append(self.clock() - self.started)
        return real(self, call, *args, **kwargs)
    monkeypatch.setattr(qa_live.Audit, "mutate", timed)
    return starts


def test_a_time_cap_that_falls_after_the_last_action_still_cuts_the_flow_and_says_so(runs, walk, tmp_path, monkeypatch):
    recorded = screens()
    tab_back_run(runs, recorded)
    starts = timed_starts(monkeypatch)
    walk(phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")]), tmp_path / "probe")
    monkeypatch.setattr(qa_live, "MAX_MINUTES", (starts[-1] + 0.5) / 60)  # the last BACK starts just before the cap

    report = walk(phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")]), tmp_path / "capped")

    flow = report["flows"][0]
    assert flow["status"] == "blocked" and "time cap" in report["stop"] and flow["reason"] == report["stop"]
    assert [c["action"] for c in flow["checkpoints"]] == ["start", "tap"]


class HangsAfterLaunch(FakePhone):
    """Every element dump times out once the app is launched, as a hung uiautomator does."""

    def launch(self, retry: bool = True) -> None:
        super().launch(retry)
        self.hung_lists = 10 ** 6


def test_a_time_cap_inside_a_launch_wait_ends_the_wait(runs, walk, tmp_path, monkeypatch):
    recorded = screens()
    hop = ("s01", "s02", TAP)
    source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab")], [[hop], [hop]])
    starts = timed_starts(monkeypatch)
    walk(phone_for(screens(), [], cls=HangsAfterLaunch), tmp_path / "probe")
    cap_s = starts[-1] + 0.5  # the cap falls just after the launch, inside its wait for a screen
    monkeypatch.setattr(qa_live, "MAX_MINUTES", cap_s / 60)

    report = walk(phone_for(screens(), [], cls=HangsAfterLaunch), tmp_path / "capped")

    assert "time cap" in report["stop"] and [f["status"] for f in report["flows"]] == ["blocked", "blocked"]
    assert report["minutes"] * 60 < cap_s + 45  # past the cap by one look at most, not the whole launch wait


def test_a_device_that_fails_after_a_launch_is_not_launched_again_for_the_next_flow(runs, walk):
    recorded = screens()
    hop = ("s01", "s02", TAP)
    source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab")], [[hop], [hop], [hop]])
    phone = phone_for(screens(), [], cls=HangsAfterLaunch)

    report = walk(phone)

    assert phone.log.count(("launch",)) == 1 and report["stop"].startswith("the device failed after the launch")
    assert [f["reason"].startswith("not visited: ") for f in report["flows"]] == [False, True, True]


def with_popup(screen: Screen, dim: float) -> Screen:
    """A same-window popup over the screen (a coach mark, a promo): two new labeled controls in one box in the middle,
    drawn after everything else, over an optional scrim that mobile-mcp doesn't list."""
    image = Image.eval(screen.image, lambda v: int(v * dim))
    draw = ImageDraw.Draw(image)
    draw.rectangle((140, 900, 940, 1400), fill=(250, 250, 250))
    draw.rectangle((300, 1260, 780, 1360), fill=(80, 60, 200))
    popup = [{"ref": "@p1", "type": "android.widget.TextView", "text": "New: group chats are here", "label": "",
              "coordinates": {"x": 180, "y": 940, "width": 720, "height": 120}},
             {"ref": "@p2", "type": "android.widget.Button", "text": "Got it", "label": "",
              "coordinates": {"x": 300, "y": 1260, "width": 480, "height": 100}}]
    return Screen([*screen.elements, *popup], image, screen.package)


@pytest.mark.parametrize("dim", [1.0, 0.6], ids=["no-scrim", "scrim"])
def test_a_tap_is_never_sent_behind_an_overlay_the_recording_didnt_have(runs, walk, dim):
    recorded = screens()
    tab_back_run(runs, recorded)
    target = tab(recorded["s01"], 1)
    phone = phone_for(screens() | {"s01": with_popup(screens()["s01"], dim)}, [("s01", target, "s02")])

    flow = walk(phone)["flows"][0]

    assert flow["status"] == "unsupported" and flow["reason"].endswith(": an overlay the recording didn't have is up")
    assert taps(phone) == []


class Directional(FakePhone):
    """A feed that scrolls both ways: up goes further down the feed, down goes back."""

    def swipe(self, direction: str) -> None:
        self.tick()
        self.log.append(("swipe", self.screen, direction))
        self.go({("s01", "up"): "s02", ("s02", "down"): "s01", ("s02", "up"): "s03"}.get((self.screen, direction)))


def test_a_swipe_its_trace_calls_a_scroll_back_is_swiped_down_and_not_flagged_as_assumed(runs, walk):
    recorded = screens()
    lines = [line(1, "s01", "s02", SWIPE), line(2, "s02", "s01", SWIPE)]
    scroll_back(source_run(runs, recorded, lines, [[("s01", "s02", SWIPE), ("s02", "s01", SWIPE)]]), lines[1])
    phone = phone_for(screens(), [], cls=Directional)

    flow = walk(phone)["flows"][0]

    assert flow["status"] == "matched" and ("swipe", "s02", "down") in phone.log
    assert flow["assumed"] == [f"{flow['edges'][0]}: swipe direction assumed up"]


def test_a_swipe_back_its_trace_doesnt_call_a_scroll_back_stays_an_assumed_swipe_up(runs, walk):
    """The other order: a model-chosen swipe down first (s02 > s01), then one up back (s01 > s02). The later swipe
    returns to where the earlier one started, but explore's trace calls neither a scroll back, so its direction is
    a guess: up, flagged."""
    recorded = screens()
    source_run(runs, recorded, [line(1, "s02", "s01", SWIPE), line(2, "s01", "s02", SWIPE)], [[("s01", "s02", SWIPE)]])
    phone = phone_for(screens(), [], cls=Directional)

    flow = walk(phone)["flows"][0]

    assert ("swipe", "s01", "up") in phone.log and flow["status"] == "matched"
    assert flow["assumed"] == [f"{flow['edges'][0]}: swipe direction assumed up"]


def test_a_landing_elsewhere_after_an_assumed_swipe_blames_neither_side(runs, walk):
    recorded = screens()
    source_run(runs, recorded, [line(1, "s02", "s01", SWIPE)], [[("s02", "s01", SWIPE)]])
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")], cls=Directional)
    phone.start = "s02"  # a recorded swipe from s02 that no scroll explains: the walker guesses up, the feed goes on

    flow = walk(phone)["flows"][0]

    assert flow["status"] == "unverified" and "after a swipe whose direction is assumed up" in flow["reason"]
    assert flow["checkpoints"][-1]["live"]["landed"] == "s03"


class KeepsScratch(FakePhone):
    """Keeps what the scratch capture holds once capture() has redacted it and goes on to read the foreground."""

    def screenshot(self, path: Path, size=None) -> Path:
        self.scratch_path = path
        return super().screenshot(path, size)

    def foreground(self) -> str:
        if getattr(self, "scratch_path", None):
            self.on_disk = Image.open(self.scratch_path).convert("RGB")
        return super().foreground()


def test_the_capture_on_disk_is_the_redacted_one(runs, walk, monkeypatch):
    monkeypatch.setenv("SIMULA_REDACT", "Trending")
    recorded = screens()
    tab_back_run(runs, recorded)
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")], cls=KeepsScratch)

    walk(phone)

    hidden = [ob.rect(e) for e in recorded["s01"].elements if "Trending" in ob.words(e)]
    assert hidden and all(phone.on_disk.crop((r.x, r.y, r.x + r.w, r.y + r.h)).getextrema() == ((0, 0),) * 3
                          for r in hidden)


def test_the_account_explore_signs_up_with_is_redacted_as_explore_redacts_it(runs, walk, monkeypatch, tmp_path):
    """After an explore that made an account, the app shows it: its name, word by word, never reaches the disk."""
    monkeypatch.setenv("SIMULA_TEST_NAME", "Trending Tester")
    recorded = screens()
    tab_back_run(runs, recorded)
    phone = phone_for(screens(), [("s01", tab(recorded["s01"], 1), "s02")], cls=KeepsScratch)

    flow = walk(phone)["flows"][0]

    tree = (tmp_path / "audit" / flow["checkpoints"][0]["files"]["tree"]).read_text()
    assert "Trending" not in tree and ob.REDACTED in tree
    hidden = [ob.rect(e) for e in recorded["s01"].elements if "Trending" in ob.words(e)]
    assert hidden and all(phone.on_disk.crop((r.x, r.y, r.x + r.w, r.y + r.h)).getextrema() == ((0, 0),) * 3
                          for r in hidden)


SIGTERM_WALK = '''
import contextlib, os, signal, sys
from pathlib import Path
from simula import qa_live, runfolder
from tests import test_qa_live as t
from tests.fake_device import FakePhone

work = Path(sys.argv[1])
runfolder.RUNS = work / "runs"
recorded = t.screens()
t.tab_back_run(runfolder.RUNS, recorded)


class Killed(FakePhone):
    def screenshot(self, path, size=None):
        out = super().screenshot(path, size)
        if path.name == "now.png" and ("launch",) in self.log:
            os.kill(os.getpid(), signal.SIGTERM)  # an operator or a timeout stops the walk mid-capture
        return out


@contextlib.contextmanager
def lock(*args, **kwargs):
    try:
        yield
    finally:
        print("lock released", flush=True)


phone = t.phone_for(t.screens(), [("s01", t.tab(recorded["s01"], 1), "s02")], cls=Killed)
qa_live.emulator_lock, qa_live.resolve_serial = lock, lambda flag: "offline"
qa_live.explore.adb_shell = lambda *args: None
qa_live.Server = lambda cwd: type("S", (), {"close": lambda self: print("server closed", flush=True)})()
qa_live.Phone = lambda *args, **kwargs: phone
qa_live.run("janitorai", "r1", work / "audit", None, clock=phone.clock, sleep=phone.clock.sleep)
'''


def test_a_sigterm_mid_walk_releases_the_device_and_leaves_no_raw_capture(tmp_path):
    root = Path(__file__).resolve().parent.parent
    (tmp_path / "walk.py").write_text(SIGTERM_WALK)
    done = subprocess.run([sys.executable, str(tmp_path / "walk.py"), str(tmp_path)], cwd=root, capture_output=True,
                          text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(root), "SIMULA_REDACT": "Trending"})

    assert done.returncode == 1 and "qa-live stopped by SIGTERM" in done.stderr, done.stderr[-2000:]
    assert done.stdout.split() == ["server", "closed", "lock", "released"]
    assert not (tmp_path / "audit").exists()
