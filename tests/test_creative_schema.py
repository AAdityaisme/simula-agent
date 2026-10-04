import hashlib
import json

import pytest
from pydantic import ValidationError

from simula.creative.schema import PROVENANCE, TRAINING_INPUTS, Brief, CreativeAttributes, creative_id, dump, gate_for
from tests.conftest import ROOT

SCHEMAS = ROOT / "schemas"


def luzia_record() -> dict:
    """A Teacher variant's record as the MVP writes it, with the committed Luzia run's values."""
    return {
        "schema_version": 0,
        "creative_id": "cr_0123456789ab",
        "status": "draft",
        "format": "INT",
        "advertiser": {"app_package": "co.thewordlab.luzia", "app_name": "Luzia", "app_version": "5.44.0",
                       "run_id": "20260929-204554-1f19585", "has_unsafe_screens": True, "campaign_id": None,
                       "category_iab": None},
        "content_tier": "sfw",
        "gate_tier": "suggestive",
        "character": {"kind": "app_persona", "name": "Teacher", "art_ref": "s01.e13.art.png", "evidence_id": "s01.e13"},
        "hook": "challenge",
        "interaction": {"kind": "minigame", "mechanic": "quick_puzzles",
                        "puzzle_families": ["solve_for_x", "next_in_sequence", "unscramble_word"], "puzzle_count": 3,
                        "seed": 20261004},
        "proof": {"screen_id": "s15", "claims": [
            {"text": "I am Teacher, your personal tutor.", "evidence_id": "s15.e02"},
            {"text": "I'm here to resolve your doubts, clarify those difficult concepts", "evidence_id": "s15.e02"}]},
        "copy": {"intro": "Three quick ones. Can you get them all?",
                 "captions": ["Find x", "What comes next?", "Unscramble the word"], "right_line": "That's it!",
                 "wrong_hint": "Not quite, try again", "end_headline": "Keep learning every day"},
        "end_card": {"cta": "Install Now"},
        "qa": {"playthrough_pass": True, "grounding_pass": True, "tier_pass": True, "repair_round": 0,
               "first_pass_accept": True, "review_verdict": "pending", "review_edits": 0, "reviewers": []},
        "lineage": {"brief_id": "br_luzia_v0_grid", "parent_creative_id": None, "generator_version": "creative-v0",
                    "prompt_sha": "a" * 64, "template_sha": "b" * 64},
        "provenance": dict(PROVENANCE),
    }


def json_schema_errors(name: str, data: dict) -> list[str]:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMAS / name).read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return [error.message for error in jsonschema.Draft202012Validator(schema).iter_errors(data)]


def test_the_luzia_record_validates_in_pydantic_and_in_the_json_schema():
    record = luzia_record()
    assert dump(CreativeAttributes.model_validate(record)) == record
    assert json_schema_errors("creative-attributes-v0.json", record) == []


def test_a_reviewed_record_names_its_reviewers_in_both():
    record = luzia_record()
    record["status"] = "accepted"
    record["qa"].update(review_verdict="accepted", reviewers=["fable", "astra"])
    assert CreativeAttributes.model_validate(record).qa.reviewers == ["fable", "astra"]
    assert json_schema_errors("creative-attributes-v0.json", record) == []


@pytest.mark.parametrize("crop, valid", [([224, 508, 614, 1058], True), (None, True), ([224, 508, 614], False)])
def test_a_host_art_crop_is_four_pixel_bounds_in_both(crop, valid):
    record = luzia_record()
    record["character"]["art_crop"] = crop
    if valid:
        assert dump(CreativeAttributes.model_validate(record)) == record
    else:
        with pytest.raises(ValidationError):
            CreativeAttributes.model_validate(record)
    assert (json_schema_errors("creative-attributes-v0.json", record) == []) is valid


@pytest.mark.parametrize("crop, valid", [([0, 0, 411, 136.5], True), (None, True), ([0, 0, 411], False)])
def test_a_proof_crop_is_four_dp_bounds_in_both(crop, valid):
    record = luzia_record()
    record["proof"]["crop"] = crop
    if valid:
        assert dump(CreativeAttributes.model_validate(record)) == record
    else:
        with pytest.raises(ValidationError):
            CreativeAttributes.model_validate(record)
    assert (json_schema_errors("creative-attributes-v0.json", record) == []) is valid


def test_a_generated_persona_host_fails_both():
    record = luzia_record()
    record["character"]["kind"] = "generated_persona"
    with pytest.raises(ValidationError):
        CreativeAttributes.model_validate(record)
    assert json_schema_errors("creative-attributes-v0.json", record) != []


def test_an_sfw_gate_on_an_app_with_unsafe_screens_fails():
    record = luzia_record()
    record["gate_tier"] = "sfw"
    with pytest.raises(ValidationError, match="needs suggestive"):
        CreativeAttributes.model_validate(record)
    record["advertiser"]["has_unsafe_screens"] = False
    assert CreativeAttributes.model_validate(record).gate_tier == "sfw"


def test_gate_for_raises_sfw_only_when_the_app_has_unsafe_screens():
    assert gate_for("sfw", True) == "suggestive"
    assert gate_for("mature", True) == "mature"
    assert gate_for("sfw", False) == "sfw"


@pytest.mark.parametrize("change", ["drop", "add"])
def test_provenance_stamps_every_top_level_field_exactly_once(change):
    record = luzia_record()
    if change == "drop":
        del record["provenance"]["copy"]
    else:
        record["provenance"]["age_days"] = "code"
    with pytest.raises(ValidationError, match="provenance must stamp"):
        CreativeAttributes.model_validate(record)


def test_age_days_appears_only_once_set():
    record = luzia_record()
    assert "age_days" not in dump(CreativeAttributes.model_validate(record))
    record["age_days"] = 3
    record["provenance"]["age_days"] = "code"
    assert dump(CreativeAttributes.model_validate(record))["age_days"] == 3
    assert json_schema_errors("creative-attributes-v0.json", record) == []


def test_an_explicit_null_age_fails_both():
    record = luzia_record()
    record["age_days"] = None
    record["provenance"]["age_days"] = "code"
    with pytest.raises(ValidationError, match="age_days"):
        CreativeAttributes.model_validate(record)
    assert json_schema_errors("creative-attributes-v0.json", record) != []


def test_the_example_brief_validates():
    brief = json.loads((SCHEMAS / "brief.example.json").read_text())
    assert Brief.model_validate(brief).brief_id == "br_example_luzia_0"
    assert json_schema_errors("brief-v0.json", brief) == []


def test_both_json_schemas_list_the_same_training_inputs():
    attributes = json.loads((SCHEMAS / "creative-attributes-v0.json").read_text())
    brief = json.loads((SCHEMAS / "brief-v0.json").read_text())
    assert attributes["x-future-training-inputs"] == list(TRAINING_INPUTS)
    assert brief["$defs"]["values"]["propertyNames"]["enum"] == list(TRAINING_INPUTS)


def test_creative_id_is_the_sha1_of_the_html():
    assert creative_id("<html></html>") == "cr_" + hashlib.sha1(b"<html></html>").hexdigest()[:12]
