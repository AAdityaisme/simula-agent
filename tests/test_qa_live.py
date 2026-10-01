"""simula qa-live on a fake phone and the offline builder's mock: each hop is taken on both sides and paired, the walk
stops at the first divergence and names the side, a target it can't verify is never tapped, setup is never evidence,
and the caps, the lock and the source run hold. No model call and no real device anywhere."""

import contextlib
import hashlib
import json
import shutil
from functools import partial
from pathlib import Path

import pytest
from PIL import ImageDraw

from simula import cli, decide, llm, qa_live
from simula.contracts import (ActionLine, ContractReport, Coverage, Device, Flow, Point, ProductModel, Provenance,
                              StateFile)
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
               flows: list[list[tuple[str, str, str]]]) -> Path:
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
    return run_dir


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


def tab_back_run(runs: Path, recorded: dict[str, Screen]) -> Path:
    """One flow: tap s01's second tab to s02, then BACK to s01."""
    return source_run(runs, recorded, [line(1, "s01", "s02", TAP, tab(recorded["s01"], 1), "tab"),
                                       line(2, "s02", "s01", BACK, transition="back")],
                      [[("s01", "s02", TAP), ("s02", "s01", BACK)]])


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
            for c in flow["checkpoints"]] == [("start", "s01", "same", "s01", None), ("tap", "s02", "same", "s02", None),
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


def test_a_mock_that_lost_the_hops_tag_is_the_mock_failing(runs, walk):
    recorded = screens()
    run_dir = tab_back_run(runs, recorded)
    page = run_dir / "qa" / "approved" / "index.html"
    edge = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text()).flows[0].edge_ids[0]
    assert f'data-edge="{edge}"' in page.read_text()
    page.write_text(page.read_text().replace(f'data-edge="{edge}"', ""))
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


def test_a_control_on_the_deny_list_is_never_tapped(runs, walk):
    """A send recorded in explore's core loop is denied outside it, as on every tour tap."""
    def renamed() -> dict[str, Screen]:
        found = screens()
        s01 = found["s01"]
        found["s01"] = Screen([{k: "Send" if v == "Trending" else v for k, v in e.items()} for e in s01.elements],
                              s01.image, s01.package)
        return found
    recorded = renamed()
    send = labeled(recorded["s01"], "Send")
    source_run(runs, recorded, [line(1, "s01", "s02", TAP, send)], [[("s01", "s02", TAP)]])
    phone = phone_for(renamed(), [("s01", send, "s02")])

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
    assert (typed["status"], typed["supported"]) == ("unsupported", False) and "typing is not supported" in typed["reason"]
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
