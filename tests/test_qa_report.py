"""qa_report.json: fidelity reported apart from the loop's keep score, and what the critic still saw on the approved
version."""

from simula.contracts import QAMetrics, ScreenMetrics
from simula.stages import qa
from tests.test_qa_loop import records, scripted  # noqa: F401 (fixtures; records keeps QA's cache in tmp)


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


def test_structure_interaction_and_visual_are_reported_apart_from_the_keep_score():
    report = qa.qa_report(version(), qa.Loop(rounds=[], stop="s"), {})
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
