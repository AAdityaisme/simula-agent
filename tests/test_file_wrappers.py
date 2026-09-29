from typing import get_args

import pytest
from pydantic import ValidationError

from simula.contracts import (FILE_WRAPPERS, MODEL_FACING, SCHEMA_VERSION, ArtFile, CandidatesFile, CritiqueFile,
                              DecisionsFile, EditsFile, QAReport, StageOutcome)


@pytest.mark.parametrize("wrapper", FILE_WRAPPERS, ids=lambda w: w.__name__)
def test_every_file_wrapper_carries_schema_version(wrapper):
    field = wrapper.model_fields["schema_version"]
    assert field.default == SCHEMA_VERSION


def test_wrappers_are_code_written_not_model_facing():
    assert not set(FILE_WRAPPERS) & set(MODEL_FACING)


def test_empty_files_round_trip():
    for wrapper, empty in ((CandidatesFile, {"candidates": []}), (DecisionsFile, {"decisions": []})):
        parsed = wrapper.model_validate_json(wrapper.model_validate(empty).model_dump_json())
        assert parsed.schema_version == SCHEMA_VERSION


RECT = {"x": 16.0, "y": 664.0, "w": 121.0, "h": 66.0}
REPORT = {  # the shape PR 4's qa stage writes (test_qa_loop's partial-QA test validates a real run's report too)
    "schema_version": 1, "status": "qa_incomplete", "outcome": "partial",
    "reasons": ["the mock left screens undrawn: s04 (the model declined)", "taps that still fail: s01.e03>s02"],
    "resume": ("simula mock anyapp --run r1 --profile dev --budget transfer --usd-cap 25.00 --no-cache "
               "&& simula qa anyapp --run r1 --profile dev --budget transfer"),
    "resume_note": "What stopped it: mock: s04 (refusal: no).", "approved_round": 1, "keep_score": 8.12,
    "keep_score_formula": "10 × (0.5 bounds + 0.3 nav + 0.2 ssim); it is not a fidelity percentage.",
    "structure": {"tagged": 2, "within_4dp": 1},
    "interaction": {"taps": 1, "taps_passing": 0, "flows": 1, "flows_walked": 0},
    "visual": {"masked_ssim_mean": 0.71, "screens_by_ssim": [{"screen": "s01", "ssim": 0.71}]},
    "open_findings": [{"element_id": "s01.e02", "problem": "p", "fix": "f"}], "stop_reason": "round 2 lost",
    "rounds": [{"round": 0, "keep_score": 7.4, "kept": True, "contract_errors": 1, "failed_taps": 1, "failed_flows": 0,
                "edits_applied": 0, "edits_rejected": 0}],
    "keep_rule_disagreement": {"round": 2, "score_only_approves": 2, "contract_errors": [0, 1], "score": [8.12, 8.3]},
    "undrawn_screens": [{"screen": "s04", "reason": "its batch failed"}],
    "screens": [{"state_id": "s01", "ssim_masked": 0.71, "pixelmatch_ratio": None, "masked_coverage": 0.93,
                 "bounds_ok_share": 0.5, "nav_pass_rate": 1.0, "score": 8.12, "name": "Home", "tagged": 2, "taps": 1,
                 "data_el_misses": [{"id": "s01.e02", "text": "Go", "want": RECT, "got": "missing"},
                                    {"id": "s01.e03", "text": "Pay", "want": RECT, "got": RECT}]}],
    "failed_taps": [{"screen": "s01", "edge": "s01.e03>s02", "problem": "no data-edge tag on its screen"}],
    "flows": [{"flow": "f01", "name": "Upgrade", "status": "failed", "problem": "tap missed", "screen": "s01",
               "gestures": ["s02.back>s01"]}],
    "contract_errors": [{"kind": "undrawn_screen", "detail": "batch 2 failed", "screen": "s04"}],
}


def test_the_qa_and_art_wrappers_read_what_the_qa_and_mock_stages_write():
    assert QAReport.model_validate(REPORT).model_dump(mode="json") == REPORT
    edits = {"schema_version": 1, "edits": [{"find": "a", "replace": "b", "reason": "r", "applied": False,
                                             "why": "find matches the page 2 times, not once"}]}
    critique = {"schema_version": 1, "fixes": [{"element_id": "s01.e02", "problem": "p", "fix": "f"}], "summary": "s"}
    art = {"schema_version": 1, "art": {"assets/s02.e08.art.png": RECT}}
    for wrapper, data in ((EditsFile, edits), (CritiqueFile, critique), (ArtFile, art)):
        assert wrapper.model_validate(data).model_dump(mode="json") == data, wrapper.__name__


def test_a_complete_report_with_nothing_open_and_nothing_scored_reads():
    complete = {**REPORT, "status": "approved", "outcome": "complete", "reasons": [], "resume": None, "resume_note": None,
                "open_findings": None, "visual": {"masked_ssim_mean": None, "screens_by_ssim": []}}
    assert QAReport.model_validate(complete).model_dump(mode="json") == complete


def test_qas_outcome_is_the_done_marker_vocabulary_so_wiring_it_in_is_one_line():
    assert get_args(QAReport.model_fields["outcome"].annotation) == get_args(
        StageOutcome.model_fields["status"].annotation)
    report = QAReport.model_validate(REPORT)
    assert StageOutcome(status=report.outcome, reasons=report.reasons, resume=report.resume).status == "partial"


def without(data: dict, *keys: str) -> dict:
    return {k: v for k, v in data.items() if k not in keys}


FLOW = REPORT["flows"][0]


@pytest.mark.parametrize("report", [
    without(REPORT, "outcome", "reasons", "resume"),
    without(REPORT, "resume_note"),
    without(REPORT, "open_findings"),
    {**REPORT, "flows": [without(FLOW, "gestures")]},
    {**without(REPORT, "keep_score"), "score": 8.12},
    {**REPORT, "flows": [{**without(FLOW, "gestures"), "navigated": ["s02.back>s01"]}]},
], ids=["no outcome", "no resume_note", "no open_findings", "a flow with no gestures", "score before keep_score",
        "navigated before gestures"])
def test_a_report_missing_what_qa_always_writes_is_refused_not_read_with_a_default(report):
    with pytest.raises(ValidationError):
        QAReport.model_validate(report)


def test_an_empty_art_file_is_valid():
    assert ArtFile.model_validate({}).art == {}
