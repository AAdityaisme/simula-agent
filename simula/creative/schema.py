"""CreativeAttributes v0, the generation brief, and the content JSON one creative is assembled from. The vocabulary is
invented (v0). schemas/creative-attributes-v0.json and schemas/brief-v0.json state the same shapes as JSON Schema; the
cross-field rules (the gate rule and the provenance rule) live only in these models."""

import hashlib
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from simula.contracts import Strict

TIERS = ("sfw", "suggestive", "mature")
Tier = Literal["sfw", "suggestive", "mature"]
Format = Literal["INT", "RWD", "NAT"]
Hook = Literal["challenge", "help_host"]
Cta = Literal["Install Now", "Get the App", "Open App"]
CharacterKind = Literal["app_mascot", "app_persona", "publisher_character", "none"]
PuzzleFamily = Literal["solve_for_x", "next_in_sequence", "unscramble_word"]
Stamp = Literal["observed", "derived", "generated", "code", "assumed"]
TrainingInput = Literal["format", "gate_tier", "hook", "character.kind", "interaction.mechanic", "end_card.cta",
                        "advertiser.category_iab", "age_days"]
TRAINING_INPUTS = ("format", "gate_tier", "hook", "character.kind", "interaction.mechanic", "end_card.cta",
                   "advertiser.category_iab", "age_days")
PROVENANCE: dict[str, Stamp] = {
    "schema_version": "code", "creative_id": "code", "status": "code", "format": "assumed", "advertiser": "observed",
    "content_tier": "derived", "gate_tier": "derived", "character": "observed", "hook": "code", "interaction": "code",
    "proof": "observed", "copy": "generated", "end_card": "code", "qa": "code", "lineage": "code",
}


def gate_for(content_tier: Tier, has_unsafe_screens: bool) -> Tier:
    """The tier the content gate reads: content_tier, raised to at least suggestive when the app has unsafe screens."""
    if has_unsafe_screens and TIERS.index(content_tier) < TIERS.index("suggestive"):
        return "suggestive"
    return content_tier


def creative_id(html: str) -> str:
    """cr_ and the first 12 hex of the sha1 of the creative's HTML file."""
    return "cr_" + hashlib.sha1(html.encode()).hexdigest()[:12]


class Character(Strict):
    kind: CharacterKind
    name: str = Field(max_length=40)
    art_ref: str | None
    evidence_id: str | None


class Puzzle(Strict):
    family: PuzzleFamily
    prompt: str
    options: list[str] = Field(min_length=3, max_length=3)
    answer: int = Field(ge=0, le=2)


class Claim(Strict):
    text: str = Field(max_length=90)
    evidence_id: str


class Proof(Strict):
    screen_id: str
    claims: list[Claim] = Field(min_length=1, max_length=2)


class Copy(Strict):
    intro: str = Field(max_length=90)
    captions: list[Annotated[str, Field(max_length=60)]] = Field(min_length=3, max_length=3)
    right_line: str = Field(max_length=40)
    wrong_hint: str = Field(max_length=40)
    end_headline: str = Field(max_length=60)


class Content(Strict):
    """Everything one creative shows. Build it with Content(copy=...) and read .copy_: a field named copy would shadow
    BaseModel.copy, so the field is copy_ and its JSON name is copy."""
    model_config = ConfigDict(serialize_by_alias=True)
    variant_id: str
    app_name: str
    host: Character
    hook: Hook
    seed: int
    puzzles: list[Puzzle] = Field(min_length=3, max_length=3)
    copy_: Copy = Field(alias="copy")
    proof: Proof
    cta: Cta


class Advertiser(Strict):
    app_package: str
    app_name: str
    app_version: str
    run_id: str
    has_unsafe_screens: bool
    campaign_id: str | None
    category_iab: str | None


class Interaction(Strict):
    kind: Literal["minigame"]
    mechanic: Literal["quick_puzzles"]
    puzzle_families: list[PuzzleFamily] = Field(min_length=1)
    puzzle_count: Literal[3]
    seed: int

    @model_validator(mode="after")
    def families_once(self):
        if len(set(self.puzzle_families)) != len(self.puzzle_families):
            raise ValueError("puzzle_families lists a family twice")
        return self


class EndCard(Strict):
    cta: Cta


class QA(Strict):
    playthrough_pass: bool
    grounding_pass: bool
    tier_pass: bool
    repair_round: Literal[0, 1]
    first_pass_accept: bool
    review_verdict: Literal["pending", "accepted", "accepted_with_edits", "rejected"]
    review_edits: int = Field(ge=0)
    reviewers: list[str]


class Lineage(Strict):
    brief_id: str
    parent_creative_id: str | None
    generator_version: str
    prompt_sha: str
    template_sha: str


class CreativeAttributes(Strict):
    """One creative's attribute record (schemas/creative-attributes-v0.json). Build with copy=..., read .copy_."""
    model_config = ConfigDict(serialize_by_alias=True)
    schema_version: Literal[0]
    creative_id: str = Field(pattern=r"^cr_[0-9a-f]{12}$")
    status: Literal["draft", "failed", "accepted", "live", "retired"]
    format: Format
    age_days: int | None = Field(default=None, ge=0)
    advertiser: Advertiser
    content_tier: Tier
    gate_tier: Tier
    character: Character
    hook: Hook
    interaction: Interaction
    proof: Proof
    copy_: Copy = Field(alias="copy")
    end_card: EndCard
    qa: QA
    lineage: Lineage
    provenance: dict[str, Stamp]

    @field_validator("age_days", mode="before")
    @classmethod
    def age_is_omitted_not_null(cls, value):
        """An unset age is left out, as the JSON Schema requires; None is only the in-memory default."""
        if value is None:
            raise ValueError("age_days must be an integer when present; omit it while unset")
        return value

    @model_validator(mode="after")
    def gate_and_provenance(self):
        want = gate_for(self.content_tier, self.advertiser.has_unsafe_screens)
        if self.gate_tier != want:
            raise ValueError(f"gate_tier is {self.gate_tier}; content_tier {self.content_tier} with has_unsafe_screens="
                             f"{self.advertiser.has_unsafe_screens} needs {want}")
        fields = {f.alias or name for name, f in type(self).model_fields.items() if name in self.model_fields_set}
        if set(self.provenance) != fields - {"provenance"}:
            raise ValueError(f"provenance must stamp each top-level field once: {sorted(fields - {'provenance'})}")
        return self


class Brief(Strict):
    """A generation brief (schemas/brief-v0.json). Schema only in v0: nothing writes or reads one yet."""
    brief_id: str = Field(pattern=r"^br_[a-z0-9_]+$")
    advertiser: str
    format: Format
    keep: dict[TrainingInput, list[str]]
    drop: dict[TrainingInput, list[str]]
    explore: dict[TrainingInput, list[str]]
    siblings_of: list[Annotated[str, Field(pattern=r"^cr_[0-9a-f]{12}$")]]
    max_variants: int = Field(ge=1)
    source: str


def dump(attrs: CreativeAttributes) -> dict:
    """The record as JSON data, as creative.json holds it: age_days appears only once the registry has set it."""
    return attrs.model_dump(mode="json", exclude_unset=True)
