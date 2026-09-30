"""The brand-safety gate on a real chat screen, both real judges (a few cents; `uv run pytest -m live`)."""

import pytest

from simula import config, llm
from simula.stages import judge
from tests.judge_helpers import idea
from tests.propose_fixtures import golden

pytestmark = pytest.mark.live
CHAT = "s07"  # the golden Luzia chat thread
SHEET = "A bottom sheet over the chat thread that slides up when the free message counter reaches zero"
# case: (chat screen's rating, placement, gate passes)
CASES = {"a message in the conversation": ("safe", "Sent as a message from the assistant inside the conversation, "
                                                   "between the user's messages", False),
         "a limit sheet over a chat rated unknown": ("unknown", SHEET, True),
         "a limit sheet over a chat rated unsafe": ("unsafe", SHEET, False)}


@pytest.mark.parametrize("who", ["judge_1", "judge_2"])
@pytest.mark.parametrize("case", CASES)
def test_brand_safety_gate(case, who, tmp_path):
    config.load_env()
    rating, placement, passes = CASES[case]
    base = golden("luzia")
    model = base.model_copy(update={"states": [s.model_copy(update={"content_rating": rating}) if s.id == CHAT else s
                                               for s in base.states]})
    c = idea(model, trigger_state_id=CHAT, placement=placement, trigger_event="The free message counter reaches zero",
             adds="5 more messages today after the free ones run out",
             offer_copy="You're out of free messages. Play a short game for 5 more messages today.",
             reward={"kind": "inference", "unit": "messages", "amount": 5, "duration": "today"},
             flow_steps=[{"state_id": CHAT, "caption": "The chat as today"},
                         {"state_id": "new:offer", "caption": "The offer appears"},
                         {"state_id": "new:ad", "caption": "The game plays"},
                         {"state_id": CHAT, "caption": "Back in the chat with 5 more messages"}])
    v = judge.ask_judge(config.roles("real")[who], c, model, trace_path=tmp_path / "trace.jsonl", stage="judge",
                        step=case, budget=llm.Budget("judge", 1.0))
    assert v.g_brand_safety.passed is passes, v.g_brand_safety.reason
