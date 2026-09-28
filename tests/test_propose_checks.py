import pytest

from simula import llm
from simula.contracts import CandidateDraft, LedgerItem, LensOutput
from simula.stages import Ctx, propose
from simula.stages.propose import anchor_ids, check, daily_cap, depths, finish, free_offers, in_chat
from tests.conftest import APPS
from tests.propose_fixtures import anchored, candidate, golden, root


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
                                       "An in-chat card between messages", "A message bubble from the character",
                                       "A card inside the chat that does not block typing",
                                       "A banner inside the chat, with no close button",
                                       "A card inside the chat screen", "A pinned card in your chat"])
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


def distinct(model):
    return [candidate(model, title="A badge for a week", reward={"kind": "cosmetic", "unit": "badge", "amount": 1,
                                                                 "duration": "7 days"}),
            candidate(model, title="Keep your streak", reward={"kind": "streak_protection", "unit": "save",
                                                                "amount": 1, "duration": "one missed day"}),
            candidate(model, title="Faster replies for an hour", reward={"kind": "queue_priority", "unit": "hour",
                                                                         "amount": 1, "duration": "1 hour"})]


def test_finish_numbers_prices_labels_and_caps(model, monkeypatch):
    monkeypatch.setattr(propose, "MAX_CANDIDATES", 2)
    drafts = distinct(model) + [candidate(model, kind="no_opportunity", title="Nothing here", rationale="nothing")]
    out, _ = finish(drafts, model, "annotate")
    live = [c for c in out if not c.dropped_reason]
    assert len(live) == 2 and all(c.economics and c.reach_score for c in live)
    assert sorted(c.id for c in out) == ["c01", "c02", "c03", "c04"]
    assert sorted(c.dropped_reason for c in out if c.dropped_reason) == ["no opportunity: nothing",
                                                                         "over the 2-candidate cap"]
    assert all(c.title.startswith("Product change: ") for c in out if c.kind == "product_change")
    assert next(c for c in out if c.kind == "no_opportunity").title == "Nothing here"


def test_existing_opportunity_label(model):
    if not anchor_ids(model):
        pytest.skip("no anchor to cite")
    [out], _ = finish([anchored(model, title="Unlock one more for 3 days")], model, "annotate")
    assert out.title == "Existing opportunity: Unlock one more for 3 days"


def other_screen(model):
    return next(s.id for s in model.states
                if s.in_mock_scope and s.id != root(model) and s.content_rating in propose.SAFE_TRIGGER_RATINGS)


def test_the_same_reward_on_different_screens_is_a_duplicate(model):
    badge = {"kind": "cosmetic", "unit": "Gold  Badge", "amount": 1, "duration": "7 days"}
    elsewhere = {**badge, "unit": "gold badges"}
    drafts = [candidate(model, reward=badge),
              candidate(model, reward=elsewhere, trigger_state_id=other_screen(model), frequency_cap="5 per day")]
    by_id = {c.id: c for c in finish(drafts, model, "annotate")[0]}
    assert by_id["c02"].dropped_reason is None
    assert by_id["c01"].dropped_reason == "duplicate of c02: same reward (gold badges)"


def test_different_rewards_on_one_screen_are_both_kept(model):
    badge = {"kind": "cosmetic", "unit": "badge", "amount": 1, "duration": "7 days"}
    drafts = [candidate(model, reward=badge), candidate(model, reward={**badge, "unit": "profile frame"})]
    assert [c.dropped_reason for c in finish(drafts, model, "annotate")[0]] == [None, None]


def test_no_after_reward_is_dropped(model):
    assert "runs out" in check(candidate(model, after_reward="  "), model)


def test_a_taste_of_what_a_free_trial_already_gives_is_dropped(model):
    if not anchor_ids(model):
        pytest.skip("no anchor to cite")
    trial = LedgerItem(id="t1", kind="paywall_bullet", verbatim="Start\xa07-day free trial", evidence_ids=[])
    with_trial = model.model_copy(update={"value_ledger": model.value_ledger + [trial]})
    taste = anchored(model, title="Try the paid feature for a day")
    assert "already offers this" in check(taste, with_trial)
    assert check(anchored(model, title="One more slot for 3 days"), with_trial) is None
    assert check(candidate(model, title="Try a new theme"), with_trial) is None
    if not free_offers(model):
        assert check(taste, model) is None


def test_non_breaking_spaces_are_plain_spaces():
    assert in_chat("Inside\xa0the chat\xa0transcript")
    assert daily_cap("3\xa0per\xa0day") == 3


def test_reach_follows_the_trigger_depth(model):
    depth = depths(model)
    root_id = root(model)
    assert depth[root_id] == 0
    for e in model.edges:
        if e.from_state == root_id and e.transition == "tab":
            assert depth[e.to_state] == 0
        if e.from_state == root_id:
            assert depth[e.to_state] <= 1
    assert all(s.id in depth for s in model.states)
    live = finish([candidate(model, frequency_cap="3 per day")], model, "annotate")[0][0]
    assert live.reach_score == 3.0


@pytest.mark.parametrize("text, cap", [("3 per day, resets at midnight", 3), ("once a day", 1),
                                       ("every 24 hours", 1), ("resets at 00:00 UTC; 3 per day", 3),
                                       ("after 3+ days away, once a day", 1), ("2 times a day", 2), ("3/day", 3)])
def test_daily_cap_reads_a_per_day_count_only(text, cap):
    assert daily_cap(text) == cap


def test_mechanic_ledger_and_element_ids_resolve_to_what_they_point_at(model):
    if not anchor_ids(model):
        pytest.skip("no anchor to cite")
    mechanic = next(m for m in model.mechanics if set(m.evidence_ids) & anchor_ids(model))
    element = mechanic.evidence_ids[0]
    c = anchored(model, anchor_evidence_ids=[mechanic.id], trigger_state_id=element,
                 flow_steps=[{"state_id": element, "caption": "x"}, {"state_id": "new:offer", "caption": "y"}])
    [out], repairs = finish([c], model, "annotate")
    assert out.dropped_reason is None
    assert out.anchor_evidence_ids == mechanic.evidence_ids
    assert repairs == {"c01": f"{mechanic.id} -> {','.join(mechanic.evidence_ids)}; {element} -> {element.split('.')[0]}"}
    assert out.trigger_state_id == out.flow_steps[0].state_id == element.split(".")[0]


def test_a_trigger_outside_the_mock_scope_is_dropped(model):
    outside = [s for s in model.states if not s.in_mock_scope]
    if not outside:
        pytest.skip("every state is in scope")
    assert "outside the mock scope" in check(candidate(model, trigger_state_id=outside[0].id), model)
    steps = [{"state_id": root(model), "caption": "x"}, {"state_id": outside[0].id, "caption": "y"}]
    assert "outside the mock scope" in check(candidate(model, flow_steps=steps), model)


@pytest.mark.parametrize("rating", ["unsafe", "unknown"])
def test_a_trigger_next_to_unsafe_or_unknown_content_is_dropped(model, rating):
    states = [s.model_copy(update={"content_rating": rating}) if s.id == root(model) else s for s in model.states]
    rated = model.model_copy(update={"states": states})
    assert f"{rating} content" in check(candidate(rated), rated)


def run_with(model, tmp_path, monkeypatch, fail_lenses):
    (tmp_path / "model").mkdir()
    (tmp_path / "propose").mkdir()
    (tmp_path / "model" / "product_model.json").write_text(model.model_dump_json())
    draft = CandidateDraft(**{k: v for k, v in candidate(model).model_dump().items() if k in CandidateDraft.model_fields})

    def fake_call(*, step, **_):
        if step.removeprefix("lens:") in fail_lenses:
            raise llm.LLMFailure("timeout", "provider down")
        return LensOutput(candidates=[draft]), None

    monkeypatch.setattr(llm, "call", fake_call)
    ctx = Ctx(app={"name": model.app}, run_dir=tmp_path, profile="dev", no_cache=False, replay=False,
              usd_cap=None, allow_fixtures=True)
    propose.run(ctx)


def test_every_lens_failing_fails_the_stage(model, tmp_path, monkeypatch):
    every = {lens.id for lens in propose.build_lenses(model)}
    with pytest.raises(RuntimeError, match="every lens call failed"):
        run_with(model, tmp_path, monkeypatch, every)
    assert not (tmp_path / "propose" / "candidates.json").exists()


def test_one_lens_failing_still_finishes(model, tmp_path, monkeypatch):
    run_with(model, tmp_path, monkeypatch, {"free_at_limit"})
    assert (tmp_path / "propose" / "candidates.json").exists()
