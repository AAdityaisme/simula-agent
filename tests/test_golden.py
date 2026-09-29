"""App-neutral checks on every golden product model. A change tuned to one app should fail the others."""

import pytest
from PIL import Image

from simula.contracts import ProductModel
from tests.conftest import APPS, FIXTURES


@pytest.fixture(params=APPS)
def model(request) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / request.param / "product_model.json").read_text())


def elements(model):
    return {e.id: e for s in model.states for e in s.elements}


def test_is_fixture_test_data(model):
    assert model.provenance.source == "fixture"
    assert model.provenance.fixture_path == f"tests/fixtures/golden/{model.app}"


def test_ids_are_unique_and_scoped_to_their_state(model):
    state_ids = [s.id for s in model.states]
    assert len(state_ids) == len(set(state_ids))
    for state in model.states:
        assert all(e.id.startswith(state.id + ".") for e in state.elements)
        assert state.parent_id is None or state.parent_id in state_ids


def test_every_evidence_id_resolves(model):
    known = elements(model)
    cited = [i for m in model.mechanics for i in m.evidence_ids] + [i for l in model.value_ledger for i in l.evidence_ids]
    cited += [i for v in model.cross_screen_values for i in v.evidence_ids] + [i for f in model.flows for i in f.evidence_ids]
    assert set(cited) <= set(known)


def test_edges_and_flows_connect(model):
    states, edges = {s.id for s in model.states}, {e.id: e for e in model.edges}
    known = elements(model)
    for edge in model.edges:
        assert {edge.from_state, edge.to_state} <= states
        assert edge.element_id in known and edge.element_id.startswith(edge.from_state + ".")
    for flow in model.flows:
        hops = [edges[i] for i in flow.edge_ids]
        assert hops and all(a.to_state == b.from_state for a, b in zip(hops, hops[1:]))


def test_every_rect_dp_follows_the_formula(model):
    d = model.device
    for e in elements(model).values():
        assert e.rect_dp.x == pytest.approx(e.rect_px.x / d.scale, abs=0.01)
        assert e.rect_dp.y == pytest.approx((e.rect_px.y - d.content_top_px) / d.scale, abs=0.01)
        assert e.rect_dp.w == pytest.approx(e.rect_px.w / d.scale, abs=0.01)
        assert e.rect_dp.h == pytest.approx(e.rect_px.h / d.scale, abs=0.01)


def test_ledger_text_is_verbatim(model):
    known = elements(model)
    for item in model.value_ledger:
        assert any(item.verbatim in (known[i].text + " " + known[i].label) for i in item.evidence_ids), item.id


def test_money_mechanics_cite_elements(model):
    for m in model.mechanics:
        if m.kind in ("paywall", "limit", "currency"):
            assert m.evidence_ids, m.id


def test_every_paywall_mechanic_has_three_verbatim_bullets(model):
    for m in model.mechanics:
        if m.kind == "paywall":
            cited = {i.split(".")[0] for i in m.evidence_ids}
            bullets = [l for l in model.value_ledger
                       if l.kind == "paywall_bullet" and {i.split(".")[0] for i in l.evidence_ids} & cited]
            assert len(bullets) >= 3, m.id


def test_dynamic_regions_sit_inside_the_content_area(model):
    d = model.device
    for state in model.states:
        for r in state.dynamic_regions:
            assert 0 <= r.x and r.x + r.w <= d.w_px, state.id
            assert d.content_top_px <= r.y and r.y + r.h <= d.content_bottom_px, state.id


def test_files_exist_with_the_content_crop_size(model):
    root = FIXTURES / "golden" / model.app
    for state in model.states:
        png = Image.open(root / state.canonical_png)
        assert png.size == (model.device.w_px, model.device.content_bottom_px - model.device.content_top_px)
    for e in elements(model).values():
        if e.asset_png:
            assert Image.open(root / e.asset_png).size == (int(e.rect_px.w), int(e.rect_px.h))


def test_mock_scope_is_stage_2s_rule_in_its_priority_order(model):
    by_id, edges = {s.id: s for s in model.states}, {e.id: e for e in model.edges}
    on_flow = {sid for f in model.flows for i in f.edge_ids for sid in (edges[i].from_state, edges[i].to_state)}
    cited = {i.split(".")[0] for m in model.mechanics for i in m.evidence_ids}
    drawable = {sid for sid in on_flow | cited if by_id[sid].kind not in ("blocked", "external")
                and (by_id[sid].content_rating != "unsafe" or sid in on_flow)}
    assert model.mock_order[0] == next(s.id for s in model.states if s.kind == "screen")
    assert set(model.mock_order) == {s.id for s in model.states if s.in_mock_scope}
    assert drawable <= set(model.mock_order)
    assert all(by_id[sid].kind not in ("blocked", "external") for sid in model.mock_order)


def test_code_owned_transition_matches_the_states(model):
    by_id = {s.id: s for s in model.states}
    for edge in model.edges:
        if by_id[edge.to_state].kind in ("modal", "sheet"):
            assert edge.transition == "modal"
