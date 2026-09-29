"""validate-judge's report and gate on synthetic cases (never Aadi's fixtures): the 2x2, per-check counts, a
broken check at 0/2, Wilson over the 22 LLM cases, C8 kept out of LLM recall, rerun flips; the fixture loader;
and the blind label CLI."""

import json

import pytest

from simula import validate
from simula.contracts import GATES
from simula.stages import judge
from simula.validate import C8, LLM_CHECKS, Case
from tests.conftest import APPS
from tests.judge_helpers import idea, verdict
from tests.propose_fixtures import golden

TYPES = {"janitorai": "companion chat", "luzia": "AI assistant", "aol": "news"}


def build_cases() -> list[Case]:
    models = [golden(app) for app in APPS]
    cases = [Case(f"kg-{m.app}", "base", idea(m, f"kg-{m.app}"), m, m.app, TYPES[m.app], True) for m in models]
    for n, (check, tier) in enumerate((k, t) for k in LLM_CHECKS for t in ("flagrant", "subtle")):
        m = models[n % 3]
        app, app_type, in_set = ("fitness", "fitness", False) if n % 4 == 3 else (m.app, TYPES[m.app], True)
        cases.append(Case(f"pd-{check}-{tier}", "planted", idea(m, f"pd-{n}"), m, app, app_type, in_set, check, tier))
    m = models[0]
    no_tokens = idea(m, "c8-a", reward={"kind": "inference", "unit": "replies", "amount": 5, "duration": "today"},
                     cost_inputs={"inference_count": 5, "tokens_in": 2000, "tokens_out": 0, "minutes": 0,
                                  "currency_amount": 0})
    cases.append(Case("c8-flagrant", "planted", no_tokens, m, m.app, TYPES[m.app], True, C8, "flagrant", "dropped"))
    cases.append(Case("c8-subtle", "planted", idea(m, "c8-b"), m, m.app, TYPES[m.app], True, C8, "subtle", "PASS"))
    return cases


def judged(cases, who="judge_1", miss=(), fail_known_good=False, fail_all=False):
    """A fake judge's verdicts: it fails each planted case's target unless the case id is in `miss`."""
    out = {}
    for c in cases:
        if c.target == C8:
            continue
        if fail_all:
            out[(c.id, who)] = verdict(LLM_CHECKS)
        elif c.source == "planted":
            out[(c.id, who)] = verdict([] if c.id in miss else [c.target])
        else:
            out[(c.id, who)] = verdict(["c5_moment"] if fail_known_good else [])
    return out


def rerun(verdicts, cases, flip=None):
    out = {}
    for c in cases:
        for (cid, who), v in verdicts.items():
            if cid == c.id and c.target in GATES:
                out[(cid, who)] = verdict([] if cid == flip else [c.target])
    return out


def run_report(cases, verdicts, judges=("judge_1",), flip=None, labels=None):
    return validate.report(cases, verdicts, rerun(verdicts, cases, flip), labels or {}, list(judges))


def test_wilson_lower_bound():
    assert round(validate.wilson_lower(22, 22), 3) == 0.851
    assert round(validate.wilson_lower(11, 11), 3) == 0.741
    assert validate.wilson_lower(0, 0) == 0.0


def test_kappa():
    assert validate.kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert validate.kappa([True, False], [False, True]) == -1.0
    assert validate.kappa([], []) is None


def test_a_judge_that_catches_every_defect_and_passes_known_good_passes_the_gate():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases))
    assert passed and "## Gate: PASS" in text
    assert "| judge_1 | 22/22 (100%) | 0.851 | 11/11 (100%) | 0.741 |" in text
    assert "| Planted defects (22) | 22 | 0 |" in text and "| Known-good (3) | 3 | 0 |" in text


def test_a_judge_that_fails_everything_catches_22_of_22_and_the_2x2_shows_it():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, fail_all=True))
    assert not passed
    assert "| Planted defects (22) | 22 | 0 |" in text and "| Known-good (3) | 0 | 3 |" in text
    assert "✗ judge_1: known-good ≥ 70% (0%)" in text


def test_a_check_with_0_of_2_caught_is_broken():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, miss={"pd-c5_moment-flagrant", "pd-c5_moment-subtle"}))
    assert not passed
    row = next(line for line in text.splitlines() if line.startswith("| c5_moment |"))
    assert "0/2 **broken** (flagrant ✗, subtle ✗)" in row
    assert "✗ judge_1: no LLM check broken" in text


def test_one_miss_is_counted_but_not_broken():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, miss={"pd-c7_specific-subtle"}))
    assert passed
    assert "1/2  (flagrant ✓, subtle ✗)" in next(line for line in text.splitlines() if line.startswith("| c7_specific |"))
    assert "| judge_1 | 21/22 (95%)" in text


def test_every_flagrant_gate_defect_must_be_caught():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, miss={"pd-g_no_cash-flagrant"}))
    assert not passed and "✗ judge_1: every flagrant gate defect caught (4/5)" in text


def test_c8_is_scored_by_the_economics_code_and_kept_out_of_llm_recall():
    cases = build_cases()
    text, _ = run_report(cases, judged(cases))
    assert "22 planted LLM cases (11 subtle), 2 C8 cases" in text
    assert "| c8-flagrant | flagrant | dropped | dropped | ✓ |" in text
    assert "| c8-subtle | subtle | PASS | PASS | ✓ |" in text


def test_a_safety_verdict_that_flips_on_the_rerun_fails_the_gate():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases), flip="pd-g_policy-subtle")
    assert not passed and "safety flips 1 (pd-g_policy-subtle)" in text


def test_a_missing_c8_case_or_a_narrow_app_spread_fails_the_gate():
    cases = [c for c in build_cases() if c.id != "c8-subtle"]
    text, passed = run_report(cases, judged(cases))
    assert not passed and "c8_economics has 1" in text
    cases = [c.__class__(**{**c.__dict__, "in_test_set": True}) for c in build_cases()]
    text, passed = run_report(cases, judged(cases))
    assert not passed and "✗ planted defects span ≥ 3 app types, one app outside the test set" in text


def test_known_good_ideas_cannot_meet_the_app_spread_for_the_planted_defects():
    cases = [c.__class__(**{**c.__dict__, "app": "janitorai", "app_type": "companion chat", "in_test_set": True})
             if c.source == "planted" else c for c in build_cases()]
    m = golden("aol")
    cases += [Case("kg-fitness", "base", idea(m, "kg-fitness"), m, "fitness", "fitness", False)]
    text, passed = run_report(cases, judged(cases))
    assert not passed and "✗ planted defects span ≥ 3 app types, one app outside the test set (1 types, 0 outside)" in text


def test_a_c8_case_the_economics_code_gets_wrong_fails_the_gate():
    cases = [c.__class__(**{**c.__dict__, "expect_economics": "FAIL"}) if c.id == "c8-subtle" else c
             for c in build_cases()]
    text, passed = run_report(cases, judged(cases))
    assert not passed and "✗ c8_economics: the economics code gives the expected result (1/2)" in text


def test_a_gate_case_without_a_second_verdict_is_unverified_not_stable():
    cases = build_cases()
    verdicts = judged(cases)
    reruns = rerun(verdicts, cases)
    reruns[("pd-g_no_cash-subtle", "judge_1")] = None
    text, passed = validate.report(cases, verdicts, reruns, {}, ["judge_1"])
    assert not passed and "9 of 10 gate cases compared" in text and "no second verdict for pd-g_no_cash-subtle" in text


def test_incomplete_fixtures_fail_the_gate_and_say_what_is_missing():
    cases = [c for c in build_cases() if c.target != "c6_fits_simula"]
    text, passed = run_report(cases, judged(cases))
    assert not passed and "**Fixtures incomplete:**" in text and "c6_fits_simula has 0" in text


def test_the_combined_rule_catches_what_either_judge_catches_and_passes_only_what_both_pass():
    cases = build_cases()
    verdicts = judged(cases) | judged(cases, "judge_2", miss={"pd-c7_specific-subtle"}, fail_known_good=True)
    text, passed = run_report(cases, verdicts, judges=("judge_1", "judge_2"))
    assert not passed
    assert "| combined | 22/22 (100%)" in text and "| judge_2 | 21/22 (95%)" in text
    assert "| combined | 0/3 (0%) |" in text


def test_human_labels_give_a_kappa_per_judge():
    cases = build_cases()
    labels = {c.id: {"overall": "fail" if c.source == "planted" else "pass"} for c in cases if c.target != C8}
    text, _ = run_report(cases, judged(cases), labels=labels)
    assert "- judge_1: kappa 1.00 over 25 labels." in text


# ---------- fixture files ----------

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def base_candidate(app="aol"):
    return idea(golden(app)).model_dump(exclude={"id", "lens", "economics", "reach_score", "rank_score",
                                                 "dropped_reason"})


def planted(**changes):
    return {"id": "pd-g-policy-flagrant", "target": "g_policy", "tier": "flagrant", "one_liner": "x", "author": "aadi",
            "expanded_by": "claude-opus-5-5", "base": "kg-aol-01",
            "change": {"decline_path": "Declining hides the article summary."}, **changes}


@pytest.fixture
def fixture_dir(tmp_path):
    write(tmp_path / "known_good" / "kg-aol-01.json",
          {"id": "kg-aol-01", "source": "base", "app": "aol", "app_type": "news", "in_test_set": True,
           "model": "golden/aol/product_model.json", "author": "aadi", "candidate": base_candidate()})
    return tmp_path


def test_a_planted_case_is_its_base_with_exactly_one_field_changed(fixture_dir):
    write(fixture_dir / "planted" / "pd.json", planted(change={"reward": {"unit": "gift card"}}))
    cases = {c.id: c for c in validate.load_cases(fixture_dir)}
    base, bad = cases["kg-aol-01"].candidate, cases["pd-g-policy-flagrant"].candidate
    differs = [f for f in type(base).model_fields if getattr(base, f) != getattr(bad, f)]
    assert differs == ["id", "reward"] and bad.reward.unit == "gift card" and bad.reward.kind == base.reward.kind
    assert bad.title.startswith("Product change: ") and cases["pd-g-policy-flagrant"].target == "g_policy"


@pytest.mark.parametrize("bad, message", [
    (planted(change={"decline_path": "a", "offer_copy": "b"}), "exactly one candidate field"),
    (planted(change={"not_a_field": 1}), "exactly one candidate field"),
    (planted(base="kg-missing"), "is not a known_good id"),
    (planted(target=C8), "expect_economics"),
    (planted(expect_economics="FAIL"), "expect_economics")])
def test_the_loader_refuses_a_malformed_planted_case(fixture_dir, bad, message):
    write(fixture_dir / "planted" / "pd.json", bad)
    with pytest.raises(ValueError, match=message):
        validate.load_cases(fixture_dir)


def test_an_out_of_set_label_on_a_golden_model_is_refused(fixture_dir):
    good = json.loads((fixture_dir / "known_good" / "kg-aol-01.json").read_text())
    write(fixture_dir / "known_good" / "kg-aol-01.json", {**good, "app": "fitness", "in_test_set": False})
    with pytest.raises(ValueError, match="a test-set app uses a golden model"):
        validate.load_cases(fixture_dir)


def test_a_sketch_model_for_an_app_outside_the_test_set_loads_and_judges(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "FIXTURES", tmp_path)
    write(tmp_path / "judge" / "known_good" / "models" / "fitness.json", {
        "app": "fitness", "app_category": "utility",
        "states": [{"id": "s01", "name": "Today", "purpose": "Daily plan.",
                    "elements": [{"id": "s01.e01", "text": "2 of 3 free workouts left this week", "role": "meter"}]}],
        "mechanics": [{"id": "m01", "kind": "limit", "evidence_ids": ["s01.e01"], "summary": "3 free a week.",
                       "observed_numbers": ["3"], "status": "observed"}],
        "open_questions": ["Price not seen."]})
    model = validate.load_model("judge/known_good/models/fitness.json")
    c = idea(model, anchor_evidence_ids=["s01.e01"])
    text = judge.judge_messages(c, model)[0]["content"][0]["text"]
    assert '"2 of 3 free workouts left this week"' in text and "Price not seen." in text
    assert model.states[0].in_mock_scope and model.provenance.source == "fixture"


def test_a_real_candidate_can_be_a_planted_case(fixture_dir):
    real = {**idea(golden("aol"), "c07").model_dump(), "title": "Product change: A second city"}
    write(fixture_dir / "planted" / "pd.json", planted(base=None, change=None, target="c1_revealed_value",
                                                       tier="subtle", from_run="round 6 c07", app="aol",
                                                       app_type="news", model="golden/aol/product_model.json",
                                                       candidate=real))
    case = next(c for c in validate.load_cases(fixture_dir) if c.source == "planted")
    assert (case.target, case.candidate.id, case.candidate.title) == ("c1_revealed_value", "pd-g-policy-flagrant",
                                                                      "Product change: A second city")
    write(fixture_dir / "planted" / "pd.json", planted(candidate=real, from_run="x", app="aol", app_type="news",
                                                       model="golden/aol/product_model.json"))
    with pytest.raises(ValueError, match="no base or change"):
        validate.load_cases(fixture_dir)
    write(fixture_dir / "planted" / "pd.json", planted(base=None, change=None, target=C8, tier="subtle",
                                                       expect_economics="PASS", from_run="round 6 c07", app="aol",
                                                       app_type="news", model="golden/aol/product_model.json",
                                                       candidate=real))
    case = next(c for c in validate.load_cases(fixture_dir) if c.source == "planted")
    assert case.expect_economics == "PASS"


def test_real_run_ideas_count_toward_the_known_good_bar():
    cases = build_cases()
    m = golden("luzia")
    cases += [Case("kg-run", "run", idea(m, "kg-run"), m, "luzia", "AI assistant", True)]
    verdicts = judged(cases) | {("kg-run", "judge_1"): verdict(["c5_moment"])}
    text, passed = run_report(cases, verdicts)
    assert "| Known-good (4) | 3 | 1 |" in text and "| judge_1 | 3/4 (75%) |" in text and passed


def test_the_committed_fixtures_load():
    cases = validate.load_cases()
    assert {c.source for c in cases} >= {"run", "planted"}
    assert all(not c.candidate.title.startswith("Product change: Product change") for c in cases)


# ---------- blind labels ----------

def test_label_asks_blind_saves_the_label_and_shows_verdicts_after(tmp_path):
    cases = build_cases()
    verdicts = judged(cases) | judged(cases, "judge_2", fail_known_good=True)
    answers = iter(["f", "c5_moment", "p"])
    said = []
    n = validate.label_cases(cases, verdicts, tmp_path, ask=lambda _: next(answers), say=said.append, limit=2)
    assert n == 2
    first = json.loads((tmp_path / "kg-janitorai.json").read_text())
    assert (first["overall"], first["deciding_check"]) == ("fail", "c5_moment")
    shown = "\n".join(said)
    assert shown.index("judge_2: fails c5_moment") > shown.index("title: ")
    assert "pd-" not in said[0] and "Planted" not in said[0]


def test_label_stops_at_the_target_count_and_skips_labeled_cases(tmp_path):
    cases = build_cases()
    (tmp_path / "kg-janitorai.json").write_text("{}")
    n = validate.label_cases(cases, judged(cases), tmp_path, ask=lambda _: "p", say=lambda *_: None, limit=3)
    assert n == 2 and len(list(tmp_path.glob("*.json"))) == 3
