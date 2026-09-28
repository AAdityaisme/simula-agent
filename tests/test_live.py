"""Real API calls (cents). Skipped in CI: run with `uv run pytest -m live`."""

import pytest
from pydantic import BaseModel

from simula import config, decide, llm

pytestmark = pytest.mark.live


class Word(BaseModel):
    word: str


@pytest.fixture(autouse=True)
def env():
    config.load_env()


def test_structured_call_round_trips_through_the_cache(tmp_path):
    kwargs = dict(trace_path=tmp_path / "trace.jsonl", stage="model", step="live", model="claude-haiku-4-5-20251001",
                  effort=None, system="", max_tokens=200, budget=llm.Budget("model", 0.05), schema=Word,
                  messages=[{"role": "user", "content": [{"type": "text", "text": 'Return word="ready".'}]}],
                  cache_dir=tmp_path / "cache")
    first, reply = llm.call(**kwargs)
    second, _ = llm.call(**kwargs, replay=True)
    assert first == second and reply.tokens_in > 0


@pytest.mark.parametrize("backend", ["typesafe", "adapter"])
def test_jev_picks_the_paywall_tap(backend):
    labels = ["Open settings", "Scroll the feed", "See janitor+ (subscription paywall button)", "Open a chat"]
    result = decide.ask_choice("An app screen. Goal: find the paywall.", "Which tap most likely reveals a paywall?",
                               labels, backend)
    assert result.option_id == "o03"
