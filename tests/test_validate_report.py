"""validate-judge's report and gate on synthetic cases (never Aadi's fixtures): the 2x2, per-check counts, a
broken check at 0/2, Wilson over the 24 LLM cases, C8 kept out of LLM recall, rerun flips; the fixture loader;
and the blind label CLI."""

import hashlib
import json
import shutil
import subprocess

import pytest

from simula import economics, llm, runfolder, runlog, validate
from simula.contracts import GATES
from simula.stages import judge, propose
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
    assert "| judge_1 | 24/24 (100%) | 0.862 | 12/12 (100%) | 0.757 |" in text
    assert text.index("## Headline: recall") < text.index("## Planted defects caught") < text.index(
        "## Per check: a smoke test") and "can't tell a 50% catch rate from 100%" in text
    assert "| Planted defects (24) | 24 | 0 |" in text and "| Known-good (3) | 3 | 0 |" in text


def test_a_judge_that_fails_everything_catches_24_of_24_and_the_2x2_shows_it():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, fail_all=True))
    assert not passed
    assert "| Planted defects (24) | 24 | 0 |" in text and "| Known-good (3) | 0 | 3 |" in text
    assert "✗ judge_1: known-good ≥ 70% (0%)" in text


def test_a_check_with_0_of_2_caught_is_broken():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, miss={"pd-c5_moment-flagrant", "pd-c5_moment-subtle"}))
    assert not passed
    row = next(line for line in text.splitlines() if line.startswith("| c5_moment |"))
    assert "0/2 **broken** (flagrant ✗, subtle ✗)" in row
    assert "✗ judge_1: no LLM check broken" in text


def test_a_regression_case_is_reported_on_its_own_and_counts_for_nothing_else():
    cases = build_cases()
    m = golden("janitorai")
    regressions = [Case(cid, "regression", idea(m, cid), m, m.app, "", True, target)
                   for cid, target in (("rg-a", None), ("rg-b", None), ("rg-c", "c2_evidence"), ("rg-d", None))]
    verdicts = judged(cases) | {("rg-a", "judge_1"): verdict(), ("rg-b", "judge_1"): verdict(["c1_revealed_value"]),
                                ("rg-c", "judge_1"): verdict(["c1_revealed_value"]), ("rg-d", "judge_1"): None}
    text, passed = run_report(cases + regressions, verdicts)
    assert passed and "| Planted defects (24) | 24 | 0 |" in text and "| Known-good (3) | 3 | 0 |" in text
    assert "| rg-a | any | ✗ passes all 12 |" in text and "| rg-b | any | ✓ fails c1_revealed_value |" in text
    assert "| rg-c | c2_evidence | ✗ fails c1_revealed_value |" in text
    assert "| rg-d | any | ? no verdict: a judge call failed |" in text


def test_simula_validate_judge_and_label_run_the_validation_commands(monkeypatch):
    from simula import cli
    seen = {}
    monkeypatch.setattr(validate, "validate_judge",
                        lambda profile, judges, no_cache, out: seen.update(judge=(profile, judges, no_cache)) or True)
    assert cli.main(["validate-judge", "--profile", "dev", "--judges", "judge_1", "--no-cache"]) == 0
    assert seen["judge"] == ("dev", ["judge_1"], True)
    monkeypatch.setattr(validate, "load_verdicts", lambda out: {})
    monkeypatch.setattr(validate, "label_cases", lambda cases, verdicts, labels_dir, limit: seen.update(limit=limit) or 0)
    assert cli.main(["label", "--limit", "3"]) == 0 and seen["limit"] == 3


def test_simula_validate_judge_at_its_cap_exits_with_the_cap_code(monkeypatch):
    from simula import cli, llm

    def capped(*args):
        raise llm.CapReached("validate: next call could cost $0.20")
    monkeypatch.setattr(validate, "validate_judge", capped)
    assert cli.main(["validate-judge"]) == cli.EXIT_CAP


def test_the_regression_fixtures_load_with_the_product_model_their_judge_saw():
    c03, c07, c09 = [c for c in validate.load_cases() if c.source == "regression"]
    assert [(c.id, c.target) for c in (c03, c07, c09)] == [("rg-janitorai-c03-rev-payers", "c3_spares_payers"),
                                                           ("rg-janitorai-c07-fan-boost", None),
                                                           ("rg-janitorai-c09-empty-evidence", "c2_evidence")]
    assert c07.model.app == "janitorai" and judge.candidate_text(c07.candidate, c07.model) != judge.candidate_text(
        c07.candidate, golden("janitorai"))


def test_one_miss_is_counted_but_not_broken():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, miss={"pd-c7_specific-subtle"}))
    assert passed
    assert "1/2  (flagrant ✓, subtle ✗)" in next(line for line in text.splitlines() if line.startswith("| c7_specific |"))
    assert "| judge_1 | 23/24 (96%)" in text


def test_every_flagrant_gate_defect_must_be_caught():
    cases = build_cases()
    text, passed = run_report(cases, judged(cases, miss={"pd-g_no_cash-flagrant"}))
    assert not passed and "✗ judge_1: every flagrant gate defect caught (4/5)" in text


def test_c8_is_scored_by_the_economics_code_and_kept_out_of_llm_recall():
    cases = build_cases()
    text, _ = run_report(cases, judged(cases))
    assert "24 planted LLM cases (12 subtle), 2 C8 cases" in text
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
    assert "| combined | 24/24 (100%)" in text and "| judge_2 | 23/24 (96%)" in text
    assert "| combined | 0/3 (0%) |" in text


def test_human_labels_give_a_kappa_per_judge():
    cases = build_cases()
    labels = {c.id: {"overall": "fail" if c.source == "planted" else "pass"} for c in cases if c.target != C8}
    text, _ = run_report(cases, judged(cases), labels=labels)
    assert "- judge_1: kappa 1.00 over 27 labels." in text


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


def test_a_planted_case_is_its_base_with_only_the_changed_fields_changed(fixture_dir):
    write(fixture_dir / "planted" / "pd.json",
          planted(change={"reward": {"unit": "gift card"}, "offer_copy": "Play to win a gift card."}))
    cases = {c.id: c for c in validate.load_cases(fixture_dir)}
    base, bad = cases["kg-aol-01"].candidate, cases["pd-g-policy-flagrant"].candidate
    differs = [f for f in type(base).model_fields if getattr(base, f) != getattr(bad, f)]
    assert differs == ["id", "offer_copy", "reward"] and bad.offer_copy == "Play to win a gift card."
    assert bad.reward.unit == "gift card" and bad.reward.kind == base.reward.kind
    assert bad.title.startswith("Product change: ") and cases["pd-g-policy-flagrant"].target == "g_policy"


@pytest.mark.parametrize("bad, message", [
    (planted(change={}), "change must name candidate fields"),
    (planted(change={"decline_path": "a", "not_a_field": 1}), "change must name candidate fields"),
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


def test_a_change_to_a_field_its_base_lacks_is_refused_by_name(fixture_dir):
    good = json.loads((fixture_dir / "known_good" / "kg-aol-01.json").read_text())
    del good["candidate"]["daily_cap"]
    write(fixture_dir / "known_good" / "kg-aol-01.json", good)
    write(fixture_dir / "planted" / "pd.json", planted(change={"daily_cap": 50}))
    with pytest.raises(ValueError, match=r"pd.json: change names \['daily_cap'\], which base 'kg-aol-01' doesn't have"):
        validate.load_cases(fixture_dir)


def test_the_committed_fixtures_meet_the_gates_completeness_and_spread_rules():
    cases = validate.load_cases()
    verdicts = {(c.id, who): verdict([c.target] if c.source == "planted" and c.target in LLM_CHECKS else [])
                for c in cases for who in validate.JUDGES}
    text, _ = validate.report(cases, verdicts, verdicts, {}, validate.JUDGES)
    assert "- ✓ fixtures complete" in text and "- ✓ planted defects span" in text


def test_the_committed_report_rebuilds_from_the_committed_runs(tmp_path):
    rebuilt = tmp_path / "report.md"
    validate.summarize(validate.VERDICTS / "VF3", validate.REPORT.parent / "preface.md", rebuilt)
    def body(path):
        return [line for line in path.read_text().splitlines() if not line.startswith("Generated ")]
    assert body(rebuilt) == body(validate.REPORT)
    assert validate.OUT / "report.md" != validate.REPORT


def test_a_run_saved_before_a_fixture_existed_is_scored_without_it_only_when_asked(tmp_path):
    preface, out = tmp_path / "preface.md", tmp_path / "report.md"
    preface.write_text("")
    validate.summarize(validate.VERDICTS / "VF2", preface, out, only_judged=True)
    text = out.read_text()
    assert "22 planted LLM cases (11 subtle)" in text and "| combined | 22/22 (100%)" in text
    assert "| c3_spares_payers | 0/0" in next(line for line in text.splitlines() if line.startswith("| c3_spares_payers"))


def test_by_default_a_fixture_with_no_verdict_counts_as_a_miss(tmp_path):
    runs, preface, out = tmp_path / "runs", tmp_path / "preface.md", tmp_path / "report.md"
    shutil.copytree(validate.VERDICTS / "VF3", runs)
    for p in runs.glob("kg-run-aol-c01_judge_*_r1.json"):
        p.unlink()
    preface.write_text("")
    validate.summarize(runs, preface, out)
    assert "| Known-good (6) |" in out.read_text()


def test_the_committed_fixtures_load():
    cases = validate.load_cases()
    assert {c.source for c in cases} >= {"run", "planted"}
    assert all(not c.candidate.title.startswith("Product change: Product change") for c in cases)


def test_a_committed_fixture_stores_the_cost_line_the_economics_code_gives():
    """Validation never reads a stored cost line, so only this keeps a fixture from showing a reader figures for an
    offer it no longer makes."""
    own = {d["id"] for d in (json.loads(p.read_text()) for p in validate.CASES.glob("*/*.json")) if "candidate" in d}
    stored = [c for c in validate.load_cases() if c.id in own and c.candidate.economics]
    assert len(stored) >= 10
    for c in stored:
        assert not economics.input_problem(c.candidate), c.id
        assert c.candidate.economics == economics.annotate(c.candidate, c.model), c.id


# ---------- blind labels ----------

def test_label_asks_blind_saves_the_label_and_shows_verdicts_after(tmp_path):
    cases = build_cases()
    verdicts = judged(cases) | judged(cases, "judge_2", fail_known_good=True)
    answers = iter(["f", "c5_moment", "p"])
    said = []
    n = validate.label_cases(cases, verdicts, tmp_path, ask=lambda _: next(answers), say=said.append, limit=2)
    assert n == 2
    labels = [json.loads(p.read_text()) for p in tmp_path.glob("*.json")]
    assert sorted((x["overall"], x["deciding_check"]) for x in labels) == [("fail", "c5_moment"), ("pass", None)]
    shown = "\n".join(said)
    assert shown.index("judge_2: ") > shown.index("title: ")
    assert "pd-" not in said[0] and "Planted" not in said[0]


def test_label_stops_at_the_target_count_and_skips_labeled_cases(tmp_path):
    cases = build_cases()
    (tmp_path / "kg-janitorai.json").write_text("{}")
    n = validate.label_cases(cases, judged(cases), tmp_path, ask=lambda _: "p", say=lambda *_: None, limit=3)
    assert n == 2 and len(list(tmp_path.glob("*.json"))) == 3


def test_the_blind_display_shows_the_judges_product_model_and_nothing_that_gives_the_answer_away(tmp_path):
    cases = [c for c in build_cases() if c.source == "planted"][:1]
    verdicts = {(cases[0].id, "judge_1"): verdict([cases[0].target]),
                (cases[0].id, "judge_2"): verdict([], fixable=True)}
    said, asked = [], []
    validate.label_cases(cases, verdicts, tmp_path, ask=lambda _: asked.append(len(said)) or "q", say=said.append)
    blind = "\n".join(said[:asked[0]])
    assert judge.judge_messages(cases[0].candidate, cases[0].model)[0]["content"][0]["text"] in blind
    assert propose.model_text(cases[0].model) in blind
    for leak in ("judge_1", "judge_2", cases[0].id, cases[0].target, cases[0].tier, "fails here", "Planted"):
        assert leak not in blind


def test_label_mixes_every_known_good_into_its_first_cases_in_an_order_that_never_changes():
    cases = [c for c in validate.load_cases() if c.target != C8]
    goods = {c.id for c in cases if validate.known_good(c)}
    order = [c.id for c in validate.label_order(cases, {}, validate.LABEL_TARGET)]
    assert goods <= set(order[:validate.LABEL_TARGET]) and set(order[:len(goods)]) != goods
    assert order == [c.id for c in validate.label_order(cases, {}, validate.LABEL_TARGET)]
    assert sorted(order) == sorted(c.id for c in cases)


@pytest.mark.parametrize("limit", [1, 3, 6])
def test_a_small_label_limit_shows_the_same_mixed_order(limit):
    cases = [c for c in validate.load_cases() if c.target != C8]
    assert validate.label_order(cases, {}, limit) == validate.label_order(cases, {}, validate.LABEL_TARGET)


def test_the_label_header_shows_nothing_that_sets_the_held_out_cases_apart(tmp_path):
    held_out = next(c for c in validate.load_cases() if c.source == "regression")
    said = []
    validate.label_cases([held_out], {}, tmp_path, ask=lambda _: "q", say=said.append)
    assert said[0].startswith(f"\n=== {held_out.app} ===\n")


def test_the_report_counts_known_goods_by_the_one_definition(monkeypatch):
    cases = build_cases()
    monkeypatch.setattr(validate, "known_good", lambda c: c.id == "kg-aol")
    text, _ = run_report(cases, judged(cases))
    assert "| Known-good (1) | 1 | 0 |" in text


# ---------- an experiment arm ----------

GOOD = "kg-candycrush-01"


def register(root, usd_cap=1.0, treatment=None):
    """Experiment j under root: a control arm on the tracked rubric and a treatment arm on a rubric file there."""
    (root / "experiment-j").mkdir(exist_ok=True)
    rubric = root / "experiment-j" / "treatment.md"
    rubric.write_text(treatment or judge.read_prompt("rubric.md") + "\nA TREATMENT CLAUSE.\n")
    arms = {"control": judge.PROMPTS / "rubric.md", "treatment": rubric}
    lines = [f"usd_cap = {usd_cap}", "labels = 15", 'profile = "dev"', 'judges = ["judge_1", "judge_2"]',
             "no_cache = false"]
    for arm, path in arms.items():
        lines += [f"[arms.{arm}]", f"rubric = {json.dumps(str(path))}",
                  f'sha256 = "{hashlib.sha256(path.read_bytes()).hexdigest()}"']
    (root / "experiment-j" / "registration.toml").write_text("\n".join(lines) + "\n")
    return rubric


def every_good_labeled(n=15):
    cases = [c for c in validate.load_cases() if c.target != C8]
    ordered = sorted(cases, key=lambda c: not validate.known_good(c))
    return {c.id: {"case_id": c.id, "overall": "pass"} for c in ordered[:n]}


def files(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """Experiment j registered under tmp_path with its labels in place and committed; returns the model calls made."""
    register(tmp_path)
    monkeypatch.setattr(validate, "uncommitted", lambda paths: [])
    monkeypatch.setattr(validate, "read_labels", lambda root=validate.CASES: every_good_labeled())
    calls = []

    def call(**kw):
        calls.append(kw)
        return verdict(), None
    monkeypatch.setattr(llm, "call", call)
    return calls


def run_arm(tmp_path, arm="treatment", name="j"):
    validate.experiment(name, arm, tmp_path)


def test_an_experiment_arm_judges_under_its_registered_rubric_and_counts_a_lost_call_against_it(tmp_path, ready,
                                                                                                  monkeypatch):
    rubric = (tmp_path / "experiment-j" / "treatment.md").read_text()
    lost_case = next(c for c in validate.load_cases() if validate.known_good(c))
    seen = {}

    def call(**kw):
        ready.append(kw)
        if kw["step"].startswith(f"{lost_case.id}:judge_2:"):
            raise llm.LLMFailure("error", "Connection error.")
        return verdict(), None
    monkeypatch.setattr(llm, "call", call)
    arm = tmp_path / "experiment-j" / "treatment"
    monkeypatch.setattr(runfolder, "git_dirty", lambda: seen.update(wrote=arm.exists()) or False)
    tracked = judge.read_prompt("rubric.md")
    run_arm(tmp_path)
    llm_cases = [c for c in validate.load_cases() if c.target != C8]
    assert {c["system"] for c in ready} == {rubric} and len(ready) == 2 * len(llm_cases)
    assert judge.read_prompt("rubric.md") == tracked and (arm / "rubric.md").read_text() == rubric
    settings = json.loads((arm / "settings.json").read_text())
    assert settings["cases"] == [c.id for c in llm_cases] and settings["git_dirty"] is False and not seen["wrote"]
    assert {"labels", "cases", str(judge.FROZEN.relative_to(validate.ROOT))} <= settings["inputs"].keys()
    assert len(list((arm / "verdicts").glob("*_r1.json"))) == 2 * len(llm_cases) - 1
    text = (arm / "report.md").read_text()
    goods = sum(validate.known_good(c) for c in llm_cases)
    assert f"Failed calls, each counted as a miss or a fail: 1 ({lost_case.id}:judge_2)" in text
    assert f"| judge_2 | {goods - 1}/{goods} " in text and f"| combined | {goods - 1}/{goods} " in text


def test_arms_of_one_experiment_share_its_registered_cap(tmp_path, ready, monkeypatch):
    (tmp_path / "experiment-j" / "control").mkdir()
    runlog.run_trace(tmp_path / "experiment-j" / "control", stage="validate", step="x", decider="model", usd=0.9,
                     note="")
    seen = {}

    def call(**kw):
        seen["budget"] = (kw["budget"].spent, kw["budget"].cap)
        return verdict(), None
    monkeypatch.setattr(llm, "call", call)
    run_arm(tmp_path)
    assert seen["budget"] == (pytest.approx(0.9), 1.0)


def test_an_arm_runs_once_and_its_first_draw_stays(tmp_path, ready):
    run_arm(tmp_path)
    before = files(tmp_path)
    calls = len(ready)
    with pytest.raises(SystemExit, match="already ran"):
        run_arm(tmp_path)
    assert files(tmp_path) == before and len(ready) == calls


def test_a_re_roll_can_only_happen_visibly_in_git(tmp_path, monkeypatch):
    """The red team's probe_reroll (rt-pr34-dea1f7e), against real git: an arm's draw shows in git status at once, no
    other arm runs until it is committed, and once committed, neither rerunning, deleting its folder, nor deleting it
    in a commit draws it again; there's no --out to draw it elsewhere."""
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout
    register(tmp_path)
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "register experiment j")
    monkeypatch.setattr(validate, "read_labels", lambda root=validate.CASES: every_good_labeled())

    def draw(passes):
        def call(**kw):
            return verdict([] if passes or not kw["step"].startswith(GOOD) else ["c2_evidence"]), None
        monkeypatch.setattr(llm, "call", call)
        run_arm(tmp_path)
    arm = tmp_path / "experiment-j" / "treatment"
    verdict_file = f"experiment-j/treatment/verdicts/{GOOD}_judge_2_r1.json"
    draw(False)
    assert validate.uncommitted([arm]) and validate.uncommitted([arm])[0].startswith("experiment-j/treatment/")
    with pytest.raises(SystemExit, match="already ran"):
        draw(True)
    with pytest.raises(SystemExit, match=r"commit these first: experiment-j/treatment/.* and \d+ more"):
        run_arm(tmp_path, "control")
    git("add", ".")
    git("commit", "-qm", "arm treatment")
    shutil.rmtree(arm)
    with pytest.raises(SystemExit, match="in git history"):
        draw(True)
    git("add", "-A")
    git("commit", "-qm", "drop arm treatment")
    with pytest.raises(SystemExit, match="in git history"):
        draw(True)
    assert not json.loads(git("show", f"HEAD~1:{verdict_file}"))["c2_evidence"]["passed"] and not arm.exists()
    with pytest.raises(SystemExit):
        validate.main(["experiment", "j", "treatment", "--out", str(tmp_path / "elsewhere")])


def test_a_later_arm_runs_only_on_the_inputs_the_first_arm_ran_on(tmp_path, ready, monkeypatch):
    run_arm(tmp_path, "control")
    monkeypatch.setattr(validate, "read_labels", lambda root=validate.CASES: every_good_labeled(16))
    with pytest.raises(SystemExit, match=r"other inputs \(labels\)"):
        run_arm(tmp_path)
    monkeypatch.setattr(validate, "read_labels", lambda root=validate.CASES: every_good_labeled())
    register(tmp_path, usd_cap=50.0)
    with pytest.raises(SystemExit, match="registration.toml\\); an experiment's arms run on the same inputs"):
        run_arm(tmp_path)
    assert not (tmp_path / "experiment-j" / "treatment").exists()
    register(tmp_path)
    monkeypatch.setattr(runfolder, "git_sha", lambda: "0000000")
    run_arm(tmp_path)
    assert json.loads((tmp_path / "experiment-j" / "treatment" / "settings.json").read_text())["git_sha"] == "0000000"


@pytest.mark.parametrize("unmet, refusal", [
    ("labels", "needs 15 labels"), ("good", "1 known-good idea"), ("commit", "commit these first"),
    ("rubric", "not the .* registered for arm treatment")])
def test_the_experiment_refuses_before_any_call_or_write_until_its_prerequisites_hold(tmp_path, ready, monkeypatch,
                                                                                      unmet, refusal):
    if unmet == "labels":
        monkeypatch.setattr(validate, "read_labels", lambda root=validate.CASES: every_good_labeled(14))
    if unmet == "good":
        labels = every_good_labeled(16)
        del labels[next(c.id for c in validate.load_cases() if validate.known_good(c))]
        monkeypatch.setattr(validate, "read_labels", lambda root=validate.CASES: labels)
    if unmet == "commit":
        monkeypatch.setattr(validate, "uncommitted", lambda paths: ["tests/fixtures/judge/labels/kg-x.json"])
    if unmet == "rubric":
        (tmp_path / "experiment-j" / "treatment.md").write_text("edited after registration")
    before = files(tmp_path)
    with pytest.raises(SystemExit, match=refusal):
        run_arm(tmp_path)
    assert not ready and files(tmp_path) == before


@pytest.mark.parametrize("name, arm", [("..", "control"), ("j", "../../prompts/judge"), ("J", "control"),
                                       ("j", "Control"), ("j", ""), ("j", "/tmp/x")])
def test_experiment_and_arm_names_are_plain_slugs(tmp_path, ready, name, arm):
    before = files(tmp_path)
    with pytest.raises(SystemExit, match="plain slugs"):
        run_arm(tmp_path, arm, name)
    assert not ready and files(tmp_path) == before


def test_uncommitted_names_every_new_changed_or_deleted_file_under_a_path(tmp_path):
    repo = tmp_path / "repo"
    labels = repo / "labels"
    labels.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                       capture_output=True)
    git("init", "-q")
    for name in ("a.json", "b.json", "gone.json"):
        (labels / name).write_text("{}")
    (repo / ".gitignore").write_text("*.tmp\n")
    git("add", ".")
    git("commit", "-qm", "x")
    assert validate.uncommitted([labels]) == [] and validate.last_commit(labels)
    (labels / "a.json").write_text('{"changed": true}')
    (labels / "c.json").write_text("{}")
    (labels / "d.tmp").write_text("{}")
    (labels / "gone.json").unlink()
    assert sorted(validate.uncommitted([labels])) == ["labels/a.json", "labels/c.json", "labels/gone.json"]
    (tmp_path / "loose").mkdir()
    assert validate.uncommitted([tmp_path / "loose"]) == [str(tmp_path / "loose")]
    assert validate.last_commit(tmp_path / "loose") == "" and validate.last_commit(repo / "never") == ""


def test_simula_validate_experiment_takes_only_its_name_and_arm(monkeypatch):
    seen = {}
    monkeypatch.setattr(validate, "experiment", lambda *args: seen.update(args=args))
    assert validate.main(["experiment", "j", "control"]) == 0
    assert seen["args"] == ("j", "control")
