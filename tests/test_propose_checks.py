import time

import pytest

from simula import llm
from simula.contracts import CandidateDraft, CandidatesFile, LedgerItem, LensOutput, Mechanic, Term
from simula.stages import Ctx, propose
from simula.stages.propose import anchor_ids, check, daily_cap, depths, finish, in_chat
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
    out, *_ = finish(drafts, model, "annotate")
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
    [out], *_ = finish([anchored(model, title="Unlock one more for 3 days")], model, "annotate")
    assert out.title == "Existing opportunity: Unlock one more for 3 days"


def other_screen(model):
    return next(s.id for s in model.states
                if s.in_mock_scope and s.id != root(model) and s.content_rating in propose.SAFE_TRIGGER_RATINGS)


def test_the_same_benefit_name_on_different_screens_is_a_duplicate(model):
    badge = {"kind": "cosmetic", "unit": "badge", "amount": 1, "duration": "7 days"}
    drafts = [candidate(model, reward=badge),
              candidate(model, reward={**badge, "unit": "profile flair"}, trigger_state_id=other_screen(model),
                        frequency_cap="5 per day")]
    named = {"c01": "Gold  Badges", "c02": "gold-badge"}
    by_id = {c.id: c for c in finish(drafts, model, "annotate", lambda live: (named, {}))[0]}
    assert by_id["c02"].dropped_reason is None
    assert by_id["c01"].dropped_reason == "duplicate of c02: same benefit (gold-badge)"


def test_the_same_paid_benefit_for_the_same_users_is_a_duplicate_whatever_the_name(model):
    meter = LedgerItem(id="x1", kind="meter", verbatim="3 chats left", evidence_ids=[])
    metered = model.model_copy(update={"value_ledger": model.value_ledger + [meter]})
    drafts = [candidate(metered, title="Two more chats", grants_id="x1"),
              candidate(metered, title="Keep talking a little longer", grants_id="x1", frequency_cap="3 per day")]
    by_id = {c.id: c for c in finish(drafts, metered, "annotate")[0]}
    assert by_id["c02"].dropped_reason is None
    assert by_id["c01"].dropped_reason == "duplicate of c02: same benefit (x1 for free users)"


@pytest.mark.parametrize("a_users, b_users, twins", [("free", "paying", False), ("paying", "free", False),
                                                     ("free", "everyone", True), ("everyone", "paying", True),
                                                     ("paying", "paying", True)])
def test_a_shared_name_merges_only_overlapping_users(model, a_users, b_users, twins):
    drafts = [candidate(model, for_users=a_users), candidate(model, for_users=b_users)]
    out = finish(drafts, model, "annotate", lambda live: ({"c01": "no ads", "c02": "No ads"}, {}))[0]
    assert sum(bool(c.dropped_reason) for c in out) == twins


def test_the_same_paid_benefit_for_different_users_is_not_a_duplicate(model):
    meter = LedgerItem(id="x1", kind="meter", verbatim="3 chats left", evidence_ids=[])
    metered = model.model_copy(update={"value_ledger": model.value_ledger + [meter]})
    drafts = [candidate(metered, grants_id="x1", for_users="free"), candidate(metered, grants_id="x1",
                                                                               for_users="everyone")]
    assert [c.dropped_reason for c in finish(drafts, metered, "annotate")[0]] == [None, None]


def test_different_names_or_no_names_keep_both(model):
    drafts = [candidate(model), candidate(model)]
    assert [c.dropped_reason for c in finish(drafts, model, "annotate")[0]] == [None, None]
    named = lambda live: ({"c01": "badge", "c02": "profile frame"}, {})
    assert [c.dropped_reason for c in finish(drafts, model, "annotate", named)[0]] == [None, None]


def test_an_idea_is_only_a_duplicate_of_one_that_was_kept(model):
    drafts = [candidate(model, for_users="free", frequency_cap="3 per day"),
              candidate(model, for_users="everyone", frequency_cap="2 per day"), candidate(model, for_users="paying")]
    out = finish(drafts, model, "annotate", lambda live: ({c.id: "no ads" for c in live}, {}))[0]
    assert {c.id: c.dropped_reason for c in out} == {"c01": None, "c02": "duplicate of c01: same benefit (no ads)",
                                                      "c03": None}


def test_no_after_reward_is_dropped(model):
    assert "runs out" in check(candidate(model, after_reward="  "), model)


def paywalled(model, bullet, limit=False):
    """The golden plus a synthetic paywall bullet `b1` on its paywall screen, a free trial on that screen, and
    optionally an observed limit on the bullet."""
    screen = next((i.evidence_ids[0].split(".")[0] for i in model.value_ledger if i.kind == "paywall_bullet"), None)
    if screen is None:
        pytest.skip("no paywall")
    bullet_id = f"{screen}.e999"
    trial = next(e for s in model.states for e in s.elements).model_copy(
        update={"id": f"{screen}.e998", "text": "Start 3-day free\xa0trial", "label": ""})
    states = [s.model_copy(update={"elements": s.elements + [trial]}) if s.id == screen else s for s in model.states]
    cap = Mechanic(id="mx", kind="limit", evidence_ids=[bullet_id], summary="A cap", observed_numbers=[],
                   status="observed")
    return model.model_copy(update={
        "states": states, "mechanics": model.mechanics + [cap] * limit,
        "value_ledger": model.value_ledger + [LedgerItem(id="b1", kind="paywall_bullet", verbatim=bullet,
                                                         evidence_ids=[bullet_id])]})


@pytest.mark.parametrize("users", ["free", "everyone", "paying"])
def test_a_piece_of_a_bullet_with_a_free_trial_on_its_screen_is_kept(model, users):
    m = paywalled(model, "Up to 5 chats a day")
    assert check(candidate(m, grants_id="b1", for_users=users), m) is None


def test_more_of_a_bullet_with_a_number_for_payers_is_kept(model):
    m = paywalled(model, "Up to 5 chats a day")
    assert check(candidate(m, grants_id="b1", for_users="paying"), m) is None


def test_more_of_a_bullet_nobody_counted_for_payers_is_dropped(model):
    m = paywalled(model, "Smarter replies")
    assert "no amount or cap for it was observed" in check(candidate(m, grants_id="b1", for_users="paying"), m)
    capped = paywalled(model, "Smarter replies", limit=True)
    assert check(candidate(capped, grants_id="b1", for_users="paying"), capped) is None


def test_a_new_resource_or_an_unknown_ledger_id(model):
    m = paywalled(model, "Smarter replies")
    assert check(candidate(m, grants_id=None, for_users="free"), m) is None
    assert "is not a ledger id" in check(candidate(m, grants_id="nope"), m)


def test_no_paywall_means_no_grants_rule_fires(model):
    if any(i.kind == "paywall_bullet" for i in model.value_ledger):
        pytest.skip("has a paywall")
    for grants_id in [None] + [i.id for i in model.value_ledger]:
        for users in ("free", "paying", "everyone"):
            assert check(candidate(model, grants_id=grants_id, for_users=users), model) is None


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
    [out], repairs, _ = finish([c], model, "annotate")
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


def run_with(model, tmp_path, monkeypatch, fail_lenses, delay=None, benefit=None, part_of=None, draft=None):
    """Runs the stage on fake calls: every lens and the top-up return one valid draft (`draft` overrides its
    fields), and the naming call gives every idea `benefit` (a different name each when None) and links it to
    `part_of`. Returns each call's step and prompt text."""
    (tmp_path / "model").mkdir()
    (tmp_path / "propose").mkdir()
    (tmp_path / "model" / "product_model.json").write_text(model.model_dump_json())
    draft = CandidateDraft(**{k: v for k, v in candidate(model, **(draft or {})).model_dump().items()
                              if k in CandidateDraft.model_fields})
    calls = []

    def fake_call(*, step, schema, messages, **_):
        calls.append((step, messages[0]["content"][0]["text"]))
        time.sleep((delay or {}).get(step, 0))
        if step.removeprefix("lens:") in fail_lenses:
            raise llm.LLMFailure("timeout", "provider down")
        if schema is propose.BenefitNames:
            return schema(ideas=[{"id": f"c{n:02d}", "benefit": benefit or f"benefit {n}", "part_of": part_of}
                                 for n in range(1, 20)]), None
        return LensOutput(candidates=[draft]), None

    monkeypatch.setattr(llm, "call", fake_call)
    ctx = Ctx(app={"name": model.app}, run_dir=tmp_path, profile="dev", no_cache=False, replay=False,
              usd_cap=None, allow_fixtures=True)
    propose.run(ctx)
    return calls


def test_every_lens_failing_fails_the_stage(model, tmp_path, monkeypatch):
    every = {lens.id for lens in propose.build_lenses(model)}
    with pytest.raises(RuntimeError, match="every lens call failed"):
        run_with(model, tmp_path, monkeypatch, every)
    assert not (tmp_path / "propose" / "candidates.json").exists()


def test_one_lens_failing_still_finishes(model, tmp_path, monkeypatch):
    run_with(model, tmp_path, monkeypatch, {"free_at_limit"})
    assert (tmp_path / "propose" / "candidates.json").exists()


def test_lenses_run_at_the_same_time_and_keep_their_order(model, tmp_path, monkeypatch):
    lenses = propose.build_lenses(model)
    delay = {f"lens:{l.id}": 0.2 * (len(lenses) - n) for n, l in enumerate(lenses)}
    started = time.monotonic()
    run_with(model, tmp_path, monkeypatch, set(), delay)
    assert time.monotonic() - started < max(delay.values()) + 0.3 < sum(delay.values())
    out = CandidatesFile.model_validate_json((tmp_path / "propose" / "candidates.json").read_text()).candidates
    assert [c.lens for c in sorted(out, key=lambda c: c.id)][:len(lenses)] == [l.id for l in lenses]


def test_the_top_up_fires_once_when_dedupe_leaves_fewer_than_four(model, tmp_path, monkeypatch):
    calls = run_with(model, tmp_path, monkeypatch, set(), benefit="gold badge")
    steps = [step for step, _ in calls]
    assert steps.count("topup") == 1 and steps[-1] == "dedupe:topup"
    assert "The ideas kept so far give: gold badge." in dict(calls)["topup"]
    out = CandidatesFile.model_validate_json((tmp_path / "propose" / "candidates.json").read_text()).candidates
    assert [c.id for c in out if not c.dropped_reason] == ["c01"]
    assert any(c.lens == "topup" and c.dropped_reason == "duplicate of c01: same benefit (gold badge)" for c in out)
    exhibit = next((tmp_path / "exhibits").glob("05-*.md")).read_text()
    assert "Top-up call: fired (1 distinct after dedupe, under 4; 1 after the top-up)." in exhibit
    assert (f"Mock coverage: {len(out)} of {len(out)} ideas start on a screen the mock draws (counted before the "
            "mock-scope filter); the filter dropped 0.") in exhibit


def test_no_top_up_with_four_distinct_ideas(model, tmp_path, monkeypatch):
    lenses = propose.build_lenses(model)
    if len(lenses) < propose.MIN_DISTINCT:
        monkeypatch.setattr(propose, "MIN_DISTINCT", len(lenses))
    steps = [step for step, _ in run_with(model, tmp_path, monkeypatch, set())]
    assert "topup" not in steps and steps.count("dedupe") == 1


def test_a_failed_naming_call_leaves_the_paid_benefit_rule_alone(model, tmp_path, monkeypatch):
    def failing(**_):
        raise llm.LLMFailure("timeout", "provider down")

    monkeypatch.setattr(llm, "call", failing)
    ctx = Ctx(app={"name": model.app}, run_dir=tmp_path, profile="dev", no_cache=False, replay=False,
              usd_cap=None, allow_fixtures=True)
    live = [candidate(model, id="c01")]
    assert propose.name_benefits(ctx, live, llm.Budget("propose", 1.0), "dedupe", model) == ({}, {})
    assert "grants_id alone" in (tmp_path / "trace.jsonl").read_text()


def test_a_paying_idea_the_naming_call_links_to_an_uncounted_bullet_is_dropped(model, tmp_path, monkeypatch):
    m = paywalled(model, "Smarter replies")
    calls = run_with(m, tmp_path, monkeypatch, set(), part_of="b1", draft={"for_users": "paying"})
    assert '- b1: "Smarter replies"' in dict(calls)["dedupe"]
    out = CandidatesFile.model_validate_json((tmp_path / "propose" / "candidates.json").read_text()).candidates
    assert out and all(c.grants_id is None for c in out)
    assert all(c.dropped_reason == 'gives payers more of "Smarter replies", but no amount or cap for it was '
               "observed (linked by the benefit-naming call)" for c in out)
    assert "(linked by the benefit-naming call)" in (tmp_path / "trace.jsonl").read_text()


def test_mock_coverage_counts_before_the_scope_filter_and_lists_what_it_dropped(model):
    outside = next((s for s in model.states if not s.in_mock_scope), None)
    if outside is None:
        pytest.skip("every state is in scope")
    drafts = [candidate(model), candidate(model, title="Off the map", trigger_state_id=outside.id)]
    out, repairs, _ = finish(drafts, model, "annotate")
    text = propose.exhibit([], out, repairs, model, "not needed")
    assert ("Mock coverage: 1 of 2 ideas start on a screen the mock draws (counted before the mock-scope filter); "
            "the filter dropped 1:") in text
    assert f"  - c02 · Off the map · trigger {outside.id} {outside.name}" in text


def with_terms(model):
    """The golden plus one app term whose meaning was never observed and one that was."""
    terms = [Term(term="Zap Credits", meaning="meaning not observed", defined_by=[], used_in=[], observed=False),
             Term(term="Pro", meaning="The paid plan.", defined_by=[], used_in=[], observed=True)]
    return model.model_copy(update={"terms": terms})


@pytest.mark.parametrize("field", ["title", "offer_copy", "after_reward"])
def test_an_idea_using_a_term_whose_meaning_was_never_observed_is_dropped(model, field):
    m = with_terms(model)
    c = candidate(m, **{field: "Play once for 3 zap\xa0credits."})
    assert check(c, m) == 'uses "Zap Credits", whose meaning was never observed'


def test_an_observed_term_can_be_used_and_the_unobserved_one_is_listed_for_the_proposer(model):
    m = with_terms(model)
    assert check(candidate(m, offer_copy="Play once for a day of Pro."), m) is None
    text = propose.model_text(m)
    assert '- "Zap Credits"' in text and '- "Pro"' not in text
    assert "never observed" not in propose.model_text(model.model_copy(update={"terms": []}))


def test_a_short_unobserved_term_matches_only_as_a_whole_word(model):
    pro = Term(term="Pro", meaning="meaning not observed", defined_by=[], used_in=[], observed=False)
    m = model.model_copy(update={"terms": [pro]})
    assert check(candidate(m, title="Try Pro today"), m) == 'uses "Pro", whose meaning was never observed'
    assert check(candidate(m, title="Protect your streak"), m) is None
    assert propose.uses_term("Janitor+", "Said no to janitor+? Play once.")
    assert propose.uses_term("Zap Credits", "3 zap\xa0\xa0credits") and not propose.uses_term("Zap", "Zappy")
