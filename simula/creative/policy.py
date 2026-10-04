"""Exploration for new creatives on top of TH1's decision, without changing TH1:
P(a) = (1 - eps_new) * P_TH1(a) + eps_new * [a in C] / |C|, where C is the eligible cold set. TH1's probabilities
already sum to 1 over the eligible candidates, so the mixture does too."""

import numpy as np

EPSILON_NEW = 0.05  # an experimental setting, not a tuned value
N_MIN = 500  # [assumed] when a creative leaves the cold set; not a threshold for declaring a winner


def cold_set(decision: dict, counts: dict[str, int], n_min: int = N_MIN) -> set[str]:
    """The eligible candidates of a TH1 decision with fewer than n_min impressions in counts."""
    return {c["candidate_id"] for c in decision["candidates"]
            if c["eligible"] and counts.get(c["candidate_id"], 0) < n_min}


def mix(decision: dict, cold: set[str], *, seed: int, epsilon_new: float = EPSILON_NEW) -> dict:
    """Mixes TH1's selection probabilities with a uniform floor over the eligible cold candidates, then draws one with
    a seeded RNG as TH1 does (rank.py:132). An ineligible candidate gets 0 whatever cold says; with no eligible cold
    candidate epsilon_new is 0. Returns chosen (None when nothing is eligible), its propensity, every candidate's
    propensity, TH1's probabilities, the epsilon_new used and the eligible cold ids."""
    eligible = [c for c in decision["candidates"] if c["eligible"]]
    cold_ids = [c["candidate_id"] for c in eligible if c["candidate_id"] in cold]
    eps = epsilon_new if cold_ids else 0.0
    th1 = {c["candidate_id"]: c["selection_probability"] for c in decision["candidates"]}
    propensities = {cid: 0.0 for cid in th1}
    for c in eligible:
        floor = eps / len(cold_ids) if c["candidate_id"] in cold_ids else 0.0
        propensities[c["candidate_id"]] = (1 - eps) * c["selection_probability"] + floor
    chosen = None
    if eligible:
        weights = np.array([propensities[c["candidate_id"]] for c in eligible])
        pick = np.random.default_rng(seed).choice(len(eligible), p=weights / weights.sum())
        chosen = eligible[int(pick)]["candidate_id"]
    return {"chosen": chosen, "propensity": propensities[chosen] if chosen else 0.0, "propensities": propensities,
            "th1_probabilities": th1, "epsilon_new": eps, "cold": cold_ids}
