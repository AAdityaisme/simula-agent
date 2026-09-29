"""Every model call goes through here: cache, $ cap, capability table, typed failures, trace.

Messages use one provider-neutral shape:
    [{"role": "user", "content": [{"type": "text", "text": "..."}, {"type": "image", "png": b"..."}]}]
"""

import base64
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from simula import config
from simula.runlog import read_trace, record_fallback, trace

CACHE = config.ROOT / "cache"
IMAGE_TOKENS_WORST = 4784  # high-res tier cap per image (Anthropic vision docs, 2026-09-27)
RATE_HEADERS = ("anthropic-ratelimit-", "x-ratelimit-", "retry-after")
REQUEST_TIMEOUT_S = 180.0
STREAM_IDLE_TIMEOUT_S = 60.0


class LLMFailure(Exception):
    """A typed failure. tokens_in / tokens_out are what the failed attempt still cost (an aborted stream)."""
    def __init__(self, outcome: str, detail: str = "", raw: str = "", tokens_in: int = 0, tokens_out: int = 0):
        super().__init__(f"{outcome}: {detail}")
        self.outcome = outcome
        self.raw = raw
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


def _failure(outcome: str, error: Exception) -> "LLMFailure":
    """An LLMFailure that keeps whatever an aborted stream already spent (set on the error by _drain)."""
    return LLMFailure(outcome, str(error), tokens_in=getattr(error, "tokens_in", 0),
                      tokens_out=getattr(error, "tokens_out", 0))


class CapReached(SystemExit):
    """Our own $ cap: a planned stop, where the best work so far is valid. Only Budget raises it."""


class ProviderUnavailable(SystemExit):
    """The provider refuses every call (a usage, spend, quota or billing limit on the account): an outage, where
    nothing is valid. Never cached; the stage fails with no done.json, and a rerun resumes there."""


class ReplayMiss(SystemExit):
    pass


@dataclass
class Reply:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    stop_reason: str = "end_turn"
    headers: dict = field(default_factory=dict)
    failure: str = ""  # the typed outcome when this recorded attempt failed; "" for a usable answer


@dataclass
class Budget:
    """A stage's $ cap. Each call holds its worst case from reserve() until charge() settles it, so calls
    running at the same time can't pass the cap together."""
    stage: str
    cap: float
    spent: float = 0.0
    held: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def for_stage(cls, stage: str, trace_path: Path, cap: float | None = None) -> "Budget":
        spent = sum(line.usd for line in read_trace(trace_path) if line.stage == stage)
        return cls(stage, config.stage_cap(stage) if cap is None else cap, spent)

    def reserve(self, worst_usd: float) -> None:
        with self.lock:
            if self.spent + self.held + worst_usd > self.cap:
                raise CapReached(f"{self.stage}: next call could cost ${worst_usd:.2f}, ${self.spent:.2f} of "
                                 f"${self.cap:.2f} already spent, ${self.held:.2f} held by calls in flight; "
                                 "raise with --usd-cap")
            self.held += worst_usd

    def charge(self, usd: float, reserved: float) -> None:
        """Settles one call: adds what it cost and gives back exactly what its reserve() held. `reserved` has no
        default, so a caller that forgets to give its hold back fails at once instead of shrinking the cap."""
        with self.lock:
            self.spent += usd
            self.held -= reserved


# ---------- cache ----------

def canonical(messages: list[dict]) -> list[dict]:
    def part(p: dict) -> dict:
        if p["type"] == "image":
            return {"type": "image", "sha256": hashlib.sha256(p["png"]).hexdigest()}
        return p
    return [{"role": m["role"], "content": [part(p) for p in m["content"]]} for m in messages]


def cache_key(provider: str, model: str, system: str, messages: list[dict], params: dict, attempt: int) -> str:
    blob = json.dumps({"provider": provider, "model": model, "system": system, "messages": canonical(messages),
                       "params": params, "attempt": attempt}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_read(key: str, cache_dir: Path = CACHE) -> Reply | None:
    path = cache_dir / f"{key}.json"
    return Reply(**json.loads(path.read_text())) if path.exists() else None


def cache_write(key: str, reply: Reply, cache_dir: Path = CACHE) -> None:
    cache_dir.mkdir(exist_ok=True)
    data = {k: v for k, v in reply.__dict__.items() if k != "headers"}
    (cache_dir / f"{key}.json").write_text(json.dumps(data, indent=1))


# ---------- money ----------

def estimate_tokens_in(system: str, messages: list[dict]) -> int:
    chars = len(system) + sum(len(p.get("text", "")) for m in messages for p in m["content"])
    images = sum(p["type"] == "image" for m in messages for p in m["content"])
    return chars // 3 + images * IMAGE_TOKENS_WORST


def usd(model: str, tokens_in: int, tokens_out: int, tokens_cached: int = 0) -> float:
    m = config.models()[model]
    fresh = max(tokens_in - tokens_cached, 0)
    return (fresh * m["price_in"] + tokens_cached * m["price_cached_in"] + tokens_out * m["price_out"]) / 1e6


def worst_case_usd(model: str, tokens_in: int, max_tokens: int) -> float:
    return usd(model, tokens_in, max_tokens)


# ---------- providers ----------

def json_schema_for(provider: str, schema: type[BaseModel]) -> dict:
    if provider == "anthropic":
        from anthropic.lib._parse._transform import transform_schema
        return transform_schema(schema)
    from openai.lib._pydantic import to_strict_json_schema
    return to_strict_json_schema(schema)


def _rate_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower().startswith(RATE_HEADERS)}


def _anthropic_content(parts: list[dict]) -> list[dict]:
    return [{"type": "text", "text": p["text"]} if p["type"] == "text" else
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(p["png"]).decode()}}
            for p in parts]


def _spent(stream, tokens_in_estimate: int, max_tokens: int) -> tuple[int, int]:
    """What an aborted stream is charged. With no snapshot, message_start never arrived, so nothing was generated:
    only the estimated input. Otherwise its worst case, the input the stream reported plus max_tokens of output, or
    what streamed if that was more: the API bills thinking a stream may not carry, and sends the final output count
    only at the end, so what streamed is a floor, not the bill."""
    try:
        snapshot = stream.current_message_snapshot
    except (AssertionError, AttributeError):
        return tokens_in_estimate, 0
    streamed = sum(len(getattr(b, "text", None) or getattr(b, "thinking", None) or "") for b in snapshot.content) // 3
    return snapshot.usage.input_tokens, max(snapshot.usage.output_tokens, streamed, max_tokens)


def _drain(stream, total_timeout: float | None, tokens_in_estimate: int, max_tokens: int):
    """Reads the stream to its final message. On an abort (our total timeout or an SDK error mid-stream) the
    tokens already spent ride on the raised error. Everything raised here comes from reading the provider's
    stream, so an error the SDK leaves untyped (its event accumulator raises a plain RuntimeError, TypeError or
    IndexError on an event it can't place) becomes LLMFailure("error") and is retried like any provider error."""
    deadline = time.monotonic() + total_timeout if total_timeout else None
    try:
        for _ in stream:
            if deadline and time.monotonic() > deadline:
                raise LLMFailure("timeout", f"passed the {total_timeout:.0f}s total timeout")
        return stream.get_final_message()
    except Exception as e:
        e.tokens_in, e.tokens_out = _spent(stream, tokens_in_estimate, max_tokens)
        if isinstance(e, LLMFailure) or provider_error(e):
            raise
        raise LLMFailure("error", f"{type(e).__name__} reading the stream: {e}", tokens_in=e.tokens_in,
                         tokens_out=e.tokens_out) from e


def call_anthropic(model: str, system: str, messages: list[dict], effort: str | None,
                   schema: type[BaseModel] | None, max_tokens: int, total_timeout: float | None = None) -> Reply:
    import anthropic
    import httpx2  # transitive via anthropic, whose client it is
    caps = config.models()[model]
    streaming = max_tokens > caps["stream_above"]
    # On a stream the read timeout is the gap between chunks, so a stalled stream fails after 60 s.
    timeout = anthropic.Timeout(REQUEST_TIMEOUT_S, read=STREAM_IDLE_TIMEOUT_S) if streaming else REQUEST_TIMEOUT_S
    # A streamed call retries through our attempts loop, which charges each try; the SDK's retries would not.
    client = anthropic.Anthropic(max_retries=0 if streaming else 2, timeout=timeout)
    output_config = {}
    if effort and caps["supports_effort"]:
        output_config["effort"] = effort
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": json_schema_for("anthropic", schema)}
    kwargs = {"model": model, "max_tokens": max_tokens,
              "messages": [{"role": m["role"], "content": _anthropic_content(m["content"])} for m in messages]}
    if system:
        kwargs["system"] = system
    if output_config:
        kwargs["output_config"] = output_config
    try:
        if streaming:
            with client.messages.stream(**kwargs) as stream:
                message = _drain(stream, total_timeout, estimate_tokens_in(system, messages), max_tokens)
                headers = stream.response.headers
        else:
            raw = client.messages.with_raw_response.create(**kwargs)
            message, headers = raw.parse(), raw.headers
    except anthropic.APITimeoutError as e:
        raise _failure("timeout", e) from e
    except anthropic.RateLimitError as e:
        if "spend" in str(e).lower():
            raise ProviderUnavailable(f"provider spend limit reached: {e}") from e
        raise _failure("error", e) from e
    except anthropic.BadRequestError as e:
        # A console usage limit comes back as a 400, not a 429.
        if "usage limit" in str(e).lower():
            raise ProviderUnavailable(f"provider usage limit reached: {e}") from e
        raise _failure("error", e) from e
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        raise _failure("error", e) from e
    except httpx2.TransportError as e:  # the SDK doesn't wrap one raised while a stream is read
        raise _failure("timeout" if isinstance(e, httpx2.TimeoutException) else "error", e) from e
    text = "".join(block.text for block in message.content if block.type == "text")
    usage = message.usage
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    return Reply(text=text, model=message.model, tokens_in=usage.input_tokens + cached,
                 tokens_out=usage.output_tokens, tokens_cached=cached, stop_reason=message.stop_reason or "",
                 headers=_rate_headers(headers))


def _openai_content(parts: list[dict]) -> list[dict]:
    return [{"type": "input_text", "text": p["text"]} if p["type"] == "text" else
            {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(p["png"]).decode()}
            for p in parts]


def call_openai(model: str, system: str, messages: list[dict], effort: str | None,
                schema: type[BaseModel] | None, max_tokens: int, total_timeout: float | None = None) -> Reply:
    import openai
    client = openai.OpenAI(max_retries=2, timeout=REQUEST_TIMEOUT_S)
    kwargs = {"model": model, "max_output_tokens": max_tokens,
              "input": [{"role": m["role"], "content": _openai_content(m["content"])} for m in messages]}
    if system:
        kwargs["instructions"] = system
    if effort:
        kwargs["reasoning"] = {"effort": effort}
    if schema:
        kwargs["text"] = {"format": {"type": "json_schema", "name": schema.__name__, "strict": True,
                                     "schema": json_schema_for("openai", schema)}}
    try:
        raw = client.responses.with_raw_response.create(**kwargs)
    except openai.APITimeoutError as e:
        raise LLMFailure("timeout", str(e)) from e
    except openai.RateLimitError as e:
        if "quota" in str(e).lower():
            raise ProviderUnavailable(f"provider quota reached: {e}") from e
        raise LLMFailure("error", str(e)) from e
    except openai.BadRequestError as e:
        if re.search(r"billing[ _]hard[ _]limit", str(e), re.IGNORECASE):
            raise ProviderUnavailable(f"provider billing limit reached: {e}") from e
        raise LLMFailure("error", str(e)) from e
    except (openai.APIStatusError, openai.APIConnectionError) as e:
        raise LLMFailure("error", str(e)) from e
    response = raw.parse()
    refused = any(c.type == "refusal" for item in response.output if item.type == "message" for c in item.content)
    stop = "refusal" if refused else "max_tokens" if response.status == "incomplete" else "end_turn"
    usage = response.usage
    cached = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else 0
    return Reply(text=response.output_text, model=response.model, tokens_in=usage.input_tokens,
                 tokens_out=usage.output_tokens, tokens_cached=cached, stop_reason=stop,
                 headers=_rate_headers(raw.headers))


PROVIDERS = {"anthropic": call_anthropic, "openai": call_openai}


# ---------- the one entry point ----------

def call(*, trace_path: Path, stage: str, step: str, model: str, effort: str | None, system: str,
         messages: list[dict], max_tokens: int, budget: Budget, schema: type[BaseModel] | None = None,
         no_cache: bool = False, replay: bool = False, cache_dir: Path = CACHE, fallback: str | None = None,
         attempts: int = 2, total_timeout: float | None = None):
    """Returns (parsed schema object or text, Reply). Makes up to `attempts` tries (a typed failure is retried),
    then tries the declared fallback model if one is given, then raises LLMFailure. `total_timeout` bounds one
    streamed Anthropic attempt end to end."""
    try:
        return _call_model(trace_path=trace_path, stage=stage, step=step, model=model, effort=effort, system=system,
                           messages=messages, max_tokens=max_tokens, budget=budget, schema=schema,
                           no_cache=no_cache, replay=replay, cache_dir=cache_dir, attempts=attempts,
                           total_timeout=total_timeout)
    except LLMFailure as e:
        if not fallback or e.outcome not in ("error", "timeout"):
            raise
        note = f"declared fallback used: {model} -> {fallback} after {e.outcome}"
        trace(trace_path, stage=stage, step=step, decider="code", model=fallback, outcome="retry", note=note)
        record_fallback(trace_path.parent, note)
        return _call_model(trace_path=trace_path, stage=stage, step=step, model=fallback, effort=effort, system=system,
                           messages=messages, max_tokens=max_tokens, budget=budget, schema=schema,
                           no_cache=no_cache, replay=replay, cache_dir=cache_dir, attempts=attempts,
                           total_timeout=total_timeout)


def _call_model(*, trace_path, stage, step, model, effort, system, messages, max_tokens, budget, schema,
                no_cache, replay, cache_dir, attempts, total_timeout):
    provider = config.models()[model]["provider"]
    params = {"effort": effort, "max_tokens": max_tokens,
              "schema": json_schema_for(provider, schema) if schema else None}
    keys = [cache_key(provider, model, system, messages, params, attempt) for attempt in range(attempts)]
    last, pending = LLMFailure("error", "no attempt made"), []
    for key in keys:
        cached = None if no_cache else cache_read(key, cache_dir)
        if cached is None:
            pending.append(key)
        elif cached.failure:
            last = LLMFailure(cached.failure, cached.stop_reason, raw=cached.text)
            trace(trace_path, stage=stage, step=step, decider="model", model=model, effort=effort,
                  tokens_in=cached.tokens_in, tokens_out=cached.tokens_out, cache_hit=True, outcome=cached.failure,
                  note="recorded failed attempt")
        else:
            result = _parse(cached, schema)
            trace(trace_path, stage=stage, step=step, decider="model", model=model, effort=effort,
                  tokens_in=cached.tokens_in, tokens_out=cached.tokens_out, cache_hit=True, outcome="ok")
            return result, cached
    if not pending:
        raise last
    if replay:
        raise ReplayMiss(f"--replay: no cached response for {stage}/{step} (key {pending[0][:12]})")
    for key in pending:
        worst = worst_case_usd(model, estimate_tokens_in(system, messages), max_tokens)
        budget.reserve(worst)
        started = time.monotonic()
        try:
            reply = PROVIDERS[provider](model, system, messages, effort, schema, max_tokens, total_timeout)
        except BaseException as e:
            # Whatever ends a call early is settled here, so no hold outlives it, and charged what it streamed. Only
            # a typed failure or an error from the provider boundary is retried; a stop that must propagate (the
            # provider refusing the account, an interrupt) and a bug in our own code are traced, then re-raised.
            retry = isinstance(e, LLMFailure) or (isinstance(e, Exception) and provider_error(e))
            failure = e if isinstance(e, LLMFailure) else _failure(
                "blocked" if isinstance(e, ProviderUnavailable) else "error", e)
            cost = usd(model, failure.tokens_in, failure.tokens_out)
            budget.charge(cost, worst)
            note = str(e) if retry or not isinstance(e, Exception) else f"{type(e).__name__}, not a provider error: {e}"
            trace(trace_path, stage=stage, step=step, decider="model", model=model, effort=effort,
                  tokens_in=failure.tokens_in, tokens_out=failure.tokens_out, usd=round(cost, 6),
                  outcome=failure.outcome, note=note[:200])
            if not retry:
                raise
            last = failure
            continue
        cost = usd(model, reply.tokens_in, reply.tokens_out, reply.tokens_cached)
        budget.charge(cost, worst)
        outcome, result = _check(reply, schema)
        trace(trace_path, stage=stage, step=step, decider="model", model=model, effort=effort,
              tokens_in=reply.tokens_in, tokens_out=reply.tokens_out, tokens_cached=reply.tokens_cached,
              usd=round(cost, 6), outcome=outcome,
              note=f"{time.monotonic() - started:.1f}s" + ("" if outcome == "ok" else f" stop={reply.stop_reason}"))
        if outcome == "ok":
            cache_write(key, reply, cache_dir)
            return result, reply
        # The model answered but the answer failed: record it, so a replay (or a rerun) takes the same path
        # to the next attempt or the caller's own retry without paying for this one again.
        reply.failure = outcome
        cache_write(key, reply, cache_dir)
        last = LLMFailure(outcome, reply.stop_reason, raw=reply.text)
    raise last


def provider_error(e: Exception) -> bool:
    """An error from the provider boundary that another attempt may cure: an SDK error, an HTTP transport or
    decoding error, or data the SDK couldn't read (a ValueError, as in _check). Anything else is a bug in our code,
    which no retry fixes."""
    import anthropic
    import httpx2
    import openai
    return isinstance(e, (anthropic.APIError, openai.APIError, httpx2.HTTPError, ValueError))


def _check(reply: Reply, schema: type[BaseModel] | None):
    if reply.stop_reason in ("max_tokens", "refusal"):
        return reply.stop_reason, None
    try:
        return "ok", _parse(reply, schema)
    except (ValidationError, ValueError):
        return "schema_fail", None


def _parse(reply: Reply, schema: type[BaseModel] | None):
    return schema.model_validate_json(reply.text) if schema else reply.text


def without_refused_images(images: list, attempt) -> tuple[object, list]:
    """Calls attempt(images); on a refusal, bisects the set and drops the images that trigger it."""
    try:
        return attempt(images), []
    except LLMFailure as e:
        if e.outcome != "refusal" or not images:
            raise
        if len(images) == 1:
            return None, images
    half = len(images) // 2
    _, bad_left = without_refused_images(images[:half], attempt)
    _, bad_right = without_refused_images(images[half:], attempt)
    skipped = bad_left + bad_right
    kept = [img for img in images if not any(img is s for s in skipped)]
    return attempt(kept), skipped
