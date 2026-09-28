import subprocess
import sys

import pytest

from simula import economics
from simula.config import ROOT
from simula.stages.propose import finish
from tests.conftest import APPS
from tests.propose_fixtures import candidate, golden

NO_COST = {"inference_count": 0, "tokens_in": 0, "tokens_out": 0, "minutes": 0, "currency_amount": 0}


def reward(kind, amount=1):
    return {"kind": kind, "unit": "unit", "amount": amount, "duration": "today"}


def verdict_cases(model):
    return {
        "PASS": candidate(model, reward=reward("cosmetic")),
        "CONDITIONAL": candidate(model, reward=reward("inference", 6),
                                 cost_inputs={**NO_COST, "inference_count": 6, "tokens_in": 4000, "tokens_out": 300}),
        "FAIL": candidate(model, reward=reward("voice", 3), cost_inputs={**NO_COST, "minutes": 3}),
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
    econ = economics.annotate(candidate(golden("janitorai"), reward=reward(kind)), "chat")
    assert econ.cost_2k == econ.cost_8k == 0.0
    assert econ.verdict == "PASS"
    assert econ.assumption_line.startswith("Costs nothing extra to serve")


def test_cost_line_carries_both_contexts_and_its_assumptions():
    c = candidate(golden("janitorai"), reward=reward("inference", 3),
                  cost_inputs={**NO_COST, "inference_count": 3, "tokens_in": 4000, "tokens_out": 300})
    econ = economics.annotate(c, "chat")
    assert econ.cost_8k > econ.cost_2k > 0
    assert f"${econ.breakeven_ecpm_2k:.2f} eCPM at 2k context (${econ.breakeven_ecpm_8k:.2f} at 8k)" in econ.assumption_line
    assert "share 1" in econ.assumption_line and "LATAM" in econ.assumption_line


def test_category_only_picks_the_reading_of_an_ambiguous_kind():
    model = golden("aol")
    unlock = candidate(model, reward=reward("content_unlock"), cost_inputs={**NO_COST, "currency_amount": 0.30})
    assert economics.annotate(unlock, "content").cost_2k > economics.annotate(unlock, "chat").cost_2k
    replies = candidate(model, reward=reward("inference", 2),
                        cost_inputs={**NO_COST, "inference_count": 2, "tokens_in": 4000, "tokens_out": 300})
    assert economics.annotate(replies, "content") == economics.annotate(replies, "chat")


@pytest.mark.parametrize("app", APPS)
def test_annotate_never_drops_or_ranks_down(app):
    model = golden(app)
    cases = verdict_cases(model)
    assert {v: economics.annotate(c, model.app_category).verdict for v, c in cases.items()} == \
        {v: v for v in cases}
    out = finish(list(cases.values()), model, "annotate")
    assert all(c.dropped_reason is None for c in out)
    assert len({c.rank_score for c in out}) == 1


@pytest.mark.parametrize("app", APPS)
def test_gate_drops_only_fail(app):
    model = golden(app)
    out = economics.apply(list(verdict_cases(model).values()), model.app_category, "gate")
    assert [c.economics.verdict for c in out if c.dropped_reason] == ["FAIL"]


@pytest.mark.parametrize("usd, verdict", [(0.45, "PASS"), (0.47, "CONDITIONAL"), (5.0, "FAIL")])
def test_gate_boundaries(usd, verdict):
    # currency at the central 2% purchase chance: break-even 9.0 is under NA's low 9.20, 9.4 is over it
    model = golden("luzia")
    c = candidate(model, reward=reward("currency", 10), cost_inputs={**NO_COST, "currency_amount": usd})
    assert economics.annotate(c, model.app_category).verdict == verdict
    kept = economics.apply([c], model.app_category, "gate")[0]
    assert (kept.dropped_reason is None) == (verdict != "FAIL")
