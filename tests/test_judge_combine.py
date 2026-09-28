"""The judge's decision table, with one judge and with two fake judges; both economics modes; the CONDITIONAL
fallback; no-opportunity; checks_passed/total; and the whole stage with a fake model on every golden."""

import pytest

from simula import llm, runlog
from simula.contracts import GATES, JUDGMENT, CandidatesFile, DecisionsFile, LensOutput
from simula.stages import judge, propose
from tests.conftest import APPS
from tests.judge_helpers import ctx_for, fake_llm, idea, live, seed, verdict
from tests.propose_fixtures import golden

ONE, TWO = 1, 2


def two(fail_a=(), fail_b=(), fixable=False):
    return [verdict(fail_a, fixable), verdict(fail_b, fixable)]


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("gate", GATES)
def test_any_judge_failing_a_gate_rejects(app, gate):
    c = idea(golden(app))
    for verdicts in ([verdict([gate])], two((), [gate]), two([gate], [gate])):
        d = judge.decide(c, verdicts, len(verdicts), "annotate")
        assert (d.final, d.gate_fails, d.failure_type) == ("reject", [gate], "proposal")


@pytest.mark.parametrize("app", APPS)
def test_every_judge_passing_everything_accepts_with_full_score(app):
    c = idea(golden(app), rank=2.5)
    for verdicts in ([verdict()], two()):
        d = judge.decide(c, verdicts, len(verdicts), "annotate")
        assert (d.final, d.checks_passed, d.checks_total, d.rank_score) == ("accept", 11, 11, 2.5)


@pytest.mark.parametrize("check", [k for k in JUDGMENT if k != "c2_evidence"])
def test_every_judge_failing_the_same_judgment_check_rejects(check):
    c = idea(golden("janitorai"))
    for verdicts in ([verdict([check])], two([check], [check])):
        d = judge.decide(c, verdicts, len(verdicts), "annotate")
        assert (d.final, d.failure_type, d.judgment_splits, d.checks_passed) == ("reject", "proposal", [], 10)


def test_unsupported_evidence_rejects_as_a_model_problem_and_names_a_human_gated_rerun():
    d = judge.decide(idea(golden("luzia")), two(["c2_evidence"], ["c2_evidence"]), TWO, "annotate")
    assert (d.final, d.failure_type, d.rerun_stage) == ("reject", "product_model", "explore")


def test_a_split_on_a_judgment_check_goes_to_a_person():
    d = judge.decide(idea(golden("aol")), two(["c5_moment"], []), TWO, "annotate")
    assert (d.final, d.judgment_splits, d.checks_passed) == ("needs_human", ["c5_moment"], 10)


def test_a_unanimous_fail_outranks_a_split():
    d = judge.decide(idea(golden("aol")), two(["c5_moment", "c7_specific"], ["c7_specific"]), TWO, "annotate")
    assert (d.final, d.judgment_splits) == ("reject", ["c5_moment"])


def test_a_judge_call_that_failed_sends_the_candidate_to_a_person():
    d = judge.decide(idea(golden("janitorai")), [verdict()], TWO, "annotate")
    assert (d.final, d.rerun_stage) == ("needs_human", "judge")


def test_a_check_counts_only_when_every_judge_passes_it():
    d = judge.decide(idea(golden("janitorai")), two(["g_policy"], ["c7_specific"]), TWO, "annotate")
    assert (d.checks_passed, d.checks_total) == (9, 11)


def test_a_candidate_propose_dropped_is_rejected_but_keeps_its_scores():
    c = idea(golden("luzia"), dropped_reason="duplicate of c01: same benefit (badge)")
    d = judge.decide(c, [verdict()], ONE, "annotate")
    assert (d.final, d.failure_type, d.checks_passed) == ("reject", "proposal", 11)


def test_no_opportunity_is_rejected_with_zero_checks():
    c = idea(golden("aol"), kind="no_opportunity")
    d = judge.decide(c, [], ONE, "annotate")
    assert (d.final, d.checks_passed, d.checks_total) == ("reject", 0, 11)


@pytest.mark.parametrize("mode, econ, final", [
    ("annotate", "PASS", "accept"), ("annotate", "CONDITIONAL", "conditional"), ("annotate", "FAIL", "conditional"),
    ("gate", "PASS", "accept"), ("gate", "CONDITIONAL", "conditional"), ("gate", "FAIL", "reject")])
def test_the_cost_line_shows_up_in_the_recommendation(mode, econ, final):
    for app in APPS:
        c = idea(golden(app), econ=econ)
        d = judge.decide(c, [verdict()], ONE, mode)
        assert (d.final, d.economics_verdict) == (final, econ)
        if final == "conditional":
            assert "cost" in judge.condition(d, c, [verdict()]).lower()


# ---------- CONDITIONAL fallback ----------

def fallback(cands, verdicts, superseded=()):
    decisions = [judge.decide(c, verdicts[c.id], ONE, "annotate") for c in cands]
    return judge.fallback_pick(decisions, {c.id: c for c in cands}, {k: {"judge_1": v[0]} for k, v in verdicts.items() if v},
                               set(superseded))


def test_fallback_picks_the_gate_passer_with_fewest_fails_then_rank():
    m = golden("janitorai")
    cands = [idea(m, "c01", rank=3), idea(m, "c02", rank=1), idea(m, "c03", rank=2), idea(m, "c04", rank=9)]
    verdicts = {"c01": [verdict(["c5_moment", "c7_specific"])], "c02": [verdict(["c5_moment"])],
                "c03": [verdict(["c7_specific"])], "c04": [verdict(["g_no_cash"])]}
    assert fallback(cands, verdicts) == "c03"


@pytest.mark.parametrize("premise", judge.PREMISE)
def test_fallback_never_rescues_a_false_premise(premise):
    m = golden("luzia")
    cands = [idea(m, "c01", rank=5), idea(m, "c02", rank=1)]
    verdicts = {"c01": [verdict([premise])], "c02": [verdict(["c5_moment", "c7_specific", "c4_protects_subscription"])]}
    assert fallback(cands, verdicts) == "c02"
    assert fallback(cands[:1], verdicts) is None


def test_fallback_skips_dropped_superseded_and_gate_failing_candidates():
    m = golden("aol")
    cands = [idea(m, "c01", dropped_reason="over the cap"), idea(m, "c02"), idea(m, "c03")]
    verdicts = {"c01": [verdict(["c5_moment"])], "c02": [verdict(["c5_moment"])], "c03": [verdict(["g_policy"])]}
    assert fallback(cands, verdicts, superseded={"c02"}) is None
    assert fallback(cands, verdicts) == "c02"


def test_fallback_does_nothing_when_something_survived():
    m = golden("janitorai")
    cands = [idea(m, "c01"), idea(m, "c02")]
    assert fallback(cands, {"c01": [verdict()], "c02": [verdict(["c5_moment"])]}) is None


def test_the_fallback_condition_names_what_failed():
    m = golden("janitorai")
    c = idea(m)
    d = judge.decide(c, [verdict(["c5_moment"])], ONE, "annotate").model_copy(update={"final": "conditional"})
    assert "c5_moment" in judge.condition(d, c, [verdict(["c5_moment"])])


def test_survivors_come_first_by_rank_then_people_then_rejects():
    m = golden("luzia")
    rows = [judge.decide(idea(m, "c01", rank=1), [verdict(["g_policy"])], ONE, "annotate"),
            judge.decide(idea(m, "c02", rank=1), [verdict()], ONE, "annotate"),
            judge.decide(idea(m, "c03", rank=4), [verdict()], ONE, "annotate"),
            judge.decide(idea(m, "c04", rank=9), [verdict()], TWO, "annotate")]
    assert [d.candidate_id for d in judge.ordered(rows)] == ["c03", "c02", "c04", "c01"]


# ---------- the whole stage, with a fake model ----------

@pytest.mark.parametrize("app", APPS)
def test_every_candidate_gets_a_decision_with_scores_and_reasons(app, tmp_path, monkeypatch):
    cands = live(app, {}, {"offer_copy": "GATEFAIL Play a short game for a badge for 7 days."},
                 {"rationale": "WEAK"})
    run_dir = seed(tmp_path, app, cands)
    call, calls = fake_llm({"GATEFAIL": ["g_policy"], "WEAK": ["c7_specific"]})
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    assert {d.candidate_id for d in decisions} >= {c.id for c in cands}
    assert all(d.checks_total == 11 and d.verdict_paths for d in decisions)
    assert all((run_dir / p).exists() for d in decisions for p in d.verdict_paths)
    assert any(d.final == "accept" for d in decisions)
    exhibit = (run_dir / "exhibits" / "06-judge.md").read_text()
    assert all(c.id in exhibit for c in cands) and "g_policy fails here" in exhibit


def test_a_fixable_reject_is_revised_once_and_judged_fresh(tmp_path, monkeypatch):
    app = "janitorai"
    cands = live(app, {"rationale": "WEAK"})
    fixed = cands[0].model_copy(update={"rationale": "Better now.", "title": "Idea 1, better"})
    run_dir = seed(tmp_path, app, cands)
    call, calls = fake_llm({"WEAK": ["c7_specific"]}, revision=fixed)
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = {d.candidate_id: d for d in
                 DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions}
    original, revised = cands[0].id, f"{cands[0].id}-rev"
    assert decisions[original].final == "reject"
    assert (decisions[revised].final, decisions[revised].revision_of) == ("accept", original)
    assert [p.split("/")[-1] for p in decisions[revised].verdict_paths] == [f"{revised}_judge_1_r2.json"]
    revisions = CandidatesFile.model_validate_json((run_dir / "judge" / "revisions.json").read_text()).candidates
    assert [r.id for r in revisions] == [revised] and revisions[0].economics and revisions[0].title.startswith("Product change: ")
    assert sum(c["schema"] is LensOutput for c in calls) == 1


def test_nothing_accepted_falls_back_to_one_conditional(tmp_path, monkeypatch):
    app = "luzia"
    cands = live(app, {"rationale": "WEAK"}, {"rationale": "WEAK MOMENT"})
    run_dir = seed(tmp_path, app, cands)
    call, _ = fake_llm({"WEAK": ["c7_specific"], "MOMENT": ["c5_moment"]})
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    assert [d.final for d in decisions].count("conditional") == 1
    assert [d.failure_type for d in decisions if d.final == "conditional"] == [None]
    assert not (run_dir / "judge" / "no-opportunity.md").exists()
    assert "Condition:" in (run_dir / "exhibits" / "06-judge.md").read_text()


@pytest.mark.parametrize("app", APPS)
def test_no_gate_passer_writes_no_opportunity_and_manufactures_nothing(app, tmp_path, monkeypatch):
    cands = live(app, {}, {"title": "Another"})
    run_dir = seed(tmp_path, app, cands)
    call, _ = fake_llm({"Idea": ["g_brand_safety"], "Another": ["g_brand_safety"]})
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    assert decisions and all(d.final == "reject" for d in decisions)
    assert "none was manufactured" in (run_dir / "judge" / "no-opportunity.md").read_text()


def test_a_failed_judge_call_queues_the_candidate_for_a_person(tmp_path, monkeypatch):
    app = "aol"
    cands = live(app, {})
    run_dir = seed(tmp_path, app, cands)

    def broken(**kw):
        raise llm.LLMFailure("schema_fail", "twice")
    monkeypatch.setattr(llm, "call", broken)
    monkeypatch.setattr(runlog, "notify", lambda *a: False)
    judge.run(ctx_for(app, run_dir))
    decisions = DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions
    assert [(d.final, d.rerun_stage) for d in decisions] == [("needs_human", "judge")]
    assert f"simula judge {app}" in (run_dir / "judge" / "human-queue.md").read_text()
    assert (run_dir / "needs-human.md").exists()


def test_a_rerun_leaves_nothing_from_an_earlier_attempt(tmp_path, monkeypatch):
    app = "luzia"
    run_dir = seed(tmp_path, app, live(app, {}))
    stale = run_dir / "judge"
    (stale / "verdicts").mkdir(parents=True)
    for name in ("human-queue.md", "no-opportunity.md", "verdicts/c09_judge_1_r1.json"):
        (stale / name).write_text("from an earlier attempt")
    call, _ = fake_llm({})
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    assert sorted(p.name for p in stale.rglob("*")) == ["c01_judge_1_r1.json", "decisions.json", "revisions.json",
                                                        "verdicts"]


def test_an_evidence_fail_the_revision_fixed_was_the_proposals_not_the_models(tmp_path, monkeypatch):
    app = "janitorai"
    cands = live(app, {"rationale": "OVERCLAIM"})
    run_dir = seed(tmp_path, app, cands)
    call, _ = fake_llm({"OVERCLAIM": ["c2_evidence"]}, revision=cands[0].model_copy(update={"rationale": "Plain."}))
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = {d.candidate_id: d for d in
                 DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions}
    original = decisions[cands[0].id]
    assert (original.final, original.failure_type, original.rerun_stage) == ("reject", "proposal", None)


def test_an_evidence_fail_the_revision_kept_still_points_at_explore():
    d = judge.decide(idea(golden("aol")), [verdict(["c2_evidence"])], ONE, "annotate")
    assert judge.proposal_fault(d, [verdict(["c2_evidence"])]).rerun_stage == "explore"
    assert judge.proposal_fault(d, []).rerun_stage == "explore"


def decisions_of(run_dir):
    return {d.candidate_id: d for d in
            DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions}


@pytest.mark.parametrize("app", APPS)
def test_a_revision_that_made_the_idea_worse_leaves_its_original_as_the_fallback(app, tmp_path, monkeypatch):
    cands = live(app, {"rationale": "SLOW"})
    worse = cands[0].model_copy(update={"rationale": "CHATTY"})
    run_dir = seed(tmp_path, app, cands)
    call, _ = fake_llm({"SLOW": ["c5_moment"], "CHATTY": ["g_brand_safety"]}, revision=worse)
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = decisions_of(run_dir)
    original = cands[0].id
    assert (decisions[original].final, decisions[f"{original}-rev"].gate_fails) == ("conditional", ["g_brand_safety"])
    assert not (run_dir / "judge" / "no-opportunity.md").exists()


def test_a_revision_that_survives_still_stands_in_for_its_original(tmp_path, monkeypatch):
    app = "luzia"
    cands = live(app, {"rationale": "SLOW"})
    run_dir = seed(tmp_path, app, cands)
    call, _ = fake_llm({"SLOW": ["c5_moment"]}, revision=cands[0].model_copy(update={"rationale": "Better."}))
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = decisions_of(run_dir)
    assert (decisions[cands[0].id].final, decisions[f"{cands[0].id}-rev"].final) == ("reject", "accept")


def test_a_revision_giving_the_same_benefit_as_a_standing_idea_is_dropped_as_its_duplicate(tmp_path, monkeypatch):
    app = "janitorai"
    cands = live(app, {}, {"title": "Second idea", "rationale": "SLOW"})
    good, weak = cands
    run_dir = seed(tmp_path, app, cands)
    call, calls = fake_llm({"SLOW": ["c5_moment"]}, revision=weak.model_copy(update={"rationale": "Better."}),
                           benefits={good.id: ("a badge", None), f"{weak.id}-rev": ("A badge", None)})
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    decisions = decisions_of(run_dir)
    revisions = CandidatesFile.model_validate_json((run_dir / "judge" / "revisions.json").read_text()).candidates
    assert revisions[0].dropped_reason == f"duplicate of {good.id}: same benefit (a badge)"
    assert decisions[f"{weak.id}-rev"].final == "reject" and decisions[good.id].final == "accept"
    assert [c["step"] for c in calls if c["step"] == "judge:revisions"] == ["judge:revisions"]


def test_a_revision_the_naming_call_links_to_an_uncounted_paid_benefit_is_dropped(tmp_path, monkeypatch):
    app = "janitorai"
    model = golden(app)
    bullet = next(i for i in model.value_ledger if i.kind == "paywall_bullet" and not any(ch.isdigit() for ch in i.verbatim)
                  and not propose.observed_limit(model, i.evidence_ids))
    cands = live(app, {"rationale": "SLOW", "for_users": "paying"})
    run_dir = seed(tmp_path, app, cands)
    rev_id = f"{cands[0].id}-rev"
    call, _ = fake_llm({"SLOW": ["c5_moment"]}, revision=cands[0].model_copy(update={"rationale": "Better."}),
                       benefits={rev_id: ("more of it", bullet.id)})
    monkeypatch.setattr(llm, "call", call)
    judge.run(ctx_for(app, run_dir))
    revision = CandidatesFile.model_validate_json((run_dir / "judge" / "revisions.json").read_text()).candidates[0]
    assert "linked by the benefit-naming call" in revision.dropped_reason
    assert decisions_of(run_dir)[rev_id].final == "reject"
