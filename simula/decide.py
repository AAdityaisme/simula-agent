"""Jev's one job: rank the explorer's untried taps (and answer the one content-filter question). Real Jev
(TypeSafe) by default; the system-one adapter on Haiku answers the same question when
SIMULA_JEV_BACKEND=adapter. Every call goes through the stage's $ cap, the response cache, and --replay,
like model calls do."""

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from itertools import zip_longest
from pathlib import Path

from typesafe_sdk import Choice

from simula import config, llm
from simula.llm import Budget, ReplayMiss, usd
from simula.runlog import trace

ADAPTER_MODEL = "claude-haiku-4-5-20251001"
CHUNK = 10
WORST_TOKENS_OUT = 1024


class JevFailed(Exception):
    """Jev errored twice, or answered outside its options (a refusal). The caller asks Sonnet instead."""


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


def priced_as(backend: str) -> str:
    return "jev-latest" if backend == "typesafe" else ADAPTER_MODEL


def ask_choice(state, instructions: str, labels: list[str], backend: str) -> ChoiceResult:
    ids = option_ids(labels)
    questions = {"next": Choice(instructions=instructions, criteria=ids)}
    started = time.monotonic()
    response = BACKENDS[backend](state, questions)
    answer = response.choices["next"]
    tokens_in = response.usage.input_tokens or 0
    tokens_out = response.usage.output_tokens or 0
    return ChoiceResult(option_id=answer.choice, confidence=answer.confidence,
                        probabilities=dict(answer.probabilities), model=response.model,
                        tokens_in=tokens_in, tokens_out=tokens_out,
                        usd=usd(priced_as(backend), tokens_in, tokens_out), seconds=time.monotonic() - started)


def cache_key(backend: str, state: str, instructions: str, labels: list[str]) -> str:
    blob = json.dumps({"jev": backend, "state": state, "instructions": instructions, "labels": labels}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def choose(trace_path: Path, stage: str, step: str, state: str, instructions: str, labels: list[str],
           profile: str = "real", budget: Budget | None = None, no_cache: bool = False, replay: bool = False,
           cache_dir: Path = llm.CACHE) -> ChoiceResult:
    """One Choice question. Cached like a model call (only answers inside the options are stored); one retry
    on an error; a second error or a refusal raises JevFailed."""
    backend = config.jev_backend(profile)
    budget = budget or Budget.for_stage(stage, trace_path)
    key = cache_key(backend, state, instructions, labels)
    path = cache_dir / f"{key}.json"
    if not no_cache and path.exists():
        result = ChoiceResult(**json.loads(path.read_text()))
        trace(trace_path, stage=stage, step=step, decider="jev", model=result.model, confidence=result.confidence,
              tokens_in=result.tokens_in, tokens_out=result.tokens_out, cache_hit=True,
              note=f"{backend} picked {result.option_id} (cached)")
        return result
    if replay:
        raise ReplayMiss(f"--replay: no cached Jev answer for {stage}/{step} (key {key[:12]})")
    tokens_in = llm.estimate_tokens_in(state + instructions + "".join(labels), [])
    worst = llm.worst_case_usd(priced_as(backend), tokens_in, WORST_TOKENS_OUT)
    failed = usd(priced_as(backend), tokens_in, 0)  # like a stream that never started: its input, estimated
    for attempt in (1, 2):
        budget.reserve(worst)
        try:
            result = ask_choice(state, instructions, labels, backend)
        except Exception as e:  # noqa: BLE001 - any backend failure gets one retry, then Sonnet
            budget.charge(failed, worst)
            trace(trace_path, stage=stage, step=step, decider="jev", model=priced_as(backend), tokens_in=tokens_in,
                  usd=round(failed, 6), outcome="retry" if attempt == 1 else "error",
                  note=f"{type(e).__name__}: {str(e)[:160]}")
            continue
        budget.charge(result.usd, worst)
        if result.option_id not in option_ids(labels):
            trace(trace_path, stage=stage, step=step, decider="jev", model=result.model, usd=round(result.usd, 6),
                  outcome="refusal", note=f"answered {result.option_id!r}, not one of the options")
            raise JevFailed(f"{step}: Jev answered {result.option_id!r}")
        cache_dir.mkdir(exist_ok=True)
        path.write_text(json.dumps(asdict(result), indent=1))
        trace(trace_path, stage=stage, step=step, decider="jev", model=result.model, confidence=result.confidence,
              tokens_in=result.tokens_in, tokens_out=result.tokens_out, usd=round(result.usd, 6),
              note=f"{backend} picked {result.option_id} in {result.seconds:.2f}s")
        return result
    raise JevFailed(f"{step}: Jev failed twice")


def index_of(option_id: str) -> int:
    return int(option_id[1:]) - 1


def ordered(result: ChoiceResult, members: list[int]) -> list[int]:
    """The chosen option first, then the others by Jev's probability."""
    chosen = members[index_of(result.option_id)]
    probability = {members[index_of(o)]: p for o, p in result.probabilities.items() if o in option_ids(members)}
    return [chosen] + sorted((m for m in members if m != chosen), key=lambda m: -probability.get(m, 0.0))


def rank(trace_path: Path, stage: str, step: str, state: str, instructions: str, labels: list[str],
         **kwargs) -> tuple[list[int], float]:
    """Indexes into labels, most likely first, plus the top option's confidence. Chunks of <= 10 options;
    with more than one chunk, the chunk winners go to a final round and the runners-up follow in turn."""
    chunks = [list(range(i, min(i + CHUNK, len(labels)))) for i in range(0, len(labels), CHUNK)]
    ranked = []
    for n, chunk in enumerate(chunks, start=1):
        result = choose(trace_path, stage, f"{step}.c{n}" if len(chunks) > 1 else step, state, instructions,
                        [labels[i] for i in chunk], **kwargs)
        ranked.append(ordered(result, chunk))
    if len(ranked) == 1:
        return ranked[0], result.confidence
    winners = [r[0] for r in ranked]
    final = choose(trace_path, stage, f"{step}.final", state, instructions, [labels[i] for i in winners], **kwargs)
    runners_up = [i for group in zip_longest(*(r[1:] for r in ranked)) for i in group if i is not None]
    return ordered(final, winners) + runners_up, final.confidence
