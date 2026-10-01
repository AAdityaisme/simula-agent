import subprocess
import sys

import pytest

from simula import economics, validate
from simula.config import ROOT
from simula.contracts import Candidate, ProductModel, Term
from simula.stages.propose import finish
from tests.conftest import APPS
from tests.propose_fixtures import candidate, golden

NO_COST = {"inference_count": 0, "tokens_in": 0, "tokens_out": 0, "minutes": 0, "currency_amount": 0}


def reward(kind, amount=1):
    return {"kind": kind, "unit": "replies" if kind == "inference" else kind, "amount": amount, "duration": "today"}


def as_category(model, category):
    return model.model_copy(update={"app_category": category})


def verdict_cases(model):
    return {
        "PASS": candidate(model, title="A badge", reward=reward("cosmetic")),
        "CONDITIONAL": candidate(model, title="Six more replies", reward=reward("inference", 6),
                                 cost_inputs={**NO_COST, "inference_count": 6, "tokens_in": 4000, "tokens_out": 300}),
        "FAIL": candidate(model, title="Three minutes of voice", reward=reward("voice", 3),
                          cost_inputs={**NO_COST, "minutes": 3}),
    }


def test_red_team_case_2k_and_8k():
    red_team = {"count": 5, "tokens_out": 300, "usd_per_mtok_in": 1.0, "usd_per_mtok_out": 5.0}
    cost_2k, cost_8k = economics.costs("inference", red_team)
    assert cost_2k == pytest.approx(0.0175)
    assert cost_8k == pytest.approx(0.0475)
    assert economics.breakeven.break_even_ecpm(cost_2k) == pytest.approx(17.50)
    assert economics.breakeven.break_even_ecpm(cost_8k) == pytest.approx(47.50)


def test_bible_self_check_passes():
    out = subprocess.run([sys.executable, str(ROOT / "bible" / "breakeven.py")], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip().endswith("ok")


@pytest.mark.parametrize("kind", ["cosmetic", "streak_protection", "queue_priority"])
def test_zero_cost_rewards(kind):
    econ = economics.annotate(candidate(golden("janitorai"), reward=reward(kind)), golden("janitorai"))
    assert econ.cost_2k == econ.cost_8k == 0.0
    assert econ.verdict == "PASS"
    assert econ.assumption_line.startswith("Costs nothing extra to serve")


def test_cost_line_carries_both_contexts_and_its_assumptions():
    c = candidate(golden("janitorai"), reward=reward("inference", 3),
                  cost_inputs={**NO_COST, "inference_count": 3, "tokens_in": 4000, "tokens_out": 300})
    econ = economics.annotate(c, golden("janitorai"))
    assert econ.cost_8k > econ.cost_2k > 0
    assert f"${econ.breakeven_ecpm_2k:.2f} eCPM at 2k context (${econ.breakeven_ecpm_8k:.2f} at 8k)" in econ.assumption_line
    assert "share 1" in econ.assumption_line and "LATAM" in econ.assumption_line


def test_category_only_picks_the_reading_of_an_ambiguous_kind():
    model = golden("aol")
    unlock = candidate(model, reward=reward("content_unlock"), cost_inputs={**NO_COST, "currency_amount": 0.30})
    assert economics.annotate(unlock, as_category(model, "content")).cost_2k > \
        economics.annotate(unlock, as_category(model, "chat")).cost_2k
    replies = candidate(model, reward=reward("inference", 2),
                        cost_inputs={**NO_COST, "inference_count": 2, "tokens_in": 4000, "tokens_out": 300})
    assert economics.annotate(replies, as_category(model, "content")) == \
        economics.annotate(replies, as_category(model, "chat"))


@pytest.mark.parametrize("app", APPS)
def test_annotate_never_drops_or_changes_the_rank_score(app):
    model = golden(app)
    cases = verdict_cases(model)
    assert {v: economics.annotate(c, model).verdict for v, c in cases.items()} == \
        {v: v for v in cases}
    out, *_ = finish(list(cases.values()), model, "annotate")
    assert all(c.dropped_reason is None for c in out)
    assert len({c.rank_score for c in out}) == 1


@pytest.mark.parametrize("app", APPS)
def test_gate_drops_only_fail(app):
    model = golden(app)
    out = economics.apply(list(verdict_cases(model).values()), model, "gate")
    assert [c.economics.verdict for c in out if c.dropped_reason] == ["FAIL"]


@pytest.mark.parametrize("usd, verdict", [(0.45, "PASS"), (0.47, "CONDITIONAL"), (5.0, "FAIL")])
def test_gate_boundaries(usd, verdict):
    # currency at the central 2% purchase chance: break-even 9.0 is under NA's low 9.20, 9.4 is over it
    model = golden("luzia")
    c = candidate(model, reward=reward("currency", 10), cost_inputs={**NO_COST, "currency_amount": usd})
    assert economics.annotate(c, model).verdict == verdict
    kept = economics.apply([c], model, "gate")[0]
    assert (kept.dropped_reason is None) == (verdict != "FAIL")


@pytest.mark.parametrize("kind, field", [("image", "inference_count"), ("voice", "minutes"),
                                         ("feature_time", "minutes"), ("inference", "inference_count")])
def test_a_reward_without_its_required_input_is_malformed(kind, field):
    c = candidate(golden("aol"), reward=reward(kind), cost_inputs=NO_COST)
    assert economics.input_problem(c)
    filled = {**NO_COST, field: 3, "tokens_out": 300 if kind == "inference" else 0}
    assert economics.input_problem(candidate(golden("aol"), reward=reward(kind), cost_inputs=filled)) is None


def test_a_zero_cost_kind_carrying_replies_is_malformed():
    c = candidate(golden("janitorai"), cost_inputs={**NO_COST, "inference_count": 3, "tokens_out": 300})
    assert "doesn't match" in economics.input_problem(c)


@pytest.mark.parametrize("kind, category, why", [
    ("currency", "chat", "no price observed"),
    ("content_unlock", "chat", "no price observed"),
    ("feature_time", "chat", "a timed taste of a paid feature"),
    ("feature_time", "learning", "a timed taste of a paid feature"),
])
def test_an_uncounted_cost_says_so_and_is_never_a_pass(kind, category, why):
    c = candidate(golden("janitorai"), reward=reward(kind), cost_inputs={**NO_COST, "minutes": 30})
    econ = economics.annotate(c, as_category(golden("janitorai"), category))
    assert econ.assumption_line.startswith(f"Serving cost not counted: {why}")
    assert "Costs nothing extra" not in econ.assumption_line
    assert econ.verdict == "CONDITIONAL"


def test_the_line_names_the_numbers_that_drive_it():
    model = golden("aol")
    window = economics.annotate(candidate(model, reward=reward("feature_time"), cost_inputs={**NO_COST, "minutes": 30}),
                                as_category(model, "content")).assumption_line
    assert "0.2 ads a minute" in window and "$9.70 eCPM" in window and "banners ($0.55 eCPM)" in window
    coins = economics.annotate(candidate(model, reward=reward("currency", 10),
                                         cost_inputs={**NO_COST, "currency_amount": 0.1}),
                               as_category(model, "game")).assumption_line
    assert "2% chance" in coins
    replies = economics.annotate(candidate(model, reward=reward("inference", 3), cost_inputs={
        **NO_COST, "inference_count": 3, "tokens_out": 300}), as_category(model, "chat")).assumption_line
    assert "3 replies of 300 tokens out, at $0.25 / $2.00 per million tokens" in replies
    assert "8k figure" in replies


def test_the_verdict_is_taken_at_8k_context():
    # 20 replies: favorable prices at 8k break even at $17.92, over NA's high $16.49; at the bible's
    # default 1.5k they would be CONDITIONAL
    c = candidate(golden("janitorai"), reward=reward("inference", 20),
                  cost_inputs={**NO_COST, "inference_count": 20, "tokens_in": 4000, "tokens_out": 300})
    assert economics.annotate(c, golden("janitorai")).verdict == "FAIL"
    assert economics.apply([c], golden("janitorai"), "gate")[0].dropped_reason


def stored(case_id: str) -> tuple[Candidate, ProductModel]:
    case = next(c for c in validate.load_cases() if c.id == case_id)
    return case.candidate, case.model


@pytest.mark.parametrize("case", ["kg-run-janitorai-c02", "kg-run-luzia-c05"])
def test_a_sample_of_a_paid_perk_is_a_neutral_note_not_a_lost_sale(case):
    c, model = stored(case)
    econ = economics.annotate(c, model)
    assert econ.lost_sale is None and "It may give away" not in econ.assumption_line
    assert f"It is a sample of the paid benefit {c.grants_id} " in econ.assumption_line
    assert econ.assumption_line.count("which stays on sale.") == 1


@pytest.mark.parametrize("kind, unit", [("queue_priority", "hours at the top of Explore"), ("currency", "coins")])
def test_priority_and_currency_are_flagged_where_no_paid_plan_was_seen(kind, unit):
    model = golden("aol")
    econ = economics.annotate(candidate(model, reward={**reward(kind), "unit": unit}), model)
    assert econ.lost_sale and "It may give away something the app could sell" in econ.assumption_line
    assert "any completed view pays for it" not in econ.assumption_line


def test_placement_is_read_from_the_reward_kind_never_from_the_units_words():
    """Three wordings of a spot ahead of other users agree: a lost sale when typed as priority, and a note that the
    check can't tell when typed cosmetic, the kind visibility shares."""
    model = golden("luzia")
    priority = "a place ahead of other users, which apps sell as a boost"
    for kind, lost in [("queue_priority", priority), ("cosmetic", None)]:
        marks = [economics.annotate(candidate(model, reward={**reward(kind), "unit": unit}), model)
                 for unit in ("row of stickers", "hours at the top of Explore", "front-page slot")]
        assert {(e.lost_sale, e.assumption_line) for e in marks} == {(lost, marks[0].assumption_line)}
    assert "The check can't tell whether it gives a place ahead of other users" in marks[0].assumption_line


def with_line(model, kind, verbatim):
    line = model.value_ledger[0].model_copy(update={"id": "l99", "kind": kind, "verbatim": verbatim})
    return model.model_copy(update={"value_ledger": [*model.value_ledger, line]})


@pytest.mark.parametrize("kind", ["currency", "price"])
def test_a_reward_from_a_currency_or_price_line_is_flagged_never_a_sample(kind):
    model = with_line(golden("janitorai"), kind, "Gems")
    gems = candidate(model, reward={"kind": "currency", "unit": "gems", "amount": 500, "duration": "permanent"},
                     grants_id="l99")
    badge = candidate(model, reward={**reward("cosmetic"), "duration": "7 days"}, grants_id="l99")
    for c in (gems, badge):
        econ = economics.annotate(c, model)
        assert econ.lost_sale and "sample" not in econ.assumption_line


def test_free_currency_is_flagged_by_its_kind_even_when_it_names_a_paywall_perk():
    model = golden("janitorai")
    paid = next(i.id for i in model.value_ledger if i.kind == "paywall_bullet")
    gems = candidate(model, grants_id=paid, reward={"kind": "currency", "unit": "gems", "amount": 50, "duration": "today"})
    econ = economics.annotate(gems, model)
    assert econ.lost_sale == "in-app currency, which apps sell" and "sample" not in econ.assumption_line


def test_a_paid_perk_given_for_good_or_to_payers_is_a_lost_sale_not_a_sample():
    model = golden("janitorai")
    paid = next(i.id for i in model.value_ledger if i.kind == "paywall_bullet")
    forever = candidate(model, reward={"kind": "queue_priority", "unit": "priority routing", "amount": 1,
                                       "duration": "permanent"}, grants_id=paid)
    more = candidate(model, for_users="paying", grants_id=paid,
                     reward={"kind": "inference", "unit": "replies", "amount": 5, "duration": "today"},
                     cost_inputs={**NO_COST, "inference_count": 5, "tokens_in": 8000, "tokens_out": 300})
    kept = candidate(model, grants_id=paid, reward={"kind": "streak_protection", "unit": "streak shield", "amount": 1,
                                                     "duration": "for good"})
    payers, payers_model = stored("rg-janitorai-c03-rev-payers")
    for c, m in ((forever, model), (more, model), (kept, model), (payers, payers_model)):
        econ = economics.annotate(c, m)
        assert econ.lost_sale and "sample" not in econ.assumption_line, c.title
    assert economics.annotate(more, model).lost_sale.startswith("more of the paid benefit")


def test_a_paid_perk_whose_fields_dont_say_it_ends_gets_a_note_that_says_so():
    model = golden("janitorai")
    paid = next(i for i in model.value_ledger if i.kind == "paywall_bullet")
    c = candidate(model, reward={**reward("cosmetic"), "duration": "while the event runs"}, grants_id=paid.id)
    econ = economics.annotate(c, model)
    assert econ.lost_sale is None and "sample" not in econ.assumption_line
    assert (f'It gives part of the paid benefit {paid.id} "{paid.verbatim}"; the check can\'t tell from its fields '
            "whether that part runs out.") in econ.assumption_line


def test_a_plain_cosmetic_and_a_sale_the_line_prices_carry_no_flag():
    model = golden("luzia")
    assert economics.annotate(candidate(model), model).lost_sale is None
    price = next(i.id for i in model.value_ledger if i.kind == "price")
    coins = candidate(model, reward=reward("currency", 10), grants_id=price,
                      cost_inputs={**NO_COST, "currency_amount": 0.5})
    assert economics.annotate(coins, model).lost_sale is None


@pytest.mark.parametrize("mode", ["annotate", "gate"])
def test_the_lost_sale_flag_never_changes_the_verdict_a_drop_or_the_rank(mode):
    model = golden("janitorai")
    (plain,), *_ = finish([candidate(model, reward=reward("streak_protection"))], model, mode)
    (flagged,), *_ = finish([candidate(model, reward=reward("queue_priority"))], model, mode)
    assert plain.economics.lost_sale is None and flagged.economics.lost_sale
    assert (flagged.economics.verdict, flagged.dropped_reason, flagged.rank_score) == \
        (plain.economics.verdict, plain.dropped_reason, plain.rank_score)


def tasks(model, unit="agent tasks"):
    return candidate(model, reward={**reward("inference", 1), "unit": unit},
                     cost_inputs={**NO_COST, "inference_count": 10, "tokens_in": 4000, "tokens_out": 300})


@pytest.mark.parametrize("unit", ["chats", "chat turns", "swipes", "questions", "Deep Research answers",
                                  "agent task responses", "voice messages"])
def test_a_reply_count_is_priced_whatever_the_unit_is_called_and_none_leaves_it_uncounted(unit):
    model = golden("janitorai")
    assert economics.annotate(tasks(model, unit), model) == economics.annotate(tasks(model, "replies"), model)
    bare = candidate(model, reward={**reward("inference"), "unit": unit}, cost_inputs=NO_COST)
    assert economics.input_problem(bare) is None and economics.uncounted(economics.annotate(bare, model))


@pytest.mark.parametrize("unit", ["replies", "Messages", "chat replies", "messages.", "chat-replies"])
def test_a_unit_that_is_a_reply_word_punctuation_aside_must_carry_its_count(unit):
    bare = candidate(golden("janitorai"), reward={**reward("inference"), "unit": unit}, cost_inputs=NO_COST)
    assert "count" in economics.input_problem(bare)


@pytest.mark.parametrize("count, tokens_out", [(-5, 300), (5, -300), (5, 0), (0, 300)])
def test_a_reply_count_that_is_filled_must_be_positive_whatever_the_unit(count, tokens_out):
    model = golden("janitorai")
    bad = candidate(model, title="Voice", reward={**reward("inference", 5), "unit": "voice messages"},
                    cost_inputs={**NO_COST, "inference_count": count, "tokens_in": 2000, "tokens_out": tokens_out})
    assert economics.input_problem(bad)
    out, *_ = finish([bad, candidate(model, title="A badge")], model, "gate")
    assert [c.title.split(": ")[1] for c in out if not c.dropped_reason] == ["A badge"]


def test_an_inference_reward_with_no_count_and_a_unit_not_known_as_a_reply_is_uncounted():
    model = golden("janitorai")
    econ = economics.annotate(candidate(model, reward={**reward("inference"), "unit": "agent tasks"},
                                        cost_inputs=NO_COST), model)
    assert econ.assumption_line.startswith("Serving cost not counted: one of its agent tasks isn't known to be a "
                                           "chat reply")
    assert (econ.cost_2k, econ.cost_8k, econ.verdict) == (0, 0, "CONDITIONAL") and economics.uncounted(econ)


@pytest.mark.parametrize("meaning", [
    "Regenerates the character's last reply.",
    "An agent that runs multi-step tasks and sends you an answer when done.",
    "An agent that runs multi-step tasks, not a chat reply.",
    "Works for you in the background rather than just giving a quick response."])
def test_what_a_units_term_means_never_makes_it_a_known_chat_reply(meaning):
    model = golden("janitorai")
    agent = Term(term="Computer", meaning=meaning, defined_by=[], used_in=[], everyday=False, observed=True)
    model = model.model_copy(update={"terms": [agent]})
    bare = candidate(model, reward={**reward("inference"), "unit": "Computer tasks"}, cost_inputs=NO_COST)
    assert economics.input_problem(bare) is None
    assert economics.annotate(bare, model).assumption_line.startswith(
        "Serving cost not counted: one of its Computer tasks isn't known to be a chat reply")


def test_gate_mode_ranks_an_uncounted_cost_last_never_as_free():
    model = golden("janitorai")
    # 12 replies break even above the benchmark at 2k (a negative margin) and still aren't a FAIL, so gate keeps them
    dear = candidate(model, title="Twelve more replies", reward=reward("inference", 12),
                     cost_inputs={**NO_COST, "inference_count": 12, "tokens_in": 4000, "tokens_out": 300})
    unknown = candidate(model, title="One agent task", reward={**reward("inference"), "unit": "agent tasks"},
                        cost_inputs=NO_COST)
    free = candidate(model, title="A badge")
    out, *_ = finish([unknown, dear, free], model, "gate")
    live = [c for c in out if not c.dropped_reason]
    assert [c.title.split(": ")[1] for c in live] == ["A badge", "Twelve more replies", "One agent task"]
    assert live[1].rank_score < 0 and live[1].economics.verdict == "CONDITIONAL"


def test_a_reward_not_known_as_a_reply_needs_no_reply_counts():
    model = golden("janitorai")
    none = {**NO_COST}
    assert economics.input_problem(candidate(model, reward={**reward("inference"), "unit": "agent tasks"},
                                             cost_inputs=none)) is None
    assert "count" in economics.input_problem(candidate(model, reward=reward("inference"), cost_inputs=none))
