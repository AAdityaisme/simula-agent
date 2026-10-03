"""A live mock that lost batches: its --replay rebuilds the same page, a replay that misses leaves the committed record
whole, and the stage reports the lost batches as a partial outcome in done.json."""

import re
import shutil
import time

import anthropic
import httpx2
import pytest

from simula import cli, config, llm, runfolder, runlog
from simula.contracts import ContractReport, ProductModel
from simula.stages import mock
from simula.stages.flows.deck import unfinished_stages
from tests.conftest import FIXTURES
from tests.mock_fake import golden, seed_model
from tests.test_mock_batches import drawn, provider_drawing, screens_of, with_cache_in
from tests.test_mock_fonts import fake_google
from tests.test_mock_isolation import ctx_for

APP = "janitorai"
GOLDEN = FIXTURES / "golden" / APP


@pytest.fixture(autouse=True)
def two_batches(monkeypatch):
    monkeypatch.setattr(mock, "BATCH_SCREENS", 2)


@pytest.fixture(autouse=True)
def no_desktop_notice(monkeypatch):
    monkeypatch.setattr(runlog, "notify", lambda title, message: True)


def losing_first_batch(model, kind: str):
    """A provider that draws every batch but loses the first one's call: a timeout, or a 5xx."""
    first = mock.batches(mock.pick_scope(model))[0][0].id

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        if first in screens_of(messages):
            if kind == "timeout":
                raise llm.LLMFailure("timeout", "stream idle 60 s")
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.InternalServerError("overloaded", response=httpx2.Response(529, request=request), body=None)
        return drawn(model, model_id, messages)
    return provider


def running_out_of_tokens_first(model):
    """A provider whose first answer to every batch runs out of tokens, batch 1's before the others', and whose retry
    answers at once."""
    first = mock.batches(mock.pick_scope(model))[0][0].id

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        if any(p.get("text") == mock.SHORTER for p in messages[0]["content"]):
            return drawn(model, model_id, messages, tokens_out=1000)
        time.sleep(0.2 if first in screens_of(messages) else 0.4)
        return drawn(model, model_id, messages, tokens_out=128000, stop="max_tokens")
    return provider


@pytest.mark.parametrize("kind", ["timeout", "provider_5xx", "retry_turned_away"])
def test_a_live_run_that_lost_a_batch_replays_to_the_same_page(tmp_path, monkeypatch, kind):
    """The three ways a batch is lost live: its call times out, the provider fails it, or the $ check turns its
    retry away. None leaves an answer in the cache, yet the replay takes the same path to the same placeholders."""
    run_dir = seed_model(tmp_path / "run", APP)
    with_cache_in(tmp_path, monkeypatch)
    model = golden(APP)
    ctx = ctx_for(run_dir, APP)
    if kind == "retry_turned_away":
        # Every call holds $1; a first answer costs $1 and a retry $0.50. Two at a time under a $3 cap, batches 1 and 2
        # hold with one $1 spare, batch 1's retry takes it, and batch 2's retry, even once the calls in flight settle,
        # is turned away: batch 2 is left out, and every later one.
        monkeypatch.setattr(llm, "worst_case_usd", lambda model_id, tokens_in, tokens_out: 1.0)
        monkeypatch.setattr(llm, "usd", lambda model_id, tokens_in, tokens_out, tokens_cached=0, tokens_cache_write=0:
                            1.0 if tokens_out >= 100000 else 0.5)
        monkeypatch.setattr(mock, "PARALLEL_BATCHES", 2)
        ctx.usd_cap = 3.0
        provider = running_out_of_tokens_first(model)
    else:
        provider = losing_first_batch(model, kind)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    mock.run(ctx)
    live = (run_dir / "mock" / "index.html").read_text()
    assert "screen not drawn" in live
    if kind == "retry_turned_away":
        second = mock.batches(mock.pick_scope(model))[1]
        report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
        assert next(e.detail for e in report.errors if e.screen == second[0].id).startswith(
            "screen not drawn: $ cap reached: over budget: batches 2-")

    replay = ctx_for(run_dir, APP)
    replay.replay = True
    mock.run(replay)
    assert (run_dir / "mock" / "index.html").read_text() == live


@pytest.mark.parametrize("gap", ["no_plan_record", "cache_moved_aside"])
def test_a_replay_that_misses_leaves_the_committed_record_whole(runs, tmp_path, monkeypatch, gap):
    """A replay that can't rebuild the mock keeps the run's done.json, so every file that marker records must still
    be there: a replay never clears what the live run built."""
    with_cache_in(tmp_path, monkeypatch)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(APP), []))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    mock_dir = run_dir / "mock"
    committed = (mock_dir / "done.json").read_bytes()
    if gap == "no_plan_record":  # a run built before plans were recorded
        (mock_dir / "plan.json").rename(tmp_path / "plan-moved-aside.json")
    else:  # a machine whose cache lacks the run's answers
        (tmp_path / "cache").rename(tmp_path / "cache-moved-aside")

    assert cli.main(["mock", APP, "--run", run_dir.name, "--allow-fixtures", "--replay"]) == cli.EXIT_CAP
    assert (mock_dir / "done.json").read_bytes() == committed
    moved = {"mock/plan.json"} if gap == "no_plan_record" else set()
    missing = [h.path for h in runfolder.read_done(mock_dir).output_hashes
               if h.path not in moved and not (run_dir / h.path).exists()]
    assert missing == []


PLAIN = re.compile(r"\bs\d{2}\b|\$|stream idle|refusal|max_tokens")  # ids, cost, and the provider's words


def losing_the_first_batch_to(model, loss: str, calls: list):
    """A provider that draws every batch but the first: that one's call times out once, is refused, is cut off at
    max_tokens on the retry too, or answers with no page. Any other `loss` draws it too."""
    first = mock.batches(mock.pick_scope(model))[0][0].id
    timed_out = []

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model_id)
        if first not in screens_of(messages):
            return drawn(model, model_id, messages)
        if loss == "timeout_once" and not timed_out:
            timed_out.append(first)
            raise llm.LLMFailure("timeout", "stream idle 60 s")
        if loss == "refusal":
            return drawn(model, model_id, messages, stop="refusal")
        if loss == "max_tokens_twice":
            return drawn(model, model_id, messages, tokens_out=128000, stop="max_tokens")
        if loss == "no_page":
            return llm.Reply(text="I can't draw these screens.", model=model_id, tokens_in=5000, tokens_out=10)
        return drawn(model, model_id, messages)
    return provider


def test_a_batch_lost_to_a_timeout_makes_the_mock_partial_and_its_printed_resume_draws_it(runs, tmp_path,
                                                                                          monkeypatch):
    with_cache_in(tmp_path, monkeypatch)
    model = golden(APP)
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", losing_the_first_batch_to(model, "timeout_once", calls))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    first = mock.batches(mock.pick_scope(model))[0]
    outcome = runfolder.read_done(run_dir / "mock").outcome
    assert outcome.status == "partial"
    assert outcome.reasons == [mock.not_drawn(first, "the model call timed out")]
    assert not any(PLAIN.search(line) for line in unfinished_stages(run_dir))

    command = outcome.resume.split()
    assert command[:3] == ["simula", "mock", APP]
    assert cli.main(command[1:]) == 0
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"
    assert "screen not drawn" not in (run_dir / "mock" / "index.html").read_text()


@pytest.mark.parametrize("loss", ["refusal", "max_tokens_twice", "no_page"])
def test_a_batch_lost_to_a_known_failure_is_a_contract_error_qa_repairs_not_a_partial_mock(runs, tmp_path,
                                                                                         monkeypatch, loss):
    """A rerun replays a known failure for free, so a resume couldn't draw it: the mock is complete, and its screens
    are undrawn_screen contract errors, which QA round 1 repairs like any other."""
    with_cache_in(tmp_path, monkeypatch)
    model = golden(APP)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", losing_the_first_batch_to(model, loss, []))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    first = mock.batches(mock.pick_scope(model))[0]
    assert [e.screen for e in report.errors if e.kind == "undrawn_screen"] == [s.id for s in first]
    assert not (run_dir / "needs-human.md").exists()


def test_batches_the_cap_left_out_are_one_plain_reason_and_resume_with_a_higher_cap(runs, tmp_path, monkeypatch):
    """The stage's outcome makes it partial, and the deck cover prints it, so it names the screens and says why in the
    product team's words: the budget's own refusal, with its dollar figures, stays in the trace."""
    with_cache_in(tmp_path, monkeypatch)
    model = golden(APP)
    monkeypatch.setattr(llm, "worst_case_usd", lambda model_id, tokens_in, tokens_out: 1.0)
    monkeypatch.setattr(llm, "usd", lambda model_id, tokens_in, tokens_out, tokens_cached=0, tokens_cache_write=0: 1.0)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(model, []))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "2"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    left_out = [s for batch in mock.batches(mock.pick_scope(model))[1:] for s in batch]
    outcome = runfolder.read_done(run_dir / "mock").outcome
    assert (outcome.status, outcome.reasons) == ("partial", [mock.not_drawn(left_out, mock.OVER_BUDGET)])
    assert float(outcome.resume.split(" --usd-cap ")[1]) > config.stage_cap("mock"), "spent plus a whole cap again"
    assert not any(PLAIN.search(line) for line in unfinished_stages(run_dir))


def test_a_replay_that_misses_a_font_record_keeps_the_font_files_its_record_names(runs, tmp_path, monkeypatch):
    fixture = tmp_path / "model-with-fonts"
    shutil.copytree(GOLDEN, fixture)
    model = ProductModel.model_validate_json((fixture / "product_model.json").read_text())
    states = [s.model_copy(update={"elements": [e.model_copy(update={"font_guess": "Roboto"}) if e.in_mock else e
                                                for e in s.elements]}) for s in model.states]
    (fixture / "product_model.json").write_text(model.model_copy(update={"states": states}).model_dump_json())
    (fixture / "done.json").unlink(missing_ok=True)
    with_cache_in(tmp_path, monkeypatch)
    monkeypatch.setattr(mock, "fetch", fake_google([], apache={"roboto"}))
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(APP), []))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={fixture}"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    mock_dir = run_dir / "mock"
    committed = (mock_dir / "done.json").read_bytes()
    assert any(p.suffix == ".woff2" for p in (mock_dir / "assets" / "fonts").iterdir())
    record = next(p for p in (mock_dir / mock.FONT_RECORDS).glob("*.json") if ".woff2" in p.read_text()[:300])
    record.rename(tmp_path / "record-moved-aside.json")

    assert cli.main(["mock", APP, "--run", run_dir.name, "--allow-fixtures", "--replay"]) == cli.EXIT_CAP
    assert (mock_dir / "done.json").read_bytes() == committed
    moved = f"mock/{mock.FONT_RECORDS}/{record.name}"
    assert [h.path for h in runfolder.read_done(mock_dir).output_hashes
            if h.path != moved and not (run_dir / h.path).exists()] == []


@pytest.mark.parametrize("first_batch", ["drawn", "refused"])
def test_a_rerun_that_draws_the_same_page_rewrites_every_mock_file_byte_for_byte(runs, tmp_path, monkeypatch,
                                                                                first_batch):
    """The stages below hash all of mock/, so a rerun whose calls all come from the cache must change nothing there,
    mock/plan.json included, or they would rerun for nothing."""
    with_cache_in(tmp_path, monkeypatch)
    model = golden(APP)
    loss = "refusal" if first_batch == "refused" else "none"
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", losing_the_first_batch_to(model, loss, []))
    assert cli.main(["mock", APP, "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    run_dir = (runs / APP / "latest").resolve()
    before = runfolder.read_done(run_dir / "mock").output_hashes
    assert cli.main(["mock", APP, "--run", run_dir.name, "--allow-fixtures"]) == 0
    assert runfolder.read_done(run_dir / "mock").output_hashes == before
