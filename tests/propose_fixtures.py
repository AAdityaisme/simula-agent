"""App-neutral builders for propose tests: a golden model and a candidate that passes every code check."""

from simula.contracts import Candidate, ProductModel
from simula.stages.propose import anchor_ids
from tests.conftest import FIXTURES


def golden(app: str) -> ProductModel:
    return ProductModel.model_validate_json((FIXTURES / "golden" / app / "product_model.json").read_text())


def root(model: ProductModel) -> str:
    return min(s.id for s in model.states if s.kind == "screen")


def candidate(model: ProductModel, **changes) -> Candidate:
    """A valid product change on the root screen with a free reward; `changes` overrides any field."""
    root_id = root(model)
    fields = dict(
        id="x", lens="free_at_limit", kind="product_change", title="A daily bonus", anchor_evidence_ids=[],
        bible_mechanic="M11", what_is_different_here="Sits on the home screen.", adds="A daily bonus card.",
        removes_nothing_free=True, trigger_event="Second session of the day", trigger_state_id=root_id,
        placement="A card under the header", offer_copy="Play a short game to get a badge for 7 days.",
        reward={"kind": "cosmetic", "unit": "badge", "amount": 1, "duration": "7 days"},
        cost_inputs={"inference_count": 0, "tokens_in": 0, "tokens_out": 0, "minutes": 0, "currency_amount": 0},
        frequency_cap="1 per day, resets at midnight", decline_path="Card closes; nothing changes.",
        ad_fail_path="Card hides; no attempt used.", subscriber_treatment="Same offer, opt-in.",
        advertiser_category="Mobile games", character_use="None",
        flow_steps=[{"state_id": root_id, "caption": "Home as today"}, {"state_id": "new:offer", "caption": "The offer"},
                    {"state_id": "new:ad", "caption": "The game plays"}, {"state_id": root_id, "caption": "Badge shows"}],
        rationale="A small daily extra.")
    fields.update(changes)
    return Candidate(**fields)


def anchored(model: ProductModel, **changes) -> Candidate:
    """A valid existing_anchor candidate, for models that have an anchor to cite."""
    anchor = sorted(anchor_ids(model))[0]
    return candidate(model, **{"kind": "existing_anchor", "anchor_evidence_ids": [anchor], "adds": None, **changes})
