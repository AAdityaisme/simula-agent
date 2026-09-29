"""qa_report.json: the stage outcome (complete | partial, reasons, resume), fidelity reported apart from the loop's
keep score, and what the critic still saw on the approved version."""

import pytest

from simula import llm
from simula.contracts import ContractError, QAMetrics, ScreenMetrics
from simula.stages import FRESH_CALLS, mock, qa
from tests.test_mock_isolation import ctx_for
# records keeps QA's replay records in tmp; scripted plays the loop on scripted scores.
from tests.test_qa_loop import OPTIONS, approved_html, records, scripted  # noqa: F401


def screen(sid: str, ssim: float | None, tagged: int, misses: int) -> dict:
    metrics = ScreenMetrics(state_id=sid, ssim_masked=ssim, pixelmatch_ratio=None, masked_coverage=0.9,
                            bounds_ok_share=1.0, nav_pass_rate=1.0, score=9.0)
    return {"metrics": metrics, "name": sid, "tagged": tagged, "taps": 0, "taps_passed": 0,
            "misses": [{"id": f"{sid}.e{i}", "text": "", "want": {}, "got": "missing"} for i in range(misses)]}


def version() -> qa.Version:
    screens = [screen("s01", 0.9, 10, 1), screen("s02", None, 0, 0), screen("s03", 0.5, 4, 0), screen("s04", 0.7, 6, 2)]
    taps = [{"screen": "s01", "edge": "s01.e1>s03", "problem": None},
            {"screen": "s01", "edge": "s01.e2>s04", "problem": "landed on 's01'"}]
    flows = [{"flow": "f1", "name": "a", "status": "passed", "problem": None, "screen": None, "gestures": []},
             {"flow": "f2", "name": "b", "status": "failed", "problem": "x", "screen": "s01", "gestures": []},
             {"flow": "f3", "name": "c", "status": "out_of_scope", "problem": None, "screen": None, "gestures": []}]
    metrics = QAMetrics(round=1, screens=[s["metrics"] for s in screens], cross_screen_failures=[], score=8.765)
    return qa.Version(1, "<html></html>", metrics, screens, taps, flows, [])


def test_structure_interaction_and_visual_are_reported_apart_from_the_keep_score(tmp_path):
    report = qa.qa_report(ctx_for(tmp_path / "run", "anyapp"), version(), qa.Loop(rounds=[], stop="s"), {})
    assert report["keep_score"] == 8.765 and "not a fidelity percentage" in report["keep_score_formula"]
    assert "score" not in report
    assert report["structure"] == {"tagged": 20, "within_4dp": 17}
    assert report["interaction"] == {"taps": 2, "taps_passing": 1, "flows": 3, "flows_walked": 1}
    assert report["visual"] == {"masked_ssim_mean": 0.7, "screens_by_ssim": [{"screen": "s03", "ssim": 0.5},
                                                                          {"screen": "s04", "ssim": 0.7},
                                                                          {"screen": "s01", "ssim": 0.9}]}


def test_the_critics_findings_on_a_version_it_could_not_improve_stay_open(scripted):
    run_dir, _, play = scripted
    report = play([5.0, 6.0, 5.5])
    assert report["approved_round"] == 1
    assert report["open_findings"] == [{"element_id": "s01", "problem": "p", "fix": "f"}]
    assert "What the critic still saw on the approved version" in (run_dir / "exhibits" / "04-qa.md").read_text()


def test_an_approved_version_no_round_critiqued_has_no_open_findings(scripted):
    _, _, play = scripted
    report = play([5.0, 6.0, 6.2])
    assert report["approved_round"] == 2 and report["open_findings"] is None


def test_each_round_reports_its_keep_score(scripted):
    run_dir, _, play = scripted
    report = play([5.0, 6.0, 5.5])
    assert [r["keep_score"] for r in report["rounds"]] == [5.0, 6.0, 5.5]
    text = (run_dir / "exhibits" / "04-qa.md").read_text()
    assert "Keep score 5.00 → 6.00" in text and "| 2 | 5.50 |" in text


def test_a_finished_review_with_every_check_passing_is_complete(scripted):
    _, _, play = scripted
    report = play([5.0, 6.0, 5.5])
    assert (report["status"], report["outcome"], report["reasons"], report["resume"]) == ("approved", "complete", [],
                                                                                          None)


@pytest.mark.parametrize("failure, flag, note", [
    (llm.LLMFailure("refusal", "no"), "--no-cache", [FRESH_CALLS]),
    (llm.CapReached("qa: cap; raise with --usd-cap 3.5"), "--usd-cap 3.50", []),
    (llm.LLMFailure("timeout", "slow"), "", []),
], ids=["answered", "our-cap", "lost-call"])
def test_a_review_a_model_call_cut_short_is_partial_yet_still_approves_its_best_version(scripted, monkeypatch,
                                                                                       failure, flag, note):
    """Red team 9a98e87 F1: the resume follows the cause. A stored answer would replay, so it asks afresh and says so;
    our own cap names a higher one; a lost call isn't replayed, so the plain stage calls again."""
    run_dir, _, play = scripted

    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(qa, "fix", fail)
    report = play([5.0])
    assert (report["status"], report["outcome"], report["approved_round"]) == ("qa_incomplete", "partial", 0)
    assert report["reasons"] == [f"the review stopped early: round 1 stopped before any edit: {failure}", *note]
    assert report["resume"] == f"simula qa luzia --run {run_dir.name} {OPTIONS} {flag}".rstrip()
    assert approved_html(run_dir) == "<html><body>v0</body></html>"
    exhibit = (run_dir / "exhibits" / "04-qa.md").read_text()
    assert "**qa_incomplete** (outcome partial): the review stopped early" in exhibit


def test_contract_errors_left_on_the_approved_version_make_it_partial(scripted):
    _, _, play = scripted
    report = play([5.0, 6.0], errors=[1, 2])
    assert report["approved_round"] == 0
    assert report["reasons"] == ["contract errors on the approved version: 1", FRESH_CALLS]


def test_failing_taps_and_flows_are_named_and_resume_from_qa(tmp_path):
    best = version()
    report = qa.qa_report(ctx_for(tmp_path / "run", "anyapp"), best, qa.Loop(rounds=[], stop="s"), {})
    # The loop's own recorded answers are what fell short, and a plain rerun would replay them.
    assert report["reasons"] == ["taps that still fail: s01.e2>s04", "core flows that still fail: f2", FRESH_CALLS]
    assert report["resume"] == f"simula qa anyapp --run run {OPTIONS} --no-cache"
    best.contract_errors.append(ContractError(kind="wallpaper", detail="d", screen="s01"))
    undrawn = {"s09": "screen not drawn: refusal: no", "s10": "screen not drawn: refusal: no",
               "s11": "screen not drawn: timeout: slow"}
    report = qa.qa_report(ctx_for(tmp_path / "run", "anyapp"), best, qa.Loop(rounds=[], stop="s"), undrawn)
    assert report["reasons"][0] == "the mock left screens undrawn: s09, s10 (refusal: no); s11 (timeout: slow)"
    assert report["reasons"][-2:] == ["contract errors on the approved version: 1", FRESH_CALLS]
    assert report["resume"] == (f"simula mock anyapp --run run {OPTIONS} --no-cache "
                                f"&& simula qa anyapp --run run {OPTIONS}")


@pytest.mark.parametrize("reason, resume", [
    ("timeout: slow", ""),
    ("refusal: no", " --no-cache"),
    ("max_tokens: cut off", " --no-cache"),
    ("the mock builder returned no ```html block", " --no-cache"),
    ("$ cap reached: over budget: batches 3-6 don't fit the $8.00 mock cap at worst case; raise with --usd-cap 18.97",
     " --usd-cap 18.97"),
    ("$ cap reached: over budget: batches 3-6 don't fit the $8.00 mock cap at worst case; raise with --usd-cap",
     " --usd-cap <higher>"),
], ids=["lost-call", "refusal", "max-tokens", "unusable-answer", "cap-with-figure", "cap-without-figure"])
def test_undrawn_screens_resume_the_mock_by_what_left_them_undrawn(tmp_path, reason, resume):
    """The reasons are the ones mock.failure_reason writes: a stored answer needs a fresh call, a lost call a plain
    rerun, and the mock's cap the figure it names. QA then reruns on the new page, without the mock's flags."""
    report = qa.qa_report(ctx_for(tmp_path / "run", "anyapp"), version(), qa.Loop(rounds=[], stop="s"),
                          {"s09": f"screen not drawn: {reason}"})
    assert report["resume"] == f"simula mock anyapp --run run {OPTIONS}{resume} && simula qa anyapp --run run {OPTIONS}"


def test_undrawn_screens_and_our_own_qa_cap_each_resume_with_their_own_flags(tmp_path):
    """Greptile on a79d341: with screens undrawn, the QA after the mock kept none of QA's own flags, so a QA that had
    stopped on its cap stopped there again. Each command carries its own stage's flags."""
    capped = llm.CapReached("qa: cap; raise with --usd-cap 5.5")
    loop = qa.Loop(rounds=[], stop="round 2 stopped before any edit", cause=capped)
    report = qa.qa_report(ctx_for(tmp_path / "run", "anyapp"), version(), loop,
                          {"s09": "screen not drawn: refusal: no"})
    assert report["resume"] == (f"simula mock anyapp --run run {OPTIONS} --no-cache "
                                f"&& simula qa anyapp --run run {OPTIONS} --usd-cap 5.50")


@pytest.mark.parametrize("failure", [llm.CapReached("over budget; raise with --usd-cap 18.97"),
                                     llm.LLMFailure("refusal", "no"), llm.LLMFailure("timeout", "slow"),
                                     ValueError("the mock builder returned no ```html block")])
def test_undrawn_cause_reads_back_what_mock_failure_reason_wrote(failure):
    cause = qa.undrawn_cause(f"screen not drawn: {mock.failure_reason(failure)}")
    assert type(cause) is type(failure) and str(cause) == str(failure)
