"""Jev's one job: rank the explorer's untried taps. Real Jev (TypeSafe) by default; the
system-one adapter on Haiku answers the same question when SIMULA_JEV_BACKEND=adapter.

PR 0 ships the interface and both clients. Chunking, retries, and the Sonnet hand-off are PR 1.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from typesafe_sdk import Choice

from simula import config
from simula.llm import usd
from simula.runlog import trace

ADAPTER_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class ChoiceResult:
    option_id: str
    confidence: float
    probabilities: dict
    model: str
    tokens_in: int
    tokens_out: int
    usd: float
    seconds: float


def option_ids(labels: list[str]) -> dict[str, str]:
    """Stable ids (o01, o02, ...) so two taps with the same label never collide."""
    return {f"o{i:02d}": label for i, label in enumerate(labels, start=1)}


def _typesafe(state, questions):
    from typesafe_sdk import TypeSafeClient
    with TypeSafeClient(api_key=os.environ["TYPESAFE_API_KEY"]) as client:
        return client.system_one(state, questions, model="jev-latest", timeout=30)


def _adapter(state, questions):
    from system_one_adapter import SystemOneAdapterClient
    with SystemOneAdapterClient(structured_outputs=True, llm_answer_mode="probabilities",
                                normalize_probabilities=True) as client:
        return client.system_one(state, questions, provider="anthropic", model=ADAPTER_MODEL)


BACKENDS = {"typesafe": _typesafe, "adapter": _adapter}


def ask_choice(state, instructions: str, labels: list[str], backend: str) -> ChoiceResult:
    ids = option_ids(labels)
    questions = {"next": Choice(instructions=instructions, criteria=ids)}
    started = time.monotonic()
    response = BACKENDS[backend](state, questions)
    answer = response.choices["next"]
    tokens_in = response.usage.input_tokens or 0
    tokens_out = response.usage.output_tokens or 0
    priced_as = "jev-latest" if backend == "typesafe" else ADAPTER_MODEL
    return ChoiceResult(option_id=answer.choice, confidence=answer.confidence,
                        probabilities=dict(answer.probabilities), model=response.model,
                        tokens_in=tokens_in, tokens_out=tokens_out,
                        usd=usd(priced_as, tokens_in, tokens_out), seconds=time.monotonic() - started)


def choose(trace_path: Path, stage: str, step: str, state, instructions: str, labels: list[str],
           profile: str = "real") -> ChoiceResult:
    backend = config.jev_backend(profile)
    result = ask_choice(state, instructions, labels, backend)
    trace(trace_path, stage=stage, step=step, decider="jev", model=result.model, confidence=result.confidence,
          tokens_in=result.tokens_in, tokens_out=result.tokens_out, usd=round(result.usd, 6),
          note=f"{backend} picked {result.option_id} in {result.seconds:.2f}s")
    return result
