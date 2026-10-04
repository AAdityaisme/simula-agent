import pytest

from simula.creative.policy import EPSILON_NEW, N_MIN, cold_set, mix

COUNTS = {"warm-a": 2000, "warm-b": N_MIN}
DRAWS = 20_000


def decision(eligible=True) -> dict:
    """A TH1 decision as rank() returns it: candidates in rank order, the one above the tier ceiling last at 0."""
    rows = [("warm-a", 0.95), ("warm-b", 0.05), ("cold-c", 0.0), ("cold-d", 0.0)]
    return {"selected_candidate_id": "warm-a" if eligible else None,
            "candidates": [{"candidate_id": cid, "eligible": eligible, "selection_probability": p if eligible else 0.0}
                           for cid, p in rows]
            + [{"candidate_id": "over-tier", "eligible": False, "selection_probability": 0.0}]}


def test_the_cold_set_is_the_eligible_creatives_under_n_min():
    assert cold_set(decision(), COUNTS) == {"cold-c", "cold-d"}


def test_logged_propensities_sum_to_1_floor_the_cold_and_match_20000_seeded_draws():
    d = decision()
    cold = cold_set(d, COUNTS)
    p = mix(d, cold, seed=0)["propensities"]
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["over-tier"] == 0.0
    assert p["cold-c"] >= EPSILON_NEW / 2 and p["cold-d"] >= EPSILON_NEW / 2
    assert p["warm-a"] == pytest.approx((1 - EPSILON_NEW) * 0.95)
    picks = [mix(d, cold, seed=s)["chosen"] for s in range(DRAWS)]
    for cid, want in p.items():
        rate = picks.count(cid) / DRAWS
        assert abs(rate - want) <= 3 * (want * (1 - want) / DRAWS) ** 0.5 + 1e-12, (cid, rate, want)
    first = mix(d, cold, seed=0)
    assert first["propensity"] == p[first["chosen"]]


def test_an_ineligible_candidate_gets_0_even_when_named_cold():
    out = mix(decision(), {"over-tier", "cold-c"}, seed=1)
    assert out["propensities"]["over-tier"] == 0.0
    assert out["cold"] == ["cold-c"]
    assert out["propensities"]["cold-c"] == pytest.approx(EPSILON_NEW)


def test_with_no_cold_candidate_the_decision_is_th1s():
    out = mix(decision(), set(), seed=3)
    assert out["epsilon_new"] == 0.0
    assert out["propensities"] == out["th1_probabilities"]


def test_no_fill_chooses_nothing():
    out = mix(decision(eligible=False), {"cold-c"}, seed=4)
    assert (out["chosen"], out["propensity"]) == (None, 0.0)
    assert set(out["propensities"].values()) == {0.0}
