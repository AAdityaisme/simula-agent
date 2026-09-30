"""The judge stage over the real llm.call (a tmp cache, a scripted provider): the queue's rerun makes new calls,
--replay reproduces a lost call for free, a failed replay keeps the last judge/, and judge calls stream."""

from dataclasses import replace
from types import SimpleNamespace

import anthropic
import pytest

from simula import config, llm, runlog
from simula.contracts import DecisionsFile
from simula.stages import judge
from tests.judge_helpers import ctx_for, idea, live, seed, verdict
from tests.propose_fixtures import golden

APP = "aol"


def lost(schema):
    raise llm.LLMFailure("error", "Connection error.")


def answers(schema):
    return llm.Reply(text=verdict().model_dump_json(), model="fake")


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """The real llm.call on a cache in tmp_path, with a provider whose answer the test sets."""
    call = llm.call
    monkeypatch.setattr(llm, "call", lambda **kw: call(**{**kw, "cache_dir": tmp_path / "cache"}))
    script = SimpleNamespace(calls=0, answer=answers)

    def fake(model, system, messages, effort, schema, max_tokens, total_timeout):
        script.calls += 1
        return script.answer(schema)
    for provider_name in ("anthropic", "openai"):
        monkeypatch.setitem(llm.PROVIDERS, provider_name, fake)
    monkeypatch.setattr(runlog, "notify", lambda *a: False)
    return script


def finals(run_dir):
    return [d.final for d in DecisionsFile.model_validate_json((run_dir / "judge" / "decisions.json").read_text()).decisions]


def test_the_queues_continue_command_makes_new_calls(tmp_path, provider):
    run_dir = seed(tmp_path, APP, live(APP, {}))
    provider.answer = lost
    judge.run(ctx_for(APP, run_dir))
    queue = run_dir / "judge" / "human-queue.md"
    assert finals(run_dir) == ["needs_human"] and f"simula judge {APP} --run" in queue.read_text()
    before = provider.calls
    provider.answer = answers
    judge.run(ctx_for(APP, run_dir))
    assert provider.calls > before and finals(run_dir) == ["accept"] and not queue.exists()


def test_replay_of_a_run_with_a_lost_call_reproduces_needs_human_with_no_calls(tmp_path, provider):
    run_dir = seed(tmp_path, APP, live(APP, {}))
    provider.answer = lost
    judge.run(ctx_for(APP, run_dir))
    before = provider.calls
    provider.answer = lambda schema: pytest.fail("replay called the provider")
    judge.run(replace(ctx_for(APP, run_dir), replay=True))
    assert provider.calls == before and finals(run_dir) == ["needs_human"]


def test_a_failed_replay_leaves_the_previous_judge_folder_intact(tmp_path, provider):
    run_dir = seed(tmp_path, APP, live(APP, {}))
    judge.run(ctx_for(APP, run_dir))
    before = {p.relative_to(run_dir): p.read_bytes() for p in (run_dir / "judge").rglob("*") if p.is_file()}
    seed(tmp_path / "grown", APP, live(APP, {}, {"title": "An idea never judged"}))
    (run_dir / "propose" / "candidates.json").write_bytes(
        (tmp_path / "grown" / APP / "propose" / "candidates.json").read_bytes())
    with pytest.raises(llm.ReplayMiss):
        judge.run(replace(ctx_for(APP, run_dir), replay=True))
    after = {p.relative_to(run_dir): p.read_bytes() for p in (run_dir / "judge").rglob("*") if p.is_file()}
    assert after == before and finals(run_dir) == ["accept"]
    assert not (run_dir / "judge.tmp").exists() and not (run_dir / "judge.old").exists()


def test_a_swap_that_fails_halfway_puts_the_old_judge_folder_back(tmp_path, monkeypatch):
    out, work = tmp_path / "judge", tmp_path / "judge.tmp"
    out.mkdir()
    (out / "decisions.json").write_text("last good")
    work.mkdir()
    rename = judge.os.replace

    def second_fails(src, dst):
        if src == work:
            raise OSError("disk full")
        rename(src, dst)
    monkeypatch.setattr(judge.os, "replace", second_fails)
    with pytest.raises(OSError):
        judge.swap_in(work, out)
    assert (out / "decisions.json").read_text() == "last good" and not (tmp_path / "judge.old").exists()


class FakeStream:
    def __init__(self, text):
        self.text, self.response = text, SimpleNamespace(headers={})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], model="fake", usage=usage,
                               stop_reason="end_turn")


@pytest.mark.parametrize("profile", ["real", "dev"])
def test_a_judge_call_goes_through_the_streaming_path(profile, tmp_path, monkeypatch):
    seen = {}

    class Client:
        def __init__(self, **kw):
            seen["retries"] = kw["max_retries"]
            self.messages = SimpleNamespace(stream=self.stream, with_raw_response=SimpleNamespace(create=self.create))

        def stream(self, **kw):
            seen["max_tokens"] = kw["max_tokens"]
            return FakeStream(verdict().model_dump_json())

        def create(self, **kw):
            pytest.fail("the judge call did not stream")
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    role = config.roles(profile)["judge_1"]
    model = golden(APP)
    call = llm.call
    monkeypatch.setattr(llm, "call", lambda **kw: call(**{**kw, "cache_dir": tmp_path / "cache"}))
    v = judge.ask_judge(role, idea(model), model, trace_path=tmp_path / "trace.jsonl", stage="judge", step="t",
                        budget=llm.Budget("judge", 5.0))
    assert v.g_policy.passed and seen["retries"] == 0
    assert seen["max_tokens"] > config.models()[role["model"]]["stream_above"]
