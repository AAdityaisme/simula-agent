"""The QA loop with a fake critic and fixer: the 0.3 stop rule, the discard rule, the best version approved, the
fixer's effort per round, a failed model call or a broken page that never blocks the slides, and a replay that
doesn't depend on render bytes."""

import json
from dataclasses import replace

import pytest
from PIL import Image

from simula import config, llm, render
from simula.contracts import ContractError, ContractReport, Critique, Edit, Edits, Fix, ProductModel, QAMetrics
from simula.runlog import read_trace
from simula.stages import mock, qa
from tests.conftest import APPS
from tests.mock_fake import seed_model
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
    run_dir = seed_model(tmp_path / "run", app)
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))
    delivered = (run_dir / "mock" / "index.html").read_text()
    first = next(t["attrs"]["data-el"] for t in mock.StartTags(delivered).tags if "data-el" in t["attrs"])
    rounds = {1: [Edit(find="</body>", replace="<!-- round 1 --></body>", reason="r")],
              2: [Edit(find=f'data-el="{first}"', replace=f'data-x="{first}"', reason="r")]}
    monkeypatch.setattr(llm, "call", fake_llm([], lambda n: rounds.get(n, [])))
    qa.run(ctx_for(run_dir, app))
    recorded = json.loads((run_dir / "qa" / "qa_report.json").read_text())

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

    for record in records.iterdir():
        record.unlink()
    with pytest.raises(llm.ReplayMiss):
        qa.run(replace(ctx_for(run_dir, app), replay=True))
