"""Every stage contract, frozen in PR 0. Changing one needs Aadi's approval and its own PR.

Schemas a model fills (listed in MODEL_FACING) follow three rules: no dict fields, no recursion,
at most 4 levels of nesting. The Anthropic SDK turns a dict field into an object that can only be
{} and pydantic still accepts it, so a dict-shaped verdict would silently pass everything.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- geometry and device ----------

class Rect(Strict):
    x: float
    y: float
    w: float
    h: float


class Device(Strict):
    w_px: int = 1080
    h_px: int = 2400
    density: int = 420
    scale: float = 2.625
    content_top_px: int = 136
    content_bottom_px: int = 2337


# ---------- product model (stage 2) ----------

StateKind = Literal["screen", "modal", "sheet", "external", "blocked"]
ContentRating = Literal["safe", "mixed", "unsafe", "unknown"]
ElementSource = Literal["mcp", "vision"]
EdgeAction = Literal["tap", "swipe", "back", "type"]
Transition = Literal["push", "modal", "tab", "back", "replace", "unknown"]
MechanicKind = Literal["paywall", "limit", "currency", "entitlement", "ad", "streak", "other"]
ClaimStatus = Literal["observed", "inferred", "unknown"]
LedgerKind = Literal["price", "limit", "meter", "currency", "paywall_bullet", "actor", "experience"]
AppCategory = Literal["chat", "content", "learning", "game", "utility", "other"]


class Element(Strict):
    id: str
    mcp_ref: str | None
    type: str
    text: str
    label: str
    source: ElementSource
    rect_px: Rect
    rect_dp: Rect
    role: str
    asset_png: str | None
    fg_hex: str | None
    bg_hex: str | None
    font_px: float | None
    font_guess: str
    in_mock: bool
    repeat_group: str | None


class State(Strict):
    id: str
    kind: StateKind
    parent_id: str | None
    name: str
    purpose: str
    fingerprint: str
    canonical_png: str
    elements: list[Element]
    in_mock_scope: bool
    content_rating: ContentRating
    dynamic_regions: list[Rect] = Field(description="Device px, like rect_px. QA converts them to content dp.")
    blocked_reason: str | None


class Edge(Strict):
    id: str
    from_state: str
    to_state: str
    element_id: str | None
    action: EdgeAction
    transition: Transition
    change_summary: str


class Flow(Strict):
    id: str
    name: str
    purpose: str
    edge_ids: list[str]
    evidence_ids: list[str]


class Mechanic(Strict):
    id: str
    kind: MechanicKind
    evidence_ids: list[str]
    summary: str
    observed_numbers: list[str]
    status: ClaimStatus


class CrossScreenValue(Strict):
    id: str
    label: str
    value_text: str
    evidence_ids: list[str]


class LedgerItem(Strict):
    id: str
    kind: LedgerKind
    verbatim: str
    evidence_ids: list[str]


class TermMeaning(Strict):
    """An app-specific word a mechanic or ledger line uses (a feature name, a unit, a plan tier)."""
    term: str
    meaning: str = Field(description="One plain-language line.")
    defined_by: list[str] = Field(description="Element ids whose text defines or explains the term.")
    used_in: list[str] = Field(description="Mechanic or ledger ids that use the term.")


class Term(TermMeaning):
    observed: bool = Field(description="Code: false when no cited element's text carries the term; the meaning "
                                       "then reads 'meaning not observed' and nothing may build on it.")


class QuestionDraft(Strict):
    """Something the captures could not answer that another explore pass could."""
    id: str
    question: str
    start_state: str = Field(description="The state id where the explorer should begin.")
    look_for: str = Field(description="One plain sentence on what would answer it.")


class OpenQuestion(QuestionDraft):
    answered: bool = False


class Coverage(Strict):
    states_found: int
    actions_taken: int
    stop_reason: str
    checklist_answered: list[str]
    checklist_open: list[str]


class Provenance(Strict):
    source: Literal["explorer_run", "fixture"]
    explorer_run_id: str | None = None
    fixture_path: str | None = None


class ProductModel(Strict):
    schema_version: int = SCHEMA_VERSION
    app: str
    app_version: str
    app_category: AppCategory
    run_id: str
    device: Device
    states: list[State]
    edges: list[Edge]
    flows: list[Flow]
    mechanics: list[Mechanic]
    cross_screen_values: list[CrossScreenValue]
    value_ledger: list[LedgerItem]
    open_questions: list[str]
    coverage: Coverage
    provenance: Provenance
    terms: list[Term] = []
    questions: list[OpenQuestion] = Field(default=[], description="The open questions an explore pass can act on, "
                                          "most monetization-relevant first, at most 5; open_questions holds their text.")
    mock_order: list[str] = Field(default=[], description="The in-scope state ids in priority order: root, paywall/"
                                  "limit/currency/ad states, other mechanic states (a modal after its parent), "
                                  "then core-flow states.")


# Stage 2's one model call fills only meaning, keyed by ids code gave it.

class StateMeaning(Strict):
    state_id: str
    name: str
    purpose: str
    content_rating: ContentRating


class ElementMeaning(Strict):
    element_id: str
    role: str
    font_guess: str


class ModelMeaning(Strict):
    app_category: AppCategory
    states: list[StateMeaning]
    elements: list[ElementMeaning]
    flows: list[Flow]
    mechanics: list[Mechanic]
    cross_screen_values: list[CrossScreenValue]
    value_ledger: list[LedgerItem]
    terms: list[TermMeaning]
    open_questions: list[QuestionDraft]


# ---------- explore (stage 1) model calls ----------

class IconName(Strict):
    box_id: int
    name: str


class TapPoint(Strict):
    x: int
    y: int
    name: str


class IconPass(Strict):
    names: list[IconName]
    extra_points: list[TapPoint]


class HardScreenAction(Strict):
    action: Literal["tap", "type", "back", "swipe", "done"]
    element_id: str | None
    text: str | None
    direction: Literal["up", "down"] | None
    reason: str


# ---------- QA and flows edits (stages 4 and 7) ----------

class Fix(Strict):
    element_id: str
    problem: str
    fix: str


class Critique(Strict):
    fixes: list[Fix]
    summary: str


class Edit(Strict):
    find: str
    replace: str
    reason: str


class Edits(Strict):
    edits: list[Edit]


class ScreenMetrics(Strict):
    state_id: str
    ssim_masked: float | None
    pixelmatch_ratio: float | None
    masked_coverage: float
    bounds_ok_share: float
    nav_pass_rate: float
    score: float


class QAMetrics(Strict):
    schema_version: int = SCHEMA_VERSION
    round: int
    screens: list[ScreenMetrics]
    cross_screen_failures: list[str]
    score: float


# ---------- propose (stage 5) ----------

RewardKind = Literal[
    "inference", "image", "voice", "feature_time", "content_unlock",
    "currency", "cosmetic", "queue_priority", "streak_protection",
]
CandidateKind = Literal["existing_anchor", "product_change", "no_opportunity"]


class Reward(Strict):
    kind: RewardKind
    unit: str
    amount: float
    duration: str


class CostInputs(Strict):
    inference_count: int
    tokens_in: int
    tokens_out: int
    minutes: float
    currency_amount: float = Field(description="The USD price the app charges for exactly what the reward grants; 0 when no price was observed.")


class FlowStep(Strict):
    state_id: str
    caption: str


class CandidateDraft(Strict):
    id: str
    lens: str
    kind: CandidateKind
    title: str
    anchor_evidence_ids: list[str]
    bible_mechanic: str
    what_is_different_here: str
    adds: str | None
    removes_nothing_free: bool
    trigger_event: str
    trigger_state_id: str
    placement: str
    offer_copy: str
    reward: Reward
    cost_inputs: CostInputs
    frequency_cap: str
    decline_path: str
    ad_fail_path: str
    subscriber_treatment: str
    advertiser_category: str
    character_use: str
    flow_steps: list[FlowStep]
    rationale: str


class LensOutput(Strict):
    candidates: list[CandidateDraft]


class Economics(Strict):
    cost_2k: float
    cost_8k: float
    breakeven_ecpm_2k: float
    breakeven_ecpm_8k: float
    benchmark_ecpm: float
    verdict: Literal["PASS", "CONDITIONAL", "FAIL"]
    assumption_line: str


class Candidate(CandidateDraft):
    economics: Economics | None = None
    reach_score: float | None = None
    rank_score: float | None = None
    dropped_reason: str | None = None


# ---------- judge (stage 6) ----------

class Check(Strict):
    passed: bool
    reason: str


class Verdict(Strict):
    candidate_id: str
    g_policy: Check
    g_no_cash: Check
    g_no_chat_content: Check
    g_no_free_removal: Check
    g_brand_safety: Check
    c1_revealed_value: Check
    c2_evidence: Check
    c4_protects_subscription: Check
    c5_moment: Check
    c6_fits_simula: Check
    c7_specific: Check
    other_concern: str
    fixable: bool


GATES = ("g_policy", "g_no_cash", "g_no_chat_content", "g_no_free_removal", "g_brand_safety")
JUDGMENT = ("c1_revealed_value", "c2_evidence", "c4_protects_subscription", "c5_moment",
            "c6_fits_simula", "c7_specific")


class PairwisePick(Strict):
    winner: Literal["A", "B"]
    reason: str


class Decision(Strict):
    candidate_id: str
    final: Literal["accept", "conditional", "reject", "needs_human"]
    checks_passed: int = Field(description="Of the 11 LLM-judged checks, those every judge that ran passed.")
    checks_total: int = Field(description="11: the 5 gates plus the 6 judgment checks.")
    rank_score: float | None
    gate_fails: list[str]
    judgment_splits: list[str]
    verdict_paths: list[str]
    economics_verdict: Literal["PASS", "CONDITIONAL", "FAIL"] | None
    revision_of: str | None
    failure_type: Literal["proposal", "product_model", "explore"] | None
    rerun_stage: str | None


# ---------- explore/ -> model/ hand-off (docs/CONTRACTS.md §3) ----------

class Point(Strict):
    x: int
    y: int


class ActionLine(Strict):
    """one line of explore/actions.jsonl"""
    step: int
    from_state: str
    to_state: str | None
    action: Literal["tap", "swipe", "back", "type", "relaunch"]
    mcp_ref: str | None
    tap_px: Point | None
    transition: Transition
    change_summary: str
    outcome: Literal["ok", "denied", "timeout", "error"]
    loop_pass: int | None = Field(default=None, description="1, 2, ... when this line is one pass of the core loop; "
                                  "null for tour moves.")
    loop_stop: str | None = Field(default=None, description="On the pass where a limit, paywall, or ad appeared: what "
                                  "it was, in a few words. The loop stops there.")


class IconLabel(Strict):
    mcp_ref: str
    name: str


class VisionElement(Strict):
    name: str
    rect_px: Rect


# ---------- file-level wrappers: every JSON file a stage writes carries schema_version ----------

class StateFile(Strict):
    """explore/states/<sid>.json"""
    schema_version: int = SCHEMA_VERSION
    state_id: str
    kind: StateKind
    parent_id: str | None
    fingerprint: str
    foreground_package: str
    screenshot: str
    elements_reply: str
    settled: bool
    settle_seconds: float
    dynamic_regions: list[Rect] = Field(description="Device px.")
    captured_at: str
    icon_labels: list[IconLabel] = []
    vision_elements: list[VisionElement] = []
    blocked_reason: str | None = None


class ExploreFile(Strict):
    """explore/explore.json"""
    schema_version: int = SCHEMA_VERSION
    app_package: str
    app_version: str | None
    budget: str
    relaunches: int
    content_filter: str | None
    blocked_state_ids: list[str]
    coverage: Coverage
    device: Device = Device()


class ContractError(Strict):
    kind: str
    detail: str
    screen: str | None


class ContractReport(Strict):
    """mock/contract_report.json"""
    schema_version: int = SCHEMA_VERSION
    passed: bool
    screens: list[str]
    errors: list[ContractError]


class Lens(Strict):
    id: str
    name: str
    kind: Literal["fixed", "ledger"]
    ledger_ids: list[str]
    focus: str


class LensesFile(Strict):
    """propose/lenses.json"""
    schema_version: int = SCHEMA_VERSION
    lenses: list[Lens]


class CandidatesFile(Strict):
    """propose/candidates.json"""
    schema_version: int = SCHEMA_VERSION
    candidates: list[Candidate]


class DecisionsFile(Strict):
    """judge/decisions.json"""
    schema_version: int = SCHEMA_VERSION
    decisions: list[Decision]


FILE_WRAPPERS = [StateFile, ExploreFile, ContractReport, LensesFile, CandidatesFile, DecisionsFile]


# ---------- run bookkeeping (code-only, so dicts are fine here) ----------

Decider = Literal["code", "jev", "model", "human"]
Outcome = Literal["ok", "retry", "max_tokens", "refusal", "schema_fail", "timeout", "denied",
                  "blocked", "cap", "not_built", "error"]


class TraceLine(Strict):
    ts: str
    stage: str
    step: str
    decider: Decider
    model: str | None = None
    effort: str | None = None
    confidence: float | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    usd: float = 0.0
    cache_hit: bool = False
    outcome: Outcome = "ok"
    note: str = ""


class FileHash(Strict):
    path: str
    sha256: str


class DoneMarker(Strict):
    schema_version: int = SCHEMA_VERSION
    stage: str
    input_hashes: list[FileHash]
    prompt_hashes: list[FileHash]
    params_hash: str
    output_hashes: list[FileHash]
    provenance: Provenance
    finished_at: str


class Manifest(Strict):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    app: str
    created_at: str
    git_sha: str
    git_dirty: bool
    profile: str
    budget: str
    allow_account_create: bool
    roles: dict[str, str]
    prompt_hashes: dict[str, str]
    app_package: str
    app_version: str | None
    mobile_mcp_version: str | None
    playwright_version: str | None
    caps_usd: dict[str, float]
    no_send: bool
    provenance: Provenance
    stages_done: list[str]
    usd_total: float
    fallbacks_used: list[str] = []


MODEL_FACING = [ModelMeaning, IconPass, HardScreenAction, Critique, Edits, LensOutput, Verdict,
                PairwisePick]
