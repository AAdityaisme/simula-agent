"""Dedupe on the 2026-09-28 experiment's hand-labeled pairs: the benefit names Sonnet 5 (effort low) gave in two
samples, fed through a fake llm.call, then code's pairing rule. The model half is checked by the merge gate."""

import json
import re

import pytest

from simula import llm
from simula.stages import Ctx, propose
from tests.conftest import FIXTURES
from tests.propose_fixtures import candidate, golden

RUNS = json.loads((FIXTURES / "dedupe" / "pairs.json").read_text())["runs"]


def named(run, sample, tmp_path, monkeypatch):
    model = golden(run["app"])
    live = [candidate(model, **c) for c in run["candidates"]]

    def fake_call(*, messages, schema, **_):
        prompt = messages[0]["content"][0]["text"]
        assert all(f"{c.id} | for: {c.for_users}" in prompt for c in live)
        return schema(ideas=[{"id": i, "benefit": b} for i, b in run["names"][sample].items()]), None

    monkeypatch.setattr(llm, "call", fake_call)
    ctx = Ctx(app={"name": model.app}, run_dir=tmp_path, profile="dev", no_cache=False, replay=False,
              usd_cap=None, allow_fixtures=True)
    return live, propose.name_benefits(ctx, live, llm.Budget("propose", 1.0), "dedupe")


@pytest.mark.parametrize("sample", ["1", "2"])
def test_no_false_merge_and_the_clear_same_pairs_found(sample, tmp_path, monkeypatch):
    scored = []
    for run in RUNS:
        live, names = named(run, sample, tmp_path, monkeypatch)
        by_id = {c.id: c for c in live}
        assert set(names) == set(by_id)
        labels = {frozenset((p["a"], p["b"])): p for p in run["pairs"]}
        for p in run["pairs"]:
            merged = propose.same_benefit(by_id[p["a"]], by_id[p["b"]], names) is not None
            scored.append((p["label"] == "SAME", p["judgment_call"], merged))
        for c in propose.dedupe(sorted(live, key=lambda c: -c.rank_score), names):
            if c.dropped_reason:
                twin = re.match(r"duplicate of (c\d+): same benefit", c.dropped_reason).group(1)
                assert labels.get(frozenset((c.id, twin)), {"label": "UNSURE"})["label"] != "DIFFERENT"
    merged = [same for same, _, m in scored if m]
    clear = [m for same, judgment, m in scored if same and not judgment]
    print(f"sample {sample}: precision {sum(merged) / len(merged):.2f}, recall on clear pairs "
          f"{sum(clear) / len(clear):.2f}, recall on every SAME pair "
          f"{sum(m for same, _, m in scored if same) / sum(same for same, _, _ in scored):.2f}")
    assert all(merged)
    assert sum(clear) / len(clear) >= 0.9
