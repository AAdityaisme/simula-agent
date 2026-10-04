import csv
import importlib.util
import json

import pytest

from simula import llm
from simula.creative import th1
from simula.creative.assemble import visible_strings
from simula.creative.schema import Content, CreativeAttributes
from simula.runlog import trace
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
CLEAN = {"intro": "Three quick ones. Can you get them all?",
         "captions": ["Find x", "What comes next?", "Unscramble the word"], "right_line": "That's it!",
         "wrong_hint": "Not quite, try again", "end_headline": "Keep learning every day"}
ONE_RUN = {"paths": ("right",), "widths": (390,), "modes": ("fast",)}
DECISIONS = [{"row": 1, "n": 4, "e": 1, "p": [0.95, 0.05, 0.0, 0.0], "u": [0, 0, 1, 1], "c14": [0, 0, 1, 0], "gap": 0.02},
             {"row": 2, "n": 2, "e": 2, "p": [0.975, 0.025], "u": [1, 0], "c14": [1, 0], "gap": 0.0},
             {"row": 3, "n": 2, "e": 1, "p": [1.0, 0.0], "u": [0, 0], "c14": [0, 0], "gap": None}]


def fake_starvation(out_dir, *, requests=10_000, **_):
    """What th1.starvation writes, for three requests, without TH1."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "starvation_decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in DECISIONS))
    summary = {"requests_sampled": 3, "starvation_requests": 2, "starved": 1, "starvation_share": 0.5,
               "starvation_ci": [0.0945, 0.9055], "exploration_set_size": {"mean": 1.333, "histogram": {"1": 2, "2": 1}},
               "pctr_gap_top_unseen_to_leader": {"median": 0.01, "p10": 0.002, "p90": 0.018, "zero_gap_share": 0.5}}
    (out_dir / "starvation.json").write_text(json.dumps(summary))
    return summary


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(th1, "starvation", fake_starvation)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", lambda model, *args, **kwargs: seen.append(model) or llm.Reply(
        text=json.dumps(CLEAN), model=model, tokens_in=100, tokens_out=10))
    return seen


@pytest.fixture
def mvp():
    spec = importlib.util.spec_from_file_location("creative_mvp", ROOT / "tools" / "creative_mvp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(mvp, tmp_path):
    return mvp.run(RUN, tmp_path / "out", seed=7, cache_dir=tmp_path / "cache", qa=ONE_RUN)


def fill_review(mvp, out, first_verdict="edit"):
    rows = mvp.read_review(out)
    for row in rows:
        row["verdict"] = "ok"
    rows[0]["verdict"], rows[0]["edit"] = first_verdict, "Sponsored mini-game"
    with open(out / "review.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=mvp.REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_run_finalize_and_check_recompute_every_number(mvp, calls, tmp_path):
    out = tmp_path / "out"
    report = run(mvp, tmp_path)
    assert (report["variants_total"], report["first_pass_accept"], report["final_accept"]) == (4, 4, None)
    assert report["status"] == "pending_human_read" and len(calls) == 4
    assert report["wrapper_min_propensity"] == pytest.approx(0.025)
    assert report["s04_fixture"]["rejected"] is True
    assert (report["exploration_set_mean"], report["pctr_gap_median"]) == (1.333, 0.01)
    assert mvp.check(out) == []
    contents = [Content.model_validate_json(p.read_text()) for p in sorted((out / "variants").glob("*/content.json"))]
    assert len(mvp.read_review(out)) == sum(len(visible_strings(c)) for c in contents) > 0
    record = CreativeAttributes.model_validate_json((out / "variants" / "teacher-challenge" / "creative.json").read_text())
    assert (record.gate_tier, record.qa.human_verdict) == ("suggestive", "pending")
    assert json.loads((out / "variants" / "toki-help_host" / "adset.json").read_text())["character_names"] == ["Toki"]
    fill_review(mvp, out)
    final = mvp.finalize(out)
    assert (final["status"], final["final_accept"], final["human_edits"]) == ("final", 4, 1)
    assert final["usd_per_accepted"] == pytest.approx(final["usd_total"] / 4, abs=1e-4)
    assert mvp.check(out) == []
    assert "On 4 variants, 4 passed every automatic check on the first draft and 4 after" in (out / "report.md").read_text()


def test_a_rejected_string_fails_its_variant(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out, first_verdict="reject")
    final = mvp.finalize(out)
    assert (final["final_accept"], final["human_edits"]) == (3, 0)
    rejected = mvp.read_review(out)[0]["variant"]
    assert json.loads((out / "variants" / rejected / "creative.json").read_text())["status"] == "failed"


def test_check_catches_a_hand_edited_number(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    md = (out / "report.md").read_text()
    (out / "report.md").write_text(md.replace("| variants_total | 4 |", "| variants_total | 5 |"))
    assert mvp.check(out) == ["report.md is not what report.json renders to"]


def test_a_rerun_counts_what_earlier_runs_spent_against_the_6_dollar_cap(mvp, calls, tmp_path):
    trace(tmp_path / "out" / "trace.jsonl", stage="creative", step="an earlier run", decider="model", usd=5.999)
    with pytest.raises(llm.CapReached):
        run(mvp, tmp_path)
    assert calls == []


def test_a_rerun_refuses_to_wipe_a_review_with_verdicts(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out)
    with pytest.raises(SystemExit, match="holds verdicts"):
        run(mvp, tmp_path)


def test_a_rerun_resumes_from_the_saved_drafts_without_calling_the_model(mvp, calls, tmp_path):
    first = run(mvp, tmp_path)
    (tmp_path / "cache").rename(tmp_path / "old-cache")
    assert run(mvp, tmp_path) == first and len(calls) == 4
    assert mvp.check(tmp_path / "out") == []


def test_a_rerun_with_another_seed_refuses_the_saved_drafts(mvp, calls, tmp_path):
    run(mvp, tmp_path)
    with pytest.raises(SystemExit, match="new drafts dir"):
        mvp.run(RUN, tmp_path / "out", seed=8, cache_dir=tmp_path / "cache", qa=ONE_RUN)
    assert len(calls) == 4
