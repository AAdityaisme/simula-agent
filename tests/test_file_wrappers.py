import pytest

from simula.contracts import FILE_WRAPPERS, MODEL_FACING, SCHEMA_VERSION, CandidatesFile, DecisionsFile


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
