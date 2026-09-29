"""Stage 3's code paths around each batch's model call: scope limits, tagging, retry, the wall, HTML extraction."""

import pytest

from simula import llm
from simula.runlog import read_trace
from simula.stages import mock
from tests.conftest import APPS
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import ctx_for, fake_builder


@pytest.fixture(params=APPS)
def app(request):
    return request.param


def test_scope_is_exactly_the_model_stages_scope_whatever_the_rating(app):
    model = golden(app)
    states = [s.model_copy(update={"in_mock_scope": i % 2 == 0}) for i, s in enumerate(model.states)]
    states[0] = states[0].model_copy(update={"content_rating": "unsafe"})
    scope = mock.pick_scope(model.model_copy(update={"states": states, "mock_order": []}))
    assert [s.id for s in scope] == [s.id for s in states if s.in_mock_scope]


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
        brief = mock.state_brief(state, model.device, {})
        offered = {e["id"] for e in brief["elements"] if "asset" in e}
        assert offered == {e.id for e in state.elements if mock.usable_asset(e, state.elements, model.device)}


BRIEF = [{"type": "text", "text": "the batch brief"}]


def budget() -> llm.Budget:
    return llm.Budget("mock", 15.0)


def test_max_tokens_retries_once_at_high_with_shorter_css(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", "janitorai")
    efforts = []

    def call(**kwargs):
        efforts.append((kwargs["effort"], kwargs["messages"][0]["content"][-1]["text"]))
        if len(efforts) == 1:
            raise llm.LLMFailure("max_tokens", "cut off")
        return "```html\n<html><body></body></html>\n```", None
    monkeypatch.setattr(llm, "call", call)
    html = mock.generate(ctx_for(run_dir, "janitorai", profile="real"), BRIEF, budget(), "batch1")
    assert html.startswith("<html>")
    assert efforts[0][0] == "xhigh" and efforts[1] == ("high", mock.SHORTER)
    assert read_trace(run_dir / "trace.jsonl")[-1].outcome == "retry"


def test_other_failures_are_not_retried(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", "luzia")

    def call(**kwargs):
        raise llm.LLMFailure("refusal", "no")
    monkeypatch.setattr(llm, "call", call)
    with pytest.raises(llm.LLMFailure):
        mock.generate(ctx_for(run_dir, "luzia"), BRIEF, budget(), "batch1")


def test_html_comes_out_of_a_fence_or_a_bare_document():
    assert mock.extract_html("x\n```html\n<p>a</p>\n```") == "<p>a</p>\n"
    assert mock.extract_html("sure: <!DOCTYPE html><html></html> done") == "<!DOCTYPE html><html></html>"
    with pytest.raises(ValueError):
        mock.extract_html('{"html": "<p>"}')


def test_runtime_goes_inside_head_and_body(app):
    html = mock.with_runtime(skeleton_html(golden(app)), "s01")
    assert html.index('id="simula-runtime"') < html.index("</head>")
    assert html.index('id="simula-runtime-js"') < html.index("</body>")


def attrs_of(html: str) -> list[dict]:
    return [t["attrs"] for t in mock.StartTags(html).tags]


def test_every_known_edge_tag_gets_its_transition_however_it_is_written(app):
    model = golden(app)
    screens = [s.id for s in mock.pick_scope(model)]
    edge = mock.scope_edges(model, mock.pick_scope(model))[0]
    escaped = edge.id.replace(">", "&gt;")
    html = (f'<a data-edge="{edge.id}">x</a>'
            f'<b data-transition="modal" data-edge="{edge.id}"></b>'
            f"<i data-edge='{edge.id}'></i>"
            f'<u data-edge="{escaped}"></u>'
            f'<s title="a>b" data-edge="{edge.id}"/>'
            '<i data-edge="new:x" data-transition="push"></i><p>untouched &amp; text</p>')
    wired = mock.wire_edges(html, model, screens)
    stamped = [a for a in attrs_of(wired) if a.get("data-edge") == edge.id]
    assert len(stamped) == 5 and all(a["data-transition"] == edge.transition for a in stamped)
    assert {"data-edge": "new:x", "data-transition": "push"} in attrs_of(wired)
    assert wired.endswith('<i data-edge="new:x" data-transition="push"></i><p>untouched &amp; text</p>')
    assert mock.wire_edges(wired, model, screens) == wired


def test_a_missing_edge_goes_on_the_tag_that_carries_its_element(app):
    model = golden(app)
    screens = [s.id for s in mock.pick_scope(model)]
    edges = mock.scope_edges(model, mock.pick_scope(model))
    first, others = edges[0], [e for e in edges[1:] if e.element_id != edges[0].element_id]
    html = f'<div data-el="{first.element_id}" class="tab">x</div>'
    if others:
        html += f'<div data-el="{others[0].element_id}" data-edge="{first.id}"></div>'
    wired = attrs_of(mock.wire_edges(html, model, screens))
    if others:
        assert wired[0] == {"data-el": first.element_id, "class": "tab"}
        assert wired[1]["data-edge"] == first.id
    else:
        assert wired[0] == {"data-el": first.element_id, "class": "tab", "data-edge": first.id,
                            "data-transition": first.transition}
    out_of_scope = [sid for sid in screens if sid != first.to_state]
    assert "data-edge" not in attrs_of(mock.wire_edges(html, model, out_of_scope))[0]


def test_an_edge_without_an_element_is_never_placed(app):
    model = golden(app)
    screens = [s.id for s in mock.pick_scope(model)]
    edge = mock.scope_edges(model, mock.pick_scope(model))[0].model_copy(update={"element_id": None})
    model = model.model_copy(update={"edges": [edge]})
    html = "<html><body><div>x</div></body></html>"
    assert mock.wire_edges(html, model, screens) == html


def test_applying_the_runtime_twice_leaves_exactly_one(app):
    html = skeleton_html(golden(app))
    twice = mock.with_runtime(mock.with_runtime(html, "s01"), "s02")
    assert twice.count('id="simula-runtime"') == 1 and twice.count('id="simula-runtime-js"') == 1
    assert 'const ROOT = "s02"' in twice
    assert mock.with_runtime(twice, "s02") == twice


def test_each_attempt_is_one_call_under_its_own_wall_and_a_timeout_is_not_retried(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", "aol")
    calls = []

    def call(**kwargs):
        calls.append((kwargs["effort"], kwargs["attempts"], kwargs["total_timeout"]))
        if len(calls) == 1:
            raise llm.LLMFailure("max_tokens", "cut off")
        raise llm.LLMFailure("timeout", "passed the wall")
    monkeypatch.setattr(llm, "call", call)
    with pytest.raises(llm.LLMFailure) as e:
        mock.generate(ctx_for(run_dir, "aol", profile="real"), BRIEF, budget(), "batch1")
    assert e.value.outcome == "timeout"
    assert calls == [("xhigh", 1, mock.WALL_SECONDS), ("high", 1, mock.WALL_SECONDS)]


def test_the_mock_opens_on_the_first_screen_that_is_not_a_dialog(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    home = mock.pick_scope(model)[0]
    dialog = home.model_copy(update={"id": "s00", "kind": "modal", "parent_id": home.id, "elements": []})
    model = model.model_copy(update={"states": [dialog] + model.states, "mock_order": ["s00", *model.mock_order]})
    (run_dir / "model" / "product_model.json").write_text(model.model_dump_json())
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))
    assert mock.pick_scope(model)[0].id == "s00" and mock.home_id(mock.pick_scope(model)) == home.id
    assert f'const ROOT = "{home.id}"' in (run_dir / "mock" / "index.html").read_text()
