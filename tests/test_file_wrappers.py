import pytest

from simula.contracts import (FILE_WRAPPERS, MODEL_FACING, SCHEMA_VERSION, ArtFile, CandidatesFile, CritiqueFile,
                              DecisionsFile, EditsFile, QAReport)


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
REPORT = {  # the shape PR 4's qa stage writes (checked against every qa_report.json in the dev runs)
    "schema_version": 1, "status": "qa_incomplete", "approved_round": 1, "score": 8.12, "stop_reason": "round 2 lost",
    "rounds": [{"round": 0, "score": 7.4, "kept": True, "contract_errors": 1, "failed_taps": 1, "failed_flows": 0,
                "edits_applied": 0, "edits_rejected": 0}],
    "keep_rule_disagreement": {"round": 2, "score_only_approves": 2, "contract_errors": [0, 1], "score": [8.12, 8.3]},
    "undrawn_screens": [{"screen": "s04", "reason": "its batch failed"}],
    "screens": [{"state_id": "s01", "ssim_masked": 0.71, "pixelmatch_ratio": None, "masked_coverage": 0.93,
                 "bounds_ok_share": 0.5, "nav_pass_rate": 1.0, "score": 8.12, "name": "Home", "tagged": 2, "taps": 1,
                 "data_el_misses": [{"id": "s01.e02", "text": "Go", "want": RECT, "got": "missing"},
                                    {"id": "s01.e03", "text": "Pay", "want": RECT, "got": RECT}]}],
    "failed_taps": [{"screen": "s01", "edge": "s01.e03>s02", "problem": "no data-edge tag on its screen"}],
    "flows": [{"flow": "f01", "name": "Upgrade", "status": "failed", "problem": "tap missed", "screen": "s01",
               "navigated": ["s02.back>s01"]}],
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


def test_defaults_keep_older_qa_reports_and_empty_art_files_valid():
    older = {k: v for k, v in REPORT.items() if k not in ("keep_rule_disagreement", "undrawn_screens")}
    older["flows"] = [{"flow": "f01", "name": "Upgrade", "status": "passed", "problem": None}]
    report = QAReport.model_validate(older)
    assert (report.keep_rule_disagreement, report.undrawn_screens, report.flows[0].navigated) == (None, [], [])
    assert ArtFile.model_validate({}).art == {}
