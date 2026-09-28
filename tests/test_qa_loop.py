"""The QA loop with a fake critic and fixer: the 0.3 stop rule, the discard rule, the best version approved, the
fixer's effort per round, a failed model call or a broken page that never blocks the slides, and a replay that
doesn't depend on render bytes. The critic in groups of screens and undrawn screens run on a synthetic 12-screen
model."""

import json
import re
import threading
import time
from dataclasses import replace

import pytest
from PIL import Image

from simula import config, llm, render
from simula.contracts import (ContractError, ContractReport, Critique, Edit, Edits, Fix, ProductModel, QAMetrics,
                              ScreenMetrics)
from simula.runlog import read_trace
from simula.stages import mock, qa
from tests.conftest import APPS
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import ctx_for, fake_builder


@pytest.fixture(autouse=True)
def records(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "RECORDS", tmp_path / "records")
    return tmp_path / "records"


def fake_llm(calls: list, edits=lambda n: []):
    def call(**kwargs):
        calls.append(kwargs)
        if kwargs["schema"] is Critique:
            return Critique(fixes=[Fix(element_id="s01", problem="p", fix="f")], summary="s"), None
        return Edits(edits=edits(sum(c["schema"] is Edits for c in calls))), None
    return call


@pytest.fixture
def scripted(tmp_path, monkeypatch):
    """A run whose measured scores follow a script, one per version, so the loop's rules can be tested alone."""
    run_dir = seed_model(tmp_path / "run", "luzia")
    (run_dir / "mock" / "assets").mkdir(parents=True)
    (run_dir / "mock" / "index.html").write_text("<html><body>v0</body></html>")
    screens = [s.id for s in mock.pick_scope(golden("luzia"))]
    (run_dir / "mock" / "contract_report.json").write_text(
        ContractReport(passed=True, screens=screens, errors=[]).model_dump_json())
    calls = []

    def play(scores, profile="dev", edits=lambda n: [Edit(find=f"v{n - 1}", replace=f"v{n}", reason="r")],
             errors=None):
        def measure(ctx, model, scope, n, html):
            metrics = QAMetrics(round=n, screens=[], cross_screen_failures=[], score=scores[n])
            broken = [ContractError(kind="invented_edge", detail="d", screen=None)] * (errors[n] if errors else 0)
            return qa.Version(n, html, metrics, [], [], [], broken)
        monkeypatch.setattr(qa, "measure", measure)
        monkeypatch.setattr(qa, "rebuild", lambda html, model, screens: html)
        monkeypatch.setattr(llm, "call", fake_llm(calls, edits))
        qa.run(ctx_for(run_dir, "luzia", profile=profile))
        return json.loads((run_dir / "qa" / "qa_report.json").read_text())
    return run_dir, calls, play


def approved_html(run_dir) -> str:
    return (run_dir / "qa" / "approved" / "index.html").read_text()


def test_after_round_2_a_gain_under_0_3_stops_the_loop(scripted):
    run_dir, calls, play = scripted
    report = play([5.0, 6.0, 6.2, 9.0])
    assert [r["round"] for r in report["rounds"]] == [0, 1, 2]
    assert report["approved_round"] == 2 and "round 2 gained 0.20" in report["stop_reason"]
    assert "v2" in approved_html(run_dir)
    assert sum(c["schema"] is Edits for c in calls) == 2


def test_round_1_never_stops_on_a_small_gain(scripted):
    _, _, play = scripted
    report = play([5.0, 5.1, 6.0, 7.0])
    assert [r["round"] for r in report["rounds"]] == [0, 1, 2, 3]
    assert report["approved_round"] == 3 and report["stop_reason"] == "all 3 rounds ran"


def test_a_round_that_lowers_the_score_is_discarded_and_stops_the_loop(scripted):
    run_dir, _, play = scripted
    report = play([5.0, 6.0, 5.5, 9.0])
    assert [(r["round"], r["kept"]) for r in report["rounds"]] == [(0, True), (1, True), (2, False)]
    assert report["approved_round"] == 1 and "discarded" in report["stop_reason"]
    assert "v1" in approved_html(run_dir) and "v2" not in approved_html(run_dir)


def test_a_first_round_that_lowers_the_score_approves_the_mock_as_delivered(scripted):
    run_dir, _, play = scripted
    report = play([5.0, 4.0])
    assert report["approved_round"] == 0
    assert approved_html(run_dir) == (run_dir / "mock" / "index.html").read_text()


def test_a_round_that_fixes_a_contract_error_is_kept_even_if_the_score_dips(scripted):
    run_dir, _, play = scripted
    report = play([5.0, 4.8, 4.7], errors=[1, 0, 0])
    assert [(r["round"], r["kept"]) for r in report["rounds"]] == [(0, True), (1, True), (2, False)]
    assert report["approved_round"] == 1 and "v1" in approved_html(run_dir)
    assert report["keep_rule_disagreement"] == {"round": 1, "score_only_approves": 0, "contract_errors": [1, 0],
                                                "score": [5.0, 4.8]}


def test_a_round_that_adds_a_contract_error_is_discarded_even_if_the_score_rises(scripted):
    _, _, play = scripted
    report = play([5.0, 6.0], errors=[0, 1])
    assert [(r["round"], r["kept"]) for r in report["rounds"]] == [(0, True), (1, False)]
    assert report["approved_round"] == 0 and "added contract errors (0 → 1)" in report["stop_reason"]
    assert report["keep_rule_disagreement"]["score_only_approves"] == 1


def test_a_round_that_repairs_a_contract_error_never_stops_the_loop_on_a_small_gain(scripted):
    _, _, play = scripted
    report = play([5.0, 6.0, 6.1, 6.2], errors=[3, 2, 1, 0])
    assert [r["round"] for r in report["rounds"]] == [0, 1, 2, 3]
    assert report["approved_round"] == 3 and report["stop_reason"] == "all 3 rounds ran"


def test_with_equal_contract_errors_the_higher_score_wins(scripted):
    _, _, play = scripted
    report = play([5.0, 6.0, 5.5], errors=[2, 2, 2])
    assert [(r["round"], r["kept"]) for r in report["rounds"]] == [(0, True), (1, True), (2, False)]
    assert report["approved_round"] == 1 and "lowered the score 6.00 → 5.50" in report["stop_reason"]
    assert report["keep_rule_disagreement"] is None


def test_the_fixer_runs_high_then_xhigh_in_round_3_and_the_critic_gets_the_history(scripted):
    _, calls, play = scripted
    play([5.0, 6.0, 7.0, 8.0], profile="real")
    assert [c["effort"] for c in calls if c["schema"] is Edits] == ["high", "high", "xhigh"]
    critics = [c for c in calls if c["schema"] is Critique]
    history = critics[1]["messages"][0]["content"][-1]["text"]
    assert '"round":1,"score_before":5.0,"score_after":6.0,"kept":true' in history


@pytest.mark.parametrize("profile", ["dev", "real"])
def test_the_fixer_has_room_to_think_on_a_whole_page_and_streams(scripted, profile):
    _, calls, play = scripted
    play([5.0, 6.0, 7.0, 8.0], profile=profile)
    fixers = [c for c in calls if c["schema"] is Edits]
    assert fixers and all(c["max_tokens"] == 64000 for c in fixers)
    assert all(c["max_tokens"] > config.models()[c["model"]]["stream_above"] for c in fixers)
    assert all(c["max_tokens"] == 16000 for c in calls if c["schema"] is Critique)


def test_rejected_edits_are_logged_and_reach_the_next_critic(scripted):
    run_dir, calls, play = scripted
    play([5.0, 5.0, 5.0], edits=lambda n: [Edit(find="nowhere", replace="x", reason="r")])
    logged = json.loads((run_dir / "qa" / "round1" / "edits.json").read_text())
    assert logged["edits"][0]["applied"] is False
    critics = [c for c in calls if c["schema"] is Critique]
    assert "find matches the page 0 times" in critics[1]["messages"][0]["content"][-1]["text"]


@pytest.mark.parametrize("failure", [llm.LLMFailure("refusal", "no"), llm.CapReached("qa: cap")])
def test_a_failed_model_call_stops_the_loop_but_still_approves_a_version(scripted, monkeypatch, failure):
    run_dir, _, play = scripted

    def fail(**kwargs):
        raise failure
    monkeypatch.setattr(qa, "fix", lambda *a, **k: fail())
    report = play([5.0])
    assert report["approved_round"] == 0 and "round 1 stopped before any edit" in report["stop_reason"]
    assert approved_html(run_dir) == "<html><body>v0</body></html>"
    assert read_trace(run_dir / "trace.jsonl")[-1].step == "stop"


@pytest.mark.parametrize("app", APPS)
def test_the_whole_stage_runs_on_every_golden(tmp_path, monkeypatch, app):
    """End to end on a real render: stage 3 with the offline builder, then QA with a fixer that strips one element's
    data-el. That round scores lower, so it is discarded and the delivered mock is approved."""
    run_dir = seed_model(tmp_path / "run", app)
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))
    delivered = (run_dir / "mock" / "index.html").read_text()
    first = next(t["attrs"]["data-el"] for t in mock.StartTags(delivered).tags if "data-el" in t["attrs"])
    knock = Edit(find=f'data-el="{first}"', replace=f'data-x="{first}"', reason="r")
    monkeypatch.setattr(llm, "call", fake_llm([], lambda n: [knock]))
    qa.run(ctx_for(run_dir, app))

    qa_dir = run_dir / "qa"
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    screens = [s.id for s in mock.pick_scope(model)]
    for kind in ("real", "mock", "heatmap"):
        assert sorted(p.stem for p in (qa_dir / "round0" / kind).glob("*.png")) == sorted(screens)
    metrics = QAMetrics.model_validate_json((qa_dir / "round0" / "metrics.json").read_text())
    assert metrics.round == 0 and len(metrics.screens) == len(screens)
    report = json.loads((qa_dir / "qa_report.json").read_text())
    assert report["schema_version"] == 1 and report["status"] == "approved"
    assert [(r["round"], r["kept"]) for r in report["rounds"]] == [(0, True), (1, False)]
    assert report["approved_round"] == 0 and approved_html(run_dir) == delivered
    assert sorted(p.name for p in (qa_dir / "approved" / "assets").iterdir()) == \
        sorted(p.name for p in (run_dir / "mock" / "assets").iterdir())
    assert "| 1 | " in (run_dir / "exhibits" / "04-qa.md").read_text() and "discarded" in report["stop_reason"]
    assert ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text()).passed


@pytest.mark.parametrize("app", APPS)
def test_a_fixer_edit_that_breaks_the_page_is_discarded_and_the_delivered_mock_approved(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))
    delivered = (run_dir / "mock" / "index.html").read_text()
    unclosed = Edit(find="<body>", replace="<body><!-- an unclosed comment swallows the runtime", reason="r")
    monkeypatch.setattr(llm, "call", fake_llm([], lambda n: [unclosed]))
    qa.run(ctx_for(run_dir, app))

    report = json.loads((run_dir / "qa" / "qa_report.json").read_text())
    assert report["approved_round"] == 0 and "round 1 broke the page" in report["stop_reason"]
    assert approved_html(run_dir) == delivered


@pytest.mark.parametrize("app", APPS)
def test_a_replay_whose_renders_differ_makes_no_model_call_and_ends_where_the_recorded_run_did(
        tmp_path, monkeypatch, records, app):
    """The recorded run has a critic group that refuses (skipped) and a fixer that fails (the loop stops); the replay
    has to fail the same way to end in the same place. Groups of 2 give every golden a second critic group."""
    monkeypatch.setattr(qa, "CRITIC_SCREENS", 2)
    run_dir = seed_model(tmp_path / "run", app)
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))
    answer = fake_llm([], lambda n: [Edit(find="</body>", replace="<!-- round 1 --></body>", reason="r")])

    def refusing(**kwargs):
        if kwargs["step"] in ("critic r1 g2", "fixer r2"):
            raise llm.LLMFailure("refusal", "refused")
        return answer(**kwargs)
    monkeypatch.setattr(llm, "call", refusing)
    qa.run(ctx_for(run_dir, app))
    recorded = json.loads((run_dir / "qa" / "qa_report.json").read_text())
    assert recorded["stop_reason"] == "round 2 stopped before any edit: refusal: refused"
    assert any("group skipped" in line.note for line in read_trace(run_dir / "trace.jsonl"))

    screenshot = render.screenshot_screens

    def one_pixel_off(page, screens, out_dir):
        paths = screenshot(page, screens, out_dir)
        for path in paths:
            image = Image.open(path).convert("RGB")
            center = (image.width // 2, image.height // 2)
            r, g, b = image.getpixel(center)
            image.putpixel(center, (r ^ 1, g, b))
            image.save(path)
        return paths

    def no_cache_entry(**kwargs):
        raise llm.ReplayMiss(f"--replay: no cached response for qa/{kwargs['step']}")
    monkeypatch.setattr(render, "screenshot_screens", one_pixel_off)
    monkeypatch.setattr(llm, "call", no_cache_entry)
    qa.run(replace(ctx_for(run_dir, app), replay=True))
    assert json.loads((run_dir / "qa" / "qa_report.json").read_text()) == recorded
    assert "Replayed: the scores are the recorded run's" in (run_dir / "exhibits" / "04-qa.md").read_text()

    replayed = [line for line in read_trace(run_dir / "trace.jsonl") if "recorded for this page" in line.note]
    assert [line.outcome for line in replayed if line.step in ("critic r1 g2", "fixer r2")] == ["refusal", "refusal"]

    for record in records.iterdir():
        record.unlink()
    with pytest.raises(llm.ReplayMiss, match="no measurement record for round 0"):
        qa.run(replace(ctx_for(run_dir, app), replay=True))
    assert not any(records.iterdir()) and not (run_dir / "qa" / "round0").exists()


@pytest.mark.parametrize("app", APPS)
def test_qa_walks_exactly_the_screens_the_mock_drew_even_an_unsafe_one_on_a_core_flow(tmp_path, monkeypatch, app):
    """The mock follows the model's scope, where an unsafe screen on a core flow is in; QA has no rating rule."""
    model = golden(app)
    scope = mock.pick_scope(model)
    edges = {e.id: e for e in mock.scope_edges(model, scope)}
    flow = next(f for f in model.flows if f.edge_ids and all(i in edges for i in f.edge_ids))
    unsafe = edges[flow.edge_ids[0]].to_state
    rated = model.model_copy(update={"states": [s.model_copy(update={"content_rating": "unsafe"}) if s.id == unsafe
                                                else s for s in model.states]})
    run_dir = seed_model(tmp_path / "run", app)
    (run_dir / "model" / "product_model.json").write_text(rated.model_dump_json())
    ids = [s.id for s in scope]
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    (run_dir / "mock" / "index.html").write_text(qa.rebuild(skeleton_html(model), rated, ids))
    (run_dir / "mock" / "contract_report.json").write_text(
        ContractReport(passed=True, screens=ids, errors=[]).model_dump_json())

    def refuse(**kwargs):
        raise llm.LLMFailure("refusal", "refused")
    monkeypatch.setattr(llm, "call", refuse)
    qa.run(ctx_for(run_dir, app))
    report = json.loads((run_dir / "qa" / "qa_report.json").read_text())
    assert unsafe in [s["state_id"] for s in report["screens"]]
    assert next(w for w in report["flows"] if w["flow"] == flow.id)["status"] == "passed"


# ---------- a synthetic 12-screen model: the critic in groups, undrawn screens ----------

@pytest.fixture
def twelve(tmp_path, monkeypatch, request):
    """A run of the app's golden with its in-scope states cloned under new state and element ids until 12 screens
    are in scope. main's 8-screen cap is lifted (PR 3c removes it)."""
    monkeypatch.setattr(mock, "MAX_SCREENS", 20, raising=False)
    app = request.param
    model = golden(app)
    scope = mock.pick_scope(model)
    clones = []
    for k in range(12 - len(scope)):
        state, sid = scope[k % len(scope)], f"x{k + 1:02}"
        elements = [e.model_copy(update={"id": sid + e.id[len(state.id):]}) for e in state.elements]
        clones.append(state.model_copy(update={"id": sid, "parent_id": None, "elements": elements}))
    model = model.model_copy(update={"states": [*model.states, *clones]})
    run_dir = seed_model(tmp_path / "run", app)
    (run_dir / "model" / "product_model.json").write_text(model.model_dump_json())
    assert len(mock.pick_scope(model)) == 12
    return app, run_dir, model, [s.id for s in mock.pick_scope(model)]


def measured_twelve(run_dir, model, errors=()) -> qa.Version:
    """Round 0 of a 12-screen mock as QA would hold it, without rendering: tiny images, even scores."""
    screens = []
    for i, state in enumerate(mock.pick_scope(model)):
        for kind in ("real", "mock", "heatmap"):
            path = run_dir / "qa" / "round0" / kind / f"{state.id}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4), (i, 0, 0)).save(path)
        metrics = ScreenMetrics(state_id=state.id, ssim_masked=0.5, pixelmatch_ratio=None, masked_coverage=1.0,
                                bounds_ok_share=1.0, nav_pass_rate=1.0, score=5.0)
        screens.append({"metrics": metrics, "name": state.name, "tagged": 0, "taps": 0, "taps_passed": 0, "misses": []})
    metrics = QAMetrics(round=0, screens=[s["metrics"] for s in screens], cross_screen_failures=[], score=5.0)
    return qa.Version(0, "<html><body></body></html>", metrics, screens, [], [], list(errors))


def shown(kwargs) -> list[str]:
    """The screens a call's images are of, in order."""
    captions = [p["text"] for p in kwargs["messages"][0]["content"] if p["type"] == "text" and p["text"].endswith(":")]
    return list(dict.fromkeys(c.split(" ")[0] for c in captions))


def told(kwargs) -> dict:
    text = kwargs["messages"][0]["content"][-1]["text"]
    return json.loads(re.search(r"(?:The numbers|What to fix):\n(.*?)\n\n", text, re.S)[1])


def fake_critic(calls: list, fail_on=(), pause=0.0):
    """A critic that fixes each screen it is shown, plus one data-el every group names. Tracks how many run at once."""
    lock, running = threading.Lock(), [0]

    def call(**kwargs):
        seen = shown(kwargs)
        with lock:
            running[0] += 1
            calls.append({"seen": seen, "told": told(kwargs), "running": running[0]})
        time.sleep(pause)
        with lock:
            running[0] -= 1
        if kwargs["schema"] is Edits:
            return Edits(edits=[]), None
        if seen[0] in fail_on:
            raise llm.LLMFailure("refusal", "no")
        fixes = [Fix(element_id=sid, problem="p", fix=f"group of {seen[0]}") for sid in seen]
        return Critique(fixes=[*fixes, Fix(element_id="shared", problem="p", fix=f"group of {seen[0]}")],
                        summary=seen[0]), None
    return call


def criticize(run_dir, app, version):
    return qa.criticize(ctx_for(run_dir, app), llm.Budget("qa", 12.0), version, [], 1)


@pytest.mark.parametrize("twelve", APPS, indirect=True)
def test_the_critic_sees_12_screens_in_3_groups_of_4_in_order_and_their_fixes_merge_by_data_el(twelve, monkeypatch):
    app, run_dir, model, ids = twelve
    page_wide = ContractError(kind="console_error", detail="page-wide", screen=None)
    on_ten = ContractError(kind="unknown_el", detail="on the tenth screen", screen=ids[9])
    calls = []
    monkeypatch.setattr(llm, "call", fake_critic(calls))
    critique = criticize(run_dir, app, measured_twelve(run_dir, model, [page_wide, on_ten]))

    groups = sorted(calls, key=lambda c: ids.index(c["seen"][0]))
    assert [c["seen"] for c in groups] == [ids[0:4], ids[4:8], ids[8:12]]
    assert [[s["screen"] for s in c["told"]["screens"]] for c in groups] == [ids[0:4], ids[4:8], ids[8:12]]
    assert [[e["detail"] for e in c["told"]["contract_errors"]] for c in groups] == \
        [["page-wide"], [], ["on the tenth screen"]]
    assert [f.element_id for f in critique.fixes] == [*ids[0:4], "shared", *ids[4:12]]
    assert next(f for f in critique.fixes if f.element_id == "shared").fix == f"group of {ids[0]}"
    assert critique.summary == " ".join([ids[0], ids[4], ids[8]])


@pytest.mark.parametrize("twelve", APPS, indirect=True)
def test_critic_groups_run_in_parallel_up_to_the_limit(twelve, monkeypatch):
    app, run_dir, model, _ = twelve
    monkeypatch.setattr(qa, "PARALLEL_CRITICS", 2)
    calls = []
    monkeypatch.setattr(llm, "call", fake_critic(calls, pause=0.2))
    criticize(run_dir, app, measured_twelve(run_dir, model))
    assert len(calls) == 3 and max(c["running"] for c in calls) == 2


@pytest.mark.parametrize("twelve", APPS, indirect=True)
def test_a_failed_group_is_skipped_and_only_all_failing_stops_the_round(twelve, monkeypatch):
    app, run_dir, model, ids = twelve
    version = measured_twelve(run_dir, model)
    monkeypatch.setattr(llm, "call", fake_critic([], fail_on={ids[4]}))
    critique = criticize(run_dir, app, version)
    assert [f.element_id for f in critique.fixes] == [*ids[0:4], "shared", *ids[8:12]]
    assert "group skipped" in read_trace(run_dir / "trace.jsonl")[-1].note
    monkeypatch.setattr(llm, "call", fake_critic([], fail_on={ids[0], ids[4], ids[8]}))
    with pytest.raises(llm.LLMFailure):
        criticize(run_dir, app, version)


@pytest.mark.parametrize("twelve", APPS, indirect=True)
def test_the_fixer_gets_only_the_screens_the_critique_names(twelve, monkeypatch):
    app, run_dir, model, ids = twelve
    with_elements = next(s for s in mock.pick_scope(model) if s.elements)
    page_wide = ContractError(kind="console_error", detail="page-wide", screen=None)
    critique = Critique(fixes=[Fix(element_id=ids[10], problem="p", fix="f"),
                               Fix(element_id=with_elements.elements[0].id, problem="p", fix="f"),
                               Fix(element_id="not.in.the.model", problem="p", fix="f")], summary="s")
    calls = []
    monkeypatch.setattr(llm, "call", fake_critic(calls))
    qa.fix(ctx_for(run_dir, app), llm.Budget("qa", 12.0), model, measured_twelve(run_dir, model, [page_wide]),
           critique, 1)
    named = [sid for sid in ids if sid in {ids[10], with_elements.id}]
    assert calls[0]["seen"] == named
    assert [s["screen"] for s in calls[0]["told"]["screens"]] == named
    assert [e["detail"] for e in calls[0]["told"]["contract_errors"]] == ["page-wide"]


@pytest.mark.parametrize("twelve", APPS, indirect=True)
def test_undrawn_screens_are_reported_and_never_scored_criticized_or_fixed(twelve, monkeypatch):
    app, run_dir, model, ids = twelve
    mock.copy_assets(run_dir / "model", run_dir / "mock", mock.pick_scope(model), model.device)
    html = qa.rebuild(skeleton_html(model), model, ids)
    undrawn = ids[4:8]
    page = run_dir / "mock" / "index.html"
    for sid in undrawn:
        html = re.sub(rf'(<section data-screen="{sid}"[^>]*>).*?(</section>)', r"\1<p>screen not drawn</p>\2", html,
                      flags=re.S)
    page.write_text(html)
    errors = [ContractError(kind="undrawn_screen", detail="screen not drawn: refusal", screen=sid) for sid in undrawn]
    (run_dir / "mock" / "contract_report.json").write_text(
        ContractReport(passed=False, screens=ids, errors=errors).model_dump_json())
    calls = []
    monkeypatch.setattr(llm, "call", fake_critic(calls))
    qa.run(ctx_for(run_dir, app))

    report = json.loads((run_dir / "qa" / "qa_report.json").read_text())
    drawn = [sid for sid in ids if sid not in undrawn]
    assert report["undrawn_screens"] == [{"screen": sid, "reason": "screen not drawn: refusal"} for sid in undrawn]
    assert [s["state_id"] for s in report["screens"]] == drawn
    assert calls and all(set(c["seen"]).isdisjoint(undrawn) for c in calls)
    assert report["status"] == "approved" and not report["contract_errors"]
    edges = {e.id: e for e in mock.scope_edges(model, mock.pick_scope(model))}
    for flow in (f for f in model.flows if f.edge_ids and all(i in edges for i in f.edge_ids)):
        crosses = any({edges[i].from_state, edges[i].to_state} & set(undrawn) for i in flow.edge_ids)
        status = next(w["status"] for w in report["flows"] if w["flow"] == flow.id)
        assert status == ("undrawn" if crosses else "passed")
    assert "didn't draw" in (run_dir / "exhibits" / "04-qa.md").read_text()
