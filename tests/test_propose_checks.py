import pytest

from simula.stages.propose import anchor_ids, check, daily_cap, depths, finish
from tests.conftest import APPS
from tests.propose_fixtures import anchored, candidate, golden


@pytest.fixture(params=APPS)
def model(request):
    return golden(request.param)


def plain_element(model):
    return next(e.id for s in model.states for e in s.elements if e.id not in anchor_ids(model))


def test_a_valid_product_change_passes(model):
    assert check(candidate(model), model) is None


def test_a_valid_existing_anchor_passes_when_the_model_has_one(model):
    if not anchor_ids(model):
        pytest.skip("no paywall, limit, currency, or entitlement to anchor on")
    assert check(anchored(model), model) is None


def test_existing_anchor_citing_a_plain_button_is_dropped(model):
    c = candidate(model, kind="existing_anchor", anchor_evidence_ids=[plain_element(model)], adds=None)
    assert "cites no paywall" in check(c, model)


def test_inference_reward_without_tokens_is_dropped(model):
    c = candidate(model, reward={"kind": "inference", "unit": "replies", "amount": 3, "duration": "today"})
    assert "token" in check(c, model)


def test_product_change_without_adds_is_dropped(model):
    assert "adds" in check(candidate(model, adds=None), model)
    assert "adds" in check(candidate(model, adds="  "), model)


def test_product_change_that_removes_something_free_is_dropped(model):
    assert "free" in check(candidate(model, removes_nothing_free=False), model)


@pytest.mark.parametrize("placement", ["Inside the chat transcript, after the last reply",
                                       "An in-chat card between messages", "A message bubble from the character"])
def test_chat_transcript_placement_is_dropped(model, placement):
    assert "chat transcript" in check(candidate(model, placement=placement), model)


@pytest.mark.parametrize("placement", ["A banner above the chat list", "A sheet over the paywall",
                                       "A card on the pet screen (not in chat)", "A banner in the chat list header",
                                       "A sheet over the feed. Never shown in a chat."])
def test_app_chrome_placement_passes(model, placement):
    assert check(candidate(model, placement=placement), model) is None


def test_made_up_ids_are_dropped(model):
    assert "don't exist" in check(candidate(model, anchor_evidence_ids=["s99.e01"]), model)
    assert "doesn't exist" in check(candidate(model, trigger_state_id="s99"), model)
    assert "don't exist" in check(candidate(model, flow_steps=[{"state_id": "s99", "caption": "x"}]), model)
    assert "M-id" in check(candidate(model, bible_mechanic="M99"), model)
    assert check(candidate(model, bible_mechanic="none"), model) is None


def test_finish_numbers_prices_and_caps(model):
    drafts = [candidate(model) for _ in range(11)] + [candidate(model, kind="no_opportunity", rationale="nothing")]
    out = finish(drafts, model, "annotate")
    live = [c for c in out if not c.dropped_reason]
    assert len(live) == 10 and all(c.economics and c.reach_score for c in live)
    assert sorted(c.id for c in out) == [f"c{n:02d}" for n in range(1, 13)]
    reasons = sorted(c.dropped_reason for c in out if c.dropped_reason)
    assert reasons == ["no opportunity: nothing", "over the 10-candidate cap"]


def test_reach_follows_the_trigger_depth(model):
    depth = depths(model)
    root = model.states[0].id
    assert depth[root] == 0
    for e in model.edges:
        if e.from_state == root and e.transition == "tab":
            assert depth[e.to_state] == 0
        if e.from_state == root:
            assert depth[e.to_state] <= 1
    assert all(s.id in depth for s in model.states)
    live = finish([candidate(model, frequency_cap="3 per day")], model, "annotate")[0]
    assert live.reach_score == 3.0


def test_daily_cap_reads_the_first_number():
    assert daily_cap("3 per day, resets at midnight") == 3
    assert daily_cap("once a day") == 1


def test_mechanic_ledger_and_element_ids_resolve_to_what_they_point_at(model):
    if not anchor_ids(model):
        pytest.skip("no anchor to cite")
    mechanic = next(m for m in model.mechanics if set(m.evidence_ids) & anchor_ids(model))
    element = mechanic.evidence_ids[0]
    c = anchored(model, anchor_evidence_ids=[mechanic.id], trigger_state_id=element,
                 flow_steps=[{"state_id": element, "caption": "x"}, {"state_id": "new:offer", "caption": "y"}])
    out = finish([c], model, "annotate")[0]
    assert out.dropped_reason is None
    assert out.anchor_evidence_ids == mechanic.evidence_ids
    assert out.trigger_state_id == out.flow_steps[0].state_id == element.split(".")[0]
