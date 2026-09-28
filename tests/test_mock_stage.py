"""Stage 3's code paths around the one model call: scope limits, tagging, retry, the wall, HTML extraction."""

import time

import pytest

from simula import llm
from simula.runlog import read_trace
from simula.stages import mock
from tests.conftest import APPS
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import ctx_for


@pytest.fixture(params=APPS)
def app(request):
    return request.param


def test_scope_never_holds_unsafe_or_blocked_states_and_stops_at_8(app):
    model = golden(app)
    states = [s.model_copy(update={"in_mock_scope": True}) for s in model.states]
    states[0] = states[0].model_copy(update={"content_rating": "unsafe"})
    scope = mock.pick_scope(model.model_copy(update={"states": states}))
    assert len(scope) <= mock.MAX_SCREENS
    assert all(s.content_rating != "unsafe" and s.kind != "blocked" for s in scope)
    assert states[0].id not in {s.id for s in scope}


def test_only_the_first_two_items_of_a_repeated_list_are_tagged(app):
    state = next(s for s in golden(app).states if len([e for e in s.elements if e.in_mock]) >= 4)
    listed = [e for e in state.elements if e.in_mock][:4]
    repeated = {e.id for e in listed}
    state = state.model_copy(update={"elements": [e.model_copy(update={"repeat_group": "g1"}) if e.id in repeated else e
                                                  for e in state.elements]})
    tagged = mock.tagged_ids(state)
    assert {e.id for e in listed[:2]} <= tagged
    assert not {e.id for e in listed[2:]} & tagged


def test_no_offered_asset_breaks_the_wallpaper_rule(app):
    model = golden(app)
    for state in mock.pick_scope(model):
        brief = mock.state_brief(state, model.device)
        offered = {e["id"] for e in brief["elements"] if "asset" in e}
        assert offered == {e.id for e in state.elements if mock.usable_asset(e, model.device)}


def test_max_tokens_retries_once_at_high_with_shorter_css(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", "janitorai")
    efforts = []

    def call(**kwargs):
        efforts.append((kwargs["effort"], kwargs["messages"][0]["content"][-1]["text"]))
        if len(efforts) == 1:
            raise llm.LLMFailure("max_tokens", "cut off")
        return "```html\n<html><body></body></html>\n```", None
    monkeypatch.setattr(llm, "call", call)
    model = golden("janitorai")
    html = mock.generate(ctx_for(run_dir, "janitorai", profile="real"), model, mock.pick_scope(model))
    assert html.startswith("<html>")
    assert efforts[0][0] == "xhigh" and efforts[1] == ("high", mock.SHORTER)
    assert read_trace(run_dir / "trace.jsonl")[-1].outcome == "retry"


def test_other_failures_are_not_retried(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", "luzia")

    def call(**kwargs):
        raise llm.LLMFailure("refusal", "no")
    monkeypatch.setattr(llm, "call", call)
    model = golden("luzia")
    with pytest.raises(llm.LLMFailure):
        mock.generate(ctx_for(run_dir, "luzia"), model, mock.pick_scope(model))


def test_generation_stops_at_the_wall(tmp_path):
    ctx = ctx_for(tmp_path, "aol")
    with pytest.raises(llm.LLMFailure) as e:
        mock.within_wall(lambda: time.sleep(5), 0.1, ctx)
    assert e.value.outcome == "timeout"
    assert read_trace(tmp_path / "trace.jsonl")[-1].outcome == "timeout"
    assert mock.within_wall(lambda: 7, 1, ctx) == 7


def test_html_comes_out_of_a_fence_or_a_bare_document():
    assert mock.extract_html("x\n```html\n<p>a</p>\n```") == "<p>a</p>\n"
    assert mock.extract_html("sure: <!DOCTYPE html><html></html> done") == "<!DOCTYPE html><html></html>"
    with pytest.raises(ValueError):
        mock.extract_html('{"html": "<p>"}')


def test_runtime_goes_inside_head_and_body(app):
    html = mock.with_runtime(skeleton_html(golden(app)), "s01")
    assert html.index('id="simula-runtime"') < html.index("</head>")
    assert html.index('id="simula-runtime-js"') < html.index("</body>")


def test_code_stamps_each_known_edge_with_its_transition(app):
    model = golden(app)
    edge = model.edges[0]
    html = (f'<a data-edge="{edge.id}">x</a><b data-transition="modal" data-edge="{edge.id}"></b>'
            '<i data-edge="new:x" data-transition="push"></i>')
    stamped = mock.stamp_transitions(html, model)
    assert stamped.count(f'data-edge="{edge.id}" data-transition="{edge.transition}"') == 2
    assert 'data-transition="modal"' not in stamped or edge.transition == "modal"
    assert '<i data-edge="new:x" data-transition="push"></i>' in stamped
