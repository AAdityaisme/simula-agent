import hashlib
import json
import time

import pytest

from simula import llm
from simula.contracts import (CandidateDraft, CandidatesFile, ContractError, ContractReport, LedgerItem, LensOutput,
                              Mechanic, Term)
from simula.stages import Ctx, propose
from simula.stages.propose import anchor_ids, check, depths, finish, in_chat, live_count
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
                                       "A banner inside the chat, with no close button", "A pinned card in your chat",
                                       "Sent as a message from the character", "A system note in the thread",
                                       "Pinned at the top of the message list", "Shown like a chat reply"])
def test_an_offer_inside_the_conversation_is_dropped(model, placement):
    assert "inside the conversation" in check(candidate(model, placement=placement), model)


@pytest.mark.parametrize("placement", ["A banner above the chat list", "A sheet over the paywall",
                                       "A card on the pet screen (not in chat)", "A banner in the chat list header",
                                       "A sheet over the feed. Never shown in a chat.",
                                       "A bottom sheet over the chat screen when the daily message limit is hit",
                                       "A dialog over the chat, never pinned in the message list",
                                       "A sheet that slides up mid-conversation when the free messages run out"])
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
                if s.in_mock_scope and s.id != root(model) and s.content_rating != "unsafe")


def test_the_same_benefit_name_on_different_screens_is_a_duplicate(model):
    badge = {"kind": "cosmetic", "unit": "badge", "amount": 1, "duration": "7 days"}
    drafts = [candidate(model, reward=badge),
              candidate(model, reward={**badge, "unit": "profile flair"}, trigger_state_id=other_screen(model),
                        daily_cap=5)]
    named = {"c01": "Gold  Badges", "c02": "gold-badge"}
    by_id = {c.id: c for c in finish(drafts, model, "annotate", lambda live: (named, {}))[0]}
    assert by_id["c02"].dropped_reason is None
    assert by_id["c01"].dropped_reason == "duplicate of c02: same benefit (gold-badge)"


def test_the_same_paid_benefit_for_the_same_users_is_a_duplicate_whatever_the_name(model):
    meter = LedgerItem(id="x1", kind="meter", verbatim="3 chats left", evidence_ids=[])
    metered = model.model_copy(update={"value_ledger": model.value_ledger + [meter]})
    drafts = [candidate(metered, title="Two more chats", grants_id="x1"),
              candidate(metered, title="Keep talking a little longer", grants_id="x1", daily_cap=3)]
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
    drafts = [candidate(model, for_users="free", daily_cap=3),
              candidate(model, for_users="everyone", daily_cap=2), candidate(model, for_users="paying")]
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
    live = finish([candidate(model, daily_cap=3)], model, "annotate")[0][0]
    assert live.reach_score == 3.0


@pytest.mark.parametrize("frequency_cap, daily_cap", [
    ("1 per user per day, resets at midnight local time; each character can be boosted by fans at most 10 times per "
     "day", 1),
    ("1 per character per user per day, 3 per user per day, resets at midnight local time. Each character can be "
     "lifted by at most 20 fan plays per day.", 3),
    ("2 per user per day; at most 50 plays per character per day, 5 per screen per day", 2)],
    ids=["per-character-limit-in-prose", "per-character-and-per-user", "mixed-per-user-and-per-resource"])
def test_reach_reads_the_typed_per_user_cap_never_the_prose(model, frequency_cap, daily_cap):
    """Astra #2: a regex over the prose read a per-character "10 times per day" as the per-user cap (0.5 x 10)."""
    one_tap_in = next(sid for sid, d in depths(model).items() if d == 1)
    c = candidate(model, trigger_state_id=one_tap_in, frequency_cap=frequency_cap, daily_cap=daily_cap)
    assert propose.rank(c, model, "annotate").rank_score == 0.5 * daily_cap


def test_the_exhibit_shows_the_typed_cap_beside_the_offers_own_cap(model):
    """Code can't tell a per-user limit from a per-character one in prose, so a person sees both side by side."""
    offer_cap = "1 per user per day; each character at most 10 times per day"
    live = finish([candidate(model, frequency_cap=offer_cap, daily_cap=10)], model, "annotate")[0]
    text = propose.exhibit([], live, {}, model, "not needed")
    assert f"per-user daily cap, 10; not a measured audience). The offer's cap: {offer_cap}" in text


def test_no_daily_cap_is_dropped_and_an_older_file_without_one_still_parses(model):
    assert check(candidate(model, daily_cap=0), model) == "doesn't give a per-user daily cap"
    older = candidate(model).model_dump(exclude={"daily_cap"})
    assert CandidateDraft.model_validate({k: v for k, v in older.items() if k in CandidateDraft.model_fields}
                                         ).daily_cap == 0


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


def rated(model, rating):
    states = [s.model_copy(update={"content_rating": rating}) if s.id == root(model) else s for s in model.states]
    return model.model_copy(update={"states": states})


def test_anything_on_or_over_an_unsafe_screen_is_dropped(model):
    unsafe = rated(model, "unsafe")
    for placement in ("A banner under the header", "A bottom sheet over the chat screen when the limit is hit"):
        assert "unsafe content" in check(candidate(unsafe, placement=placement), unsafe)


@pytest.mark.parametrize("rating", ["unknown", "mixed", "safe"])
def test_a_sheet_over_a_chat_screen_not_rated_unsafe_opened_by_a_limit_passes(model, rating):
    chat = rated(model, rating)
    c = candidate(chat, placement="A bottom sheet over the chat screen when the daily message limit is hit",
                  trigger_event="The daily free message counter reaches zero")
    assert check(c, chat) is None


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


def test_a_screen_the_mock_left_undrawn_counts_as_outside_the_mock(model, tmp_path, monkeypatch):
    """Its section is a placeholder ("screen not drawn: $ cap reached: over budget ... raise with --usd-cap"), so it
    must never reach an idea or a slide: the proposer isn't offered it, and an idea on it is dropped as off-mock."""
    home = root(model)
    (tmp_path / "mock").mkdir()
    undrawn = ContractError(kind="undrawn_screen", screen=home,
                            detail="screen not drawn: $ cap reached: over budget: batches 1-1 don't fit the $2.00 "
                                   "mock cap at worst case; raise with --usd-cap")
    (tmp_path / "mock" / "contract_report.json").write_text(
        ContractReport(passed=False, screens=[home], errors=[undrawn]).model_dump_json())
    calls = run_with(model, tmp_path, monkeypatch, set())

    out = CandidatesFile.model_validate_json((tmp_path / "propose" / "candidates.json").read_text()).candidates
    assert out and all(c.dropped_reason == f"{propose.OUTSIDE_MOCK}, which the slides can't draw: {home}" for c in out)
    lens_prompt = next(text for step, text in calls if step.startswith("lens:"))
    in_scope, others = lens_prompt.split("### Other screens (seen, not in scope)")
    assert f"#### {home} " not in in_scope and f"\n- {home} " in others


def test_with_no_mock_report_the_model_is_used_as_written(model, tmp_path):
    assert propose.as_drawn(model, tmp_path) == model


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


PRINTED = ["title", "offer_copy", "after_reward", "adds", "placement", "trigger_event", "frequency_cap", "rationale",
           "subscriber_treatment", "reward.unit", "reward.duration", "caption"]


def with_words(model, field: str, words: str):
    """candidate() with `words` in one field the slides print."""
    base = candidate(model)
    if field.startswith("reward."):
        return candidate(model, reward={**base.reward.model_dump(), field.split(".")[1]: words})
    if field == "caption":
        steps = [s.model_dump() for s in base.flow_steps]
        return candidate(model, flow_steps=steps[:-1] + [{**steps[-1], "caption": words}])
    return candidate(model, **{field: words})


@pytest.mark.parametrize("field", PRINTED)
def test_an_idea_using_a_term_whose_meaning_was_never_observed_is_flagged_and_stays_live(model, field):
    m = with_terms(model)
    c = with_words(m, field, "Play once for 3 zap\xa0credits.")
    assert check(c, m) is None
    [out], *_ = finish([c], m, "annotate")
    assert out.dropped_reason is None and out.flags == ['uses "Zap Credits", whose meaning was never observed']


def janitor_terms(model, free_is_everyday: bool):
    """Two unobserved terms from the real JanitorAI run: the plan name "Free" ("Everything in Free, plus:") and the
    tab "Hidden Gems"."""
    terms = [Term(term="Free", meaning="meaning not observed", defined_by=[], used_in=[], observed=False,
                  everyday=free_is_everyday),
             Term(term="Hidden Gems", meaning="meaning not observed", defined_by=[], used_in=[], observed=False)]
    return model.model_copy(update={"terms": terms})


def c10_rev(model, last_caption: str = "Badge shows"):
    """The real run's c10-rev, a deck idea: "free" only as the everyday adjective, in fields the slides print."""
    steps = [s.model_dump() for s in candidate(model).flow_steps]
    steps[0]["caption"] = "A free user reads the $12.99 a month offer and taps close."
    steps[-1]["caption"] = last_caption
    return candidate(model, trigger_event="A free user taps close on the paywall without subscribing.",
                     rationale="It lets free users try a paid perk on their own profile for a day.", flow_steps=steps)


def test_a_free_user_is_not_flagged_when_free_is_an_everyday_word(model):
    m = janitor_terms(model, free_is_everyday=True)
    [out], *_ = finish([c10_rev(m)], m, "annotate")
    assert out.dropped_reason is None and out.flags == []
    assert '- "Free"' not in propose.model_text(m) and '- "Hidden Gems"' in propose.model_text(m)


def test_without_the_label_free_flags_as_before(model):
    m = janitor_terms(model, free_is_everyday=False)
    [out], *_ = finish([c10_rev(m)], m, "annotate")
    assert out.flags == ['uses "Free", whose meaning was never observed']


def test_an_unobserved_app_name_in_a_caption_is_flagged_beside_an_everyday_word(model):
    m = janitor_terms(model, free_is_everyday=True)
    [out], *_ = finish([c10_rev(m, last_caption="Hidden Gems shows the character first for a day.")], m, "annotate")
    assert out.dropped_reason is None and out.flags == ['uses "Hidden Gems", whose meaning was never observed']


def flagged_and_clean(m, flagged_ranks_higher=True):
    """c01 uses the unobserved term (placed to rank higher, or identical but for its words), c02 is clean."""
    higher = {"trigger_state_id": other_screen(m), "daily_cap": 5} if flagged_ranks_higher else {}
    return [candidate(m, title="Play for 3 Zap Credits", **higher), candidate(m, title="Play for 3 extra replies")]


def test_of_two_twins_the_unflagged_one_is_kept_even_when_the_flagged_one_ranks_higher(model):
    """Red team A #4: the jargon version survived and its clean twin was dropped as "duplicate of c01"."""
    m = with_terms(model)
    drafts = flagged_and_clean(m)
    scores = {c.id: c.rank_score for c in finish(drafts, m, "annotate")[0]}
    assert scores["c01"] > scores["c02"]
    same = lambda live: ({c.id: "extra replies" for c in live}, {})
    by_id = {c.id: c for c in finish(drafts, m, "annotate", same)[0]}
    assert by_id["c02"].dropped_reason is None and by_id["c02"].flags == []
    assert by_id["c01"].dropped_reason == "duplicate of c02: same benefit (extra replies)"


@pytest.mark.parametrize("flagged_ranks_higher", [True, False], ids=["flagged-higher", "equal-score"])
def test_the_cap_keeps_an_unflagged_idea_before_a_flagged_one(model, monkeypatch, flagged_ranks_higher):
    monkeypatch.setattr(propose, "MAX_CANDIDATES", 1)
    m = with_terms(model)
    distinct_names = lambda live: ({"c01": "zap credits", "c02": "extra replies"}, {})
    out = finish(flagged_and_clean(m, flagged_ranks_higher), m, "annotate", distinct_names)[0]
    assert [(c.id, c.dropped_reason) for c in out] == [("c02", None), ("c01", "over the 1-candidate cap")]
    assert live_count(out) == 1


def test_an_observed_term_can_be_used_and_the_unobserved_one_is_listed_for_the_proposer(model):
    m = with_terms(model)
    [out], *_ = finish([candidate(m, offer_copy="Play once for a day of Pro.")], m, "annotate")
    assert out.dropped_reason is None and out.flags == []
    text = propose.model_text(m)
    assert '- "Zap Credits"' in text and '- "Pro"' not in text and "don't use them anywhere in the idea" in text
    assert "code flags an idea that does for a person reviewing the output" in text
    assert "never observed" not in propose.model_text(model.model_copy(update={"terms": []}))


def unobserved(model, *words):
    terms = [Term(term=w, meaning="meaning not observed", defined_by=[], used_in=[], observed=False) for w in words]
    return model.model_copy(update={"terms": terms})


def test_a_short_unobserved_term_flags_only_as_a_whole_word(model):
    m = unobserved(model, "Pro")
    [out], *_ = finish([candidate(m, title="Try Pro today")], m, "annotate")
    assert out.dropped_reason is None and out.flags == ['uses "Pro", whose meaning was never observed']
    [out], *_ = finish([candidate(m, title="Protect your streak")], m, "annotate")
    assert out.dropped_reason is None and out.flags == []
    assert propose.uses_term("Janitor+", "Said no to janitor+? Play once.")
    assert propose.uses_term("Zap Credits", "3 zap\xa0\xa0credits") and not propose.uses_term("Zap", "Zappy")


def test_short_terms_inside_ordinary_words_raise_no_flag(model):
    m = unobserved(model, "AI", "Pro", "Go")
    words = dict(title="Play again for a golden profile frame", offer_copy="Available daily. Good for 7 days.",
                 after_reward="The frame goes away, and your profile looks as before.")
    [out], *_ = finish([candidate(m, **words)], m, "annotate")
    assert out.dropped_reason is None and out.flags == []


def test_two_unobserved_terms_give_two_flags_in_term_order(model):
    m = unobserved(model, "Zap Credits", "Pro")
    [out], *_ = finish([candidate(m, title="Try Pro today", offer_copy="Play once for 3 Zap Credits.")], m, "annotate")
    assert out.dropped_reason is None
    assert out.flags == ['uses "Zap Credits", whose meaning was never observed',
                         'uses "Pro", whose meaning was never observed']


def test_the_report_and_trace_count_flags_apart_from_drops(model, tmp_path, monkeypatch):
    run_with(unobserved(model, "Pro"), tmp_path, monkeypatch, set(), draft={"title": "Try Pro today"})
    out = CandidatesFile.model_validate_json((tmp_path / "propose" / "candidates.json").read_text()).candidates
    live = [c for c in out if not c.dropped_reason]
    assert live and all(c.flags == ['uses "Pro", whose meaning was never observed'] for c in live)
    exhibit = next((tmp_path / "exhibits").glob("05-*.md")).read_text()
    assert (f"{len(live)} live candidates ({len(live)} flagged for an unobserved term), "
            f"{len(out) - len(live)} dropped") in exhibit
    assert "- Flag: uses \"Pro\", whose meaning was never observed" in exhibit
    trace = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    flags = [t for t in trace if t["step"].startswith("flag:")]
    assert len(flags) == len(live) and all(t["outcome"] == "ok" for t in flags)
    assert not any("never observed" in (t.get("note") or "") for t in trace if t["outcome"] == "denied")


def with_experience(model):
    fact = LedgerItem(id="exp1", kind="experience", verbatim="Replies took 2.1 s (median of 5 passes)",
                      evidence_ids=[e.id for e in model.edges[:1]])
    return model.model_copy(update={"value_ledger": model.value_ledger + [fact]})


def test_citing_a_measured_experience_is_context_not_a_missing_id(model):
    m = with_experience(model)
    [out], repairs, _ = finish([candidate(m, anchor_evidence_ids=["exp1"])], m, "annotate")
    assert out.dropped_reason is None and out.anchor_evidence_ids == []
    assert repairs == {"c01": "exp1 -> nothing (a measured experience, not an element)"}
    [anchor], *_ = finish([candidate(m, kind="existing_anchor", adds=None, anchor_evidence_ids=["exp1"])], m, "annotate")
    assert anchor.dropped_reason == "existing_anchor cites no paywall, limit, currency, or entitlement element"


def test_an_unsafe_screen_shows_its_id_name_and_rating_but_no_text(model):
    root_id = root(model)
    states = [s.model_copy(update={"content_rating": "unsafe"}) if s.id == root_id else s for s in model.states]
    text = propose.model_text(model.model_copy(update={"states": states}))
    state = next(s for s in states if s.id == root_id)
    assert f"#### {state.id} {state.name} ({state.kind}; content unsafe)\n- (text left out: unsafe content)" in text
    assert state.elements and not any(f"- {e.id} " in text for e in state.elements)


# sha256 of model_text on each golden, pinned before experience items got their own heading. The goldens carry no
# experience items, so the proposer's prompt must not change; a golden or model_text change repins these.
GOLDEN_MODEL_TEXT = {
    "janitorai": "3a11214543c279d8a8ffcf93256eb645bf1dcf98ad3914526d898d23e5d3b483",
    "luzia": "5e92b0c985386bd8357bf857cd18af17658c49abeb6269889767a200f8d4ce5f",
    "aol": "3aa04504b3a74be0e9256630f737586cf8812a9ba25480cd07ab0f86e29f6e2b",
}


def test_model_text_on_each_golden_is_byte_identical(model):
    assert hashlib.sha256(propose.model_text(model).encode()).hexdigest() == GOLDEN_MODEL_TEXT[model.app]


def test_an_experience_item_is_listed_as_measured_not_as_app_text(model):
    text = propose.model_text(with_experience(model))
    ledger, measured = text.split("### Value ledger (verbatim app text)\n")[1].split(
        "\n\n### Measured experience (what the explorer saw when repeating the core action)\n")
    assert "exp1" not in ledger
    assert measured.startswith("- exp1 Replies took 2.1 s (median of 5 passes) Evidence: ")
    assert '"Replies took' not in text
