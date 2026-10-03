"""The stage wrapper: a stage refuses to run on an upstream stage that isn't done or while a loader could read a
file git doesn't track, and every way a stage can exit still leaves failure.json and a trace line."""

import importlib
import os
import shlex
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from simula import cli, config, llm, runfolder, runlog
from simula.config import STAGES
from simula.contracts import StageOutcome
from simula.runlog import read_manifest, read_trace
from tests.conftest import FIXTURES

GOLDEN = FIXTURES / "golden" / "janitorai"


def latest(runs):
    return (runs / "janitorai" / "latest").resolve()


@pytest.fixture
def mock_stage(monkeypatch):
    calls = []
    stage = importlib.import_module("simula.stages.mock")
    monkeypatch.setattr(stage, "run", lambda ctx: calls.append(ctx))
    return stage, calls


def seeded_run(runs):
    """A run whose model stage is a finished fixture seed, and whose mock stage has run once."""
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"])
    return latest(runs)


def test_a_stage_refuses_an_upstream_that_never_ran(runs, mock_stage):
    _, calls = mock_stage
    with pytest.raises(SystemExit, match="mock can't run: model is not done; run `simula model` first"):
        cli.main(["run", "janitorai", "--new", "--from", "mock"])
    last = read_trace(latest(runs) / "trace.jsonl")[-1]
    assert (last.stage, last.step, last.outcome) == ("mock", "upstream", "blocked")
    assert calls == [] and not (latest(runs) / "mock" / "failure.json").exists()


def test_a_stage_refuses_an_upstream_that_failed_after_it_finished(runs, mock_stage):
    run_dir = seeded_run(runs)
    failure = run_dir / "model" / "failure.json"
    failure.write_text('{"reason": "rerun crashed"}')
    later = (run_dir / "model" / "done.json").stat().st_mtime + 5
    os.utime(failure, (later, later))
    with pytest.raises(SystemExit, match="model failed after it last finished"):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    assert len(mock_stage[1]) == 1



def test_a_damaged_upstream_marker_blocks_the_stage_below_with_a_trace_line(runs, mock_stage):
    run_dir = seeded_run(runs)
    (run_dir / "model" / "done.json").write_text("{not json")
    with pytest.raises(SystemExit, match="mock can't run: model is not done; run `simula model` first"):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    assert any(line.step == "marker" and line.stage == "model" for line in read_trace(run_dir / "trace.jsonl"))
    assert len(mock_stage[1]) == 1

def test_a_stage_runs_once_its_upstream_is_done(runs, mock_stage):
    run_dir = seeded_run(runs)
    assert len(mock_stage[1]) == 1 and (run_dir / "mock" / "done.json").exists()


@pytest.mark.parametrize("platform, held", [("darwin", [["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]]),
                                            ("linux", [])])
def test_a_run_holds_off_idle_sleep_on_a_mac_once_per_process(runs, mock_stage, monkeypatch, platform, held):
    """An idle Mac that sleeps mid-run stalls every model call in flight until it wakes."""
    started = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(cli, "subprocess", SimpleNamespace(Popen=started.append))
    cli.stay_awake.cache_clear()
    run_dir = seeded_run(runs)
    cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    cli.stay_awake.cache_clear()
    assert started == held


@pytest.mark.parametrize("exit_with", [lambda: sys.exit("no Android device online"),
                                       lambda: (_ for _ in ()).throw(KeyboardInterrupt())],
                         ids=["system_exit", "ctrl_c"])
def test_every_exit_leaves_a_failure_record(runs, mock_stage, monkeypatch, exit_with):
    stage, _ = mock_stage
    run_dir = seeded_run(runs)
    monkeypatch.setattr(stage, "run", lambda ctx: exit_with())
    with pytest.raises((SystemExit, KeyboardInterrupt)):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    reason = (run_dir / "mock" / "failure.json").read_text()
    assert "SystemExit: no Android device online" in reason or "KeyboardInterrupt" in reason
    assert not (run_dir / "mock" / "done.json").exists()
    last = read_trace(run_dir / "trace.jsonl")[-1]
    assert (last.stage, last.step, last.outcome) == ("mock", "run", "error")


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A git checkout with one tracked file in each folder a loader reads by glob, standing in for ROOT."""
    root = tmp_path / "checkout"
    for rel in ("prompts/mock/builder.md", "config/apps/janitorai.toml", "bible/BIBLE.md", "docs/CONTRACTS.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("tracked\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr("simula.checkout.ROOT", root)
    return root


def test_an_untracked_file_a_loader_reads_blocks_the_run_before_any_work(runs, checkout):
    (checkout / ".gitignore").write_text("*2.md\n")
    for rel in ("prompts/mock/builder 2.md", "bible/data/extra.json", "prompts/mock/notes.txt",
                "bible/__pycache__/breakeven.pyc"):
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        (checkout / rel).write_text("stray\n")
    with pytest.raises(SystemExit) as refused:
        cli.main(["run", "janitorai", "--new"])
    message = str(refused.value)
    assert "  bible/data/extra.json\n  prompts/mock/builder 2.md\n" in message, "an ignored copy still blocks"
    assert "notes.txt" not in message and "pycache" not in message, "no loader picks those up"
    assert not (runs / "janitorai").exists()


def test_a_modified_tracked_prompt_does_not_block(runs, checkout, mock_stage):
    (checkout / "prompts" / "mock" / "builder.md").write_text("edited while developing\n")
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    assert len(mock_stage[1]) == 1
    assert not any(line.step == "preflight" for line in read_trace(latest(runs) / "trace.jsonl"))


def test_without_a_git_checkout_the_check_is_skipped_with_a_trace_note(runs, tmp_path, monkeypatch, mock_stage):
    unzipped = tmp_path / "unzipped"
    (unzipped / "prompts" / "mock").mkdir(parents=True)
    (unzipped / "prompts" / "mock" / "builder 2.md").write_text("never checked\n")
    monkeypatch.setattr(cli, "ROOT", unzipped)
    monkeypatch.setattr("simula.checkout.ROOT", unzipped)
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
    [note] = [line for line in read_trace(latest(runs) / "trace.jsonl") if line.step == "preflight"]
    assert (note.stage, note.outcome) == ("run", "ok") and note.note.startswith("not a git checkout: skipped")


# ---------- what a finished stage ran, and whether it delivered all of its work ----------

@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(runlog, "notify", lambda title, message: True)


def mock_that(monkeypatch, behavior):
    """The mock stage replaced by `behavior(ctx)`, which returns what the stage returns, and then ships a page."""
    def run(ctx):
        result = behavior(ctx)
        (ctx.run_dir / "mock" / "index.html").write_text("<html>")
        return result
    monkeypatch.setattr(importlib.import_module("simula.stages.mock"), "run", run)


def refused_by_the_budget(ctx):
    budget = llm.Budget.for_stage("mock", ctx.run_dir / "trace.jsonl", ctx.usd_cap)
    try:
        budget.reserve(5.0)
    except llm.CapReached:
        pass  # the stage ships what it has, as QA does when its cap stops the rounds


def planned_away(ctx):
    budget = llm.Budget.for_stage("mock", ctx.run_dir / "trace.jsonl", ctx.usd_cap)
    if budget.cap < 5.0:  # as the mock's batch planner does with batches that don't fit
        runlog.run_trace(ctx.run_dir, stage="mock", step="plan", decider="code", outcome="cap",
                         note=f"batches 2-3 don't fit the ${budget.cap:.2f} mock cap at worst case")


def rerun(run_dir, *flags):
    """`simula run`'s check of the mock: True when it ran again, False when it skipped on matching hashes."""
    ctx = cli.open_run(cli.parser().parse_args(["run", "janitorai", "--run", run_dir.name, "--allow-fixtures", *flags]))
    assert cli.run_stage("mock", ctx, force=False)
    return read_trace(run_dir / "trace.jsonl")[-1].step != "skip"


@pytest.mark.parametrize("behavior, reason, turned_away", [
    (refused_by_the_budget, "mock: next call could cost $5.00", 5.0),
    (planned_away, "batches 2-3 don't fit the $1.00 mock cap", 0.0)], ids=["refused_call", "planned_batches"])
def test_a_stage_its_cap_cut_short_is_partial_and_says_how_to_continue(runs, monkeypatch, quiet, capsys, behavior,
                                                                      reason, turned_away):
    mock_that(monkeypatch, behavior)
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "1"])
    run_dir = latest(runs)
    marker = runfolder.read_done(run_dir / "mock")
    assert marker.outcome.status == "partial"
    assert any(r.startswith(reason) for r in marker.outcome.reasons), marker.outcome.reasons
    # the figure the stop names, or, naming none, what is spent plus a whole cap again (the configured one: 1 is lower)
    figure = config.stage_cap("mock") + turned_away
    assert marker.outcome.resume == (f"simula mock janitorai --run {run_dir.name} --profile real --budget transfer "
                                     f"--allow-fixtures --usd-cap {figure:.2f}"), "it reruns the run as it was opened"
    asked = (run_dir / "needs-human.md").read_text()
    assert "partial output" in asked and marker.outcome.resume in asked
    assert "mock: done (partial" in capsys.readouterr().out
    assert cli.upstream_problem(run_dir, "qa") is None, "a partial mock still feeds QA"


def test_a_capped_stage_reruns_until_a_higher_cap_lets_it_finish(runs, monkeypatch, quiet):
    mock_that(monkeypatch, refused_by_the_budget)
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "1"])
    run_dir = latest(runs)
    assert rerun(run_dir, "--usd-cap", "1"), "the same cap: still capped, so never counted done"
    assert "mock" not in read_manifest(run_dir).stages_done
    assert rerun(run_dir, "--usd-cap", "10")
    marker = runfolder.read_done(run_dir / "mock")
    assert marker.outcome.status == "complete", "only this run's refusals count"
    assert "mock" in read_manifest(run_dir).stages_done
    assert not rerun(run_dir, "--usd-cap", "10")
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", "--usd-cap", "10"]) == 0
    headers = [h for h in (run_dir / "needs-human.md").read_text().splitlines() if h.startswith("## ")]
    assert [h.endswith("· mock · resolved") for h in headers] == [False, False, True], "resolved once, then left alone"


def test_a_cap_that_never_bound_leaves_the_stage_done_under_any_cap(runs, monkeypatch):
    mock_that(monkeypatch, refused_by_the_budget)
    run_dir = seeded_run(runs)
    marker = runfolder.read_done(run_dir / "mock")
    assert marker.outcome.status == "complete"
    assert not rerun(run_dir, "--usd-cap", "20") and not rerun(run_dir)
    assert not (run_dir / "needs-human.md").exists()


def test_a_stage_that_reports_partial_work_is_labeled_and_reruns_until_it_completes(runs, monkeypatch, quiet):
    reports = [StageOutcome(status="partial", reasons=["2 of 24 taps failed"]), None]
    mock_that(monkeypatch, lambda ctx: reports.pop(0))
    run_dir = seeded_run(runs)
    marker = runfolder.read_done(run_dir / "mock")
    assert (marker.outcome.status, marker.outcome.reasons) == ("partial", ["2 of 24 taps failed"])
    assert marker.outcome.resume == (f"simula mock janitorai --run {run_dir.name} --profile real --budget transfer "
                                     "--allow-fixtures")
    assert "2 of 24 taps failed" in (run_dir / "needs-human.md").read_text()
    assert rerun(run_dir) and runfolder.read_done(run_dir / "mock").outcome.status == "complete"
    assert not rerun(run_dir)


def test_a_stage_that_failed_and_then_completes_resolves_what_it_asked_for(runs, monkeypatch, quiet):
    outcomes = [llm.CapReached("mock: next call could cost $5.00, raise with --usd-cap"), None]

    def fail_once(ctx):
        if outcome := outcomes.pop(0):
            raise outcome
    mock_that(monkeypatch, fail_once)
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == cli.EXIT_CAP
    run_dir = latest(runs)
    assert rerun(run_dir)
    asked = (run_dir / "needs-human.md").read_text()
    assert "$ cap reached" in asked and asked.rstrip().splitlines()[-1].startswith("**Done:** mock finished complete")


def test_a_stage_that_asks_for_a_person_as_it_finishes_keeps_the_request_open(runs, monkeypatch, quiet):
    asks = [True, False]

    def gaps_then_clean(ctx):
        if asks.pop(0):  # as the model does when its product model still has gaps after the retry
            runlog.needs_human(ctx.run_dir, "mock", "the page has gaps", "2 screens undrawn", ["mock/"], "look")
    mock_that(monkeypatch, gaps_then_clean)
    run_dir = seeded_run(runs)
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"
    assert "resolved" not in (run_dir / "needs-human.md").read_text(), "the request it just made still stands"
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"]) == 0
    assert (run_dir / "needs-human.md").read_text().rstrip().endswith("need nothing more.")


def test_a_record_that_fails_after_the_stage_ran_leaves_no_marker_so_the_next_run_repairs_it(runs, monkeypatch,
                                                                                           quiet):
    mock_that(monkeypatch, refused_by_the_budget)
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "1"])
    run_dir = latest(runs)

    def unwritable(run_dir, stage):
        raise PermissionError("needs-human.md is read-only")
    writable = runlog.resolve_needs_human
    monkeypatch.setattr(runlog, "resolve_needs_human", unwritable)
    with pytest.raises(PermissionError):
        rerun(run_dir, "--usd-cap", "10")
    assert not (run_dir / "mock" / "done.json").exists() and "mock" not in read_manifest(run_dir).stages_done
    monkeypatch.setattr(runlog, "resolve_needs_human", writable)
    assert rerun(run_dir, "--usd-cap", "10")
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"
    assert "mock" in read_manifest(run_dir).stages_done


@pytest.mark.parametrize("replay", [[], ["--replay"]], ids=["live", "replay"])
def test_the_manifest_never_lists_a_stage_whose_marker_failed_to_write(runs, mock_stage, monkeypatch, replay):
    run_dir = seeded_run(runs)
    assert "mock" in read_manifest(run_dir).stages_done
    writes = runfolder.write_done

    def unhashable(*args, **kwargs):
        raise OSError("an output can't be read")
    monkeypatch.setattr(runfolder, "write_done", unhashable)
    committed = (run_dir / "mock" / "done.json").read_bytes()
    with pytest.raises(OSError):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", *replay])
    assert "mock" not in read_manifest(run_dir).stages_done and not runlog.complete(run_dir, "mock")
    assert "an output can't be read" in (run_dir / "mock" / "failure.json").read_text()
    if replay:  # the committed record goes back, beside the newer failure
        assert (run_dir / "mock" / "done.json").read_bytes() == committed
    else:
        assert not (run_dir / "mock" / "done.json").exists()
    monkeypatch.setattr(runfolder, "write_done", writes)
    assert rerun(run_dir) and "mock" in read_manifest(run_dir).stages_done


def test_the_next_command_heals_a_manifest_that_drifted_from_the_markers(runs, mock_stage):
    run_dir = seeded_run(runs)
    runlog.update_manifest(run_dir, stages_done=["explore", "qa"], usd_total=99.0)
    cli.open_run(cli.parser().parse_args(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"]))
    manifest = read_manifest(run_dir)
    assert (manifest.stages_done, manifest.usd_total) == (["model", "mock"], 0.0)


def test_a_damaged_marker_counts_as_not_done_and_its_stage_can_still_rewrite_it(runs, mock_stage):
    run_dir = seeded_run(runs)
    (run_dir / "mock" / "done.json").write_text("{not json")
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"]) == 0
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"
    assert "mock" in read_manifest(run_dir).stages_done
    flagged = [line for line in read_trace(run_dir / "trace.jsonl") if line.step == "marker"]
    assert flagged and all((f.stage, f.outcome) == ("mock", "error") and "can't be read" in f.note for f in flagged)


def test_the_chain_reruns_a_stage_whose_code_changed(runs, mock_stage, monkeypatch, tmp_path):
    code = tmp_path / "mock.py"
    code.write_text("BATCH = 4\n")
    monkeypatch.setattr(runfolder, "code_files", lambda stage: [code])
    run_dir = seeded_run(runs)
    assert not rerun(run_dir)
    code.write_text("BATCH = 5\n")
    assert rerun(run_dir) and len(mock_stage[1]) == 2


def test_a_replay_stops_at_the_first_stage_the_run_never_finished(runs, monkeypatch):
    """An app that refuses the emulator leaves a run that stopped at explore: it replays cleanly, re-executing
    nothing, while a live run still goes on to the next stage."""
    ran = []
    for stage in STAGES:
        monkeypatch.setattr(importlib.import_module(f"simula.stages.{stage}"), "run",
                            lambda ctx, stage=stage: ran.append(stage))
    assert cli.main(["explore", "janitorai"]) == 0
    run_dir = latest(runs)
    for flags in ([], ["--from", "model"]):
        assert cli.main(["run", "janitorai", "--run", run_dir.name, "--replay", *flags]) == 0
        stop = read_trace(run_dir / "trace.jsonl")[-1]
        assert (stop.stage, stop.step) == ("model", "replay") and "never finished" in stop.note
    assert ran == ["explore"]
    assert cli.main(["run", "janitorai", "--run", run_dir.name, "--from", "model"]) == 0
    assert ran == ["explore", *STAGES[1:]]


# ---------- --replay reproduces the recorded run, partial and failed calls included ----------

HAIKU = "claude-haiku-4-5-20251001"


def model_call(ctx, step, cache):
    return llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="mock", step=step, model=HAIKU, effort=None,
                    system="", messages=[{"role": "user", "content": [{"type": "text", "text": step}]}],
                    max_tokens=64_000, budget=llm.Budget.for_stage("mock", ctx.run_dir / "trace.jsonl", ctx.usd_cap),
                    replay=ctx.replay, cache_dir=cache)


def rounds_until_capped(cache):
    """Up to three model rounds, shipping what it has when its cap stops them, as QA's fix loop does."""
    def run(ctx):
        for n in range(1, 4):
            try:
                model_call(ctx, f"round{n}", cache)
            except llm.CapReached:
                break
    return run


def answers(monkeypatch, fail_on=None):
    """The model: every call answers (40k tokens out, about $0.20 on Haiku), except the step `fail_on` fails with."""
    def provider(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        step = messages[0]["content"][0]["text"]
        if fail_on and step == fail_on[0]:
            raise fail_on[1]()
        return llm.Reply(text=f"answer to {step}", model=model, tokens_in=1000, tokens_out=40_000)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)


def no_model(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", None)


def capped_run(runs, monkeypatch, tmp_path):
    """A live mock run its $0.70 cap stopped after two of its three rounds."""
    answers(monkeypatch)
    mock_that(monkeypatch, rounds_until_capped(tmp_path / "cache"))
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "0.7"])
    run_dir = latest(runs)
    marker = runfolder.read_done(run_dir / "mock")
    assert marker.outcome.status == "partial" and marker.outcome.reasons[0].startswith("mock: next call could cost")
    return run_dir, marker


def test_simula_run_under_replay_takes_a_partial_stage_as_recorded(runs, monkeypatch, quiet, tmp_path):
    run_dir, marker = capped_run(runs, monkeypatch, tmp_path)
    no_model(monkeypatch)
    committed = (run_dir / "mock" / "done.json").read_bytes()
    assert not rerun(run_dir, "--replay"), "hashes match, so --replay skips it: only a live run can complete it"
    assert (run_dir / "mock" / "done.json").read_bytes() == committed
    answers(monkeypatch)
    assert rerun(run_dir), "a live run still reruns it until it completes"


@pytest.mark.parametrize("cap", [[], ["--usd-cap", "0.0001"], ["--usd-cap", "10"]], ids=["own", "lower", "higher"])
def test_a_forced_replay_of_a_capped_stage_stops_where_the_live_run_did(runs, monkeypatch, quiet, tmp_path, cap):
    run_dir, live = capped_run(runs, monkeypatch, tmp_path)
    no_model(monkeypatch)
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", "--replay", *cap]) == 0
    replayed = runfolder.read_done(run_dir / "mock").outcome
    assert (replayed.status, replayed.reasons) == (live.outcome.status, live.outcome.reasons)
    stops = [line for line in read_trace(run_dir / "trace.jsonl") if line.outcome == "cap"]
    assert [line.step for line in stops] == ["round3", "round3"], "one keyed line per stop, live and replayed"
    assert all(llm.split_key(line.note)[0] for line in stops)



def test_a_stage_its_cap_stops_leaves_the_manifest_total_equal_to_what_it_spent(runs, monkeypatch, quiet, tmp_path):
    answers(monkeypatch)

    def spends_then_stops(ctx):  # nothing catches the cap: the stage stops there, exit 4
        for n in range(1, 4):
            model_call(ctx, f"round{n}", tmp_path / "cache")
    mock_that(monkeypatch, spends_then_stops)
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "0.7"]) \
        == cli.EXIT_CAP
    run_dir = latest(runs)
    spent = round(sum(line.usd for line in read_trace(run_dir / "trace.jsonl")), 4)
    assert spent > 0 and read_manifest(run_dir).usd_total == spent

@pytest.mark.parametrize("failure", [
    lambda: llm.LLMFailure("timeout", "stream idle 60 s"),
    lambda: anthropic.InternalServerError("overloaded", body=None, response=httpx2.Response(
        529, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")))], ids=["timeout", "5xx"])
def test_a_call_that_failed_live_fails_the_same_way_under_replay(runs, monkeypatch, quiet, tmp_path, failure):
    cache = tmp_path / "cache"

    def two_parts(ctx):  # a part whose call fails becomes a placeholder, as a failed mock batch does
        parts = []
        for step in ("part1", "part2"):
            try:
                parts.append(model_call(ctx, step, cache)[0])
            except llm.LLMFailure as e:
                parts.append(f"placeholder: {e.outcome}")
        (ctx.run_dir / "mock" / "page.txt").write_text("\n".join(parts))
    answers(monkeypatch, fail_on=("part1", failure))
    mock_that(monkeypatch, two_parts)
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"])
    run_dir = latest(runs)
    live = (run_dir / "mock" / "page.txt").read_text()
    assert live.startswith("placeholder: ") and "answer to part2" in live
    no_model(monkeypatch)
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", "--replay"]) == 0
    assert (run_dir / "mock" / "page.txt").read_text() == live


def empties_its_folder(folder):  # as flows' clean() does: the folder stays, everything in it goes
    for child in folder.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()


@pytest.mark.parametrize("clears", [None, empties_its_folder, shutil.rmtree],
                         ids=["fails_at_once", "empties_its_folder", "removes_its_folder"])
def test_a_stage_that_cannot_replay_keeps_its_committed_marker(runs, monkeypatch, quiet, clears):
    def drives_the_device(ctx):
        if ctx.replay:  # as explore does: --replay reuses a finished explore/ folder
            if clears:  # as QA does on a replay miss: rmtree(run_dir / "qa"), then ReplayMiss before it rebuilds
                clears(ctx.run_dir / "mock")
            raise llm.ReplayMiss("explore drives the device; --replay reuses a finished explore/ folder")
    mock_that(monkeypatch, drives_the_device)
    run_dir = seeded_run(runs)
    committed = (run_dir / "mock" / "done.json").read_bytes()
    finished = (run_dir / "mock" / "done.json").stat().st_mtime_ns
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", "--replay"]) == cli.EXIT_CAP
    assert (run_dir / "mock" / "done.json").read_bytes() == committed, "the committed record survives"
    assert (run_dir / "mock" / "done.json").stat().st_mtime_ns == finished, "with its own time, older than the failure"
    assert "ReplayMiss" in (run_dir / "mock" / "failure.json").read_text()
    last = read_trace(run_dir / "trace.jsonl")[-1]
    assert (last.stage, last.step, last.outcome) == ("mock", "run", "error") and "ReplayMiss" in last.note
    assert cli.upstream_problem(run_dir, "qa").startswith("mock failed after it last finished")
    assert not runlog.complete(run_dir, "mock"), "beside a newer failure it counts as not done, as for the stages below"
    assert "mock" not in read_manifest(run_dir).stages_done, "the manifest agrees at once, not only at the next command"
    with pytest.raises(llm.ReplayMiss):
        rerun(run_dir, "--replay")


def damage(marker, how):
    if how == "unreadable":
        marker.chmod(0o000)
    else:
        marker.write_bytes({"not_json": b"{not json", "not_text": b"\xff\xfe\x00 done"}[how])


@pytest.mark.parametrize("replay", [[], ["--replay"]], ids=["live", "replay"])
@pytest.mark.parametrize("how", ["not_json", "not_text", "unreadable"])
def test_simula_runs_check_reruns_a_stage_whose_marker_is_damaged(runs, mock_stage, how, replay):
    run_dir = seeded_run(runs)
    damage(run_dir / "mock" / "done.json", how)
    assert rerun(run_dir, *replay) and len(mock_stage[1]) == 2
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"


def test_os_metadata_in_a_loader_folder_is_neither_hashed_nor_blocking(runs, checkout, mock_stage):
    (checkout / ".gitignore").write_text(".DS_Store\n")
    (checkout / "bible" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (checkout / "bible" / ".obsidian").mkdir()
    (checkout / "bible" / ".obsidian" / "app.json").write_text("{}")
    assert runfolder.expand([checkout / "bible"]) == [checkout / "bible" / "BIBLE.md"]
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0


# ---------- a printed resume command reopens the run with the options it ran under ----------

def reopened(command: str):
    """The run a printed command opens, read by the CLI's own parser, which stops at a `#` comment as a shell does."""
    return cli.open_run(cli.parser().parse_args(shlex.split(command, comments=True)[1:]))


@pytest.mark.parametrize("flag", [["--profile", "dev"], ["--budget", "deep"], ["--allow-account-create"],
                                  ["--usd-cap", "12.3456789"], ["--no-send"], ["--device", "emulator-5556"]],
                         ids=["profile", "budget", "account_create", "usd_cap", "no_send", "device"])
def test_a_partial_stages_resume_parses_back_to_the_options_it_ran_under(runs, monkeypatch, quiet, flag):
    reports = [StageOutcome(status="partial", reasons=["2 of 24 taps failed"])]
    mock_that(monkeypatch, lambda ctx: reports.pop(0) if reports else None)
    command = ["mock", "janitorai", "--allow-fixtures", *flag]
    cli.main([*command, "--fixture", f"model={GOLDEN}"])
    run_dir = latest(runs)
    ran = cli.open_run(cli.parser().parse_args([*command, "--run", run_dir.name]))
    again = reopened(runfolder.read_done(run_dir / "mock").outcome.resume)
    assert again.run_dir == run_dir and (again.usd_cap, again.no_send, again.device) == (ran.usd_cap, ran.no_send,
                                                                                         ran.device)
    assert {s: cli.stage_params(s, again) for s in STAGES} == {s: cli.stage_params(s, ran) for s in STAGES}


def test_explores_continue_command_parses_back_to_the_options_it_ran_under(runs):
    from simula.stages import explore
    ran = cli.open_run(cli.parser().parse_args(["explore", "janitorai", "--new", "--profile", "dev", "--budget", "deep",
                                                "--no-send", "--device", "emulator-5556", "--usd-cap", "1.5"]))
    again = reopened(explore.rerun(ran))
    assert again.run_dir == ran.run_dir and (again.no_send, again.device, again.usd_cap) == (True, "emulator-5556", 1.5)
    assert {s: cli.stage_params(s, again) for s in STAGES} == {s: cli.stage_params(s, ran) for s in STAGES}


@pytest.mark.parametrize("stop, cap", [(llm.CapReached("mock: next call could cost $5.00"), 25.0),
                                       (llm.ProviderUnavailable("429: usage limit reached"), 12.5)],
                         ids=["cap_raised", "outage_keeps_the_cap"])
def test_a_stopped_stages_command_carries_its_options_and_one_cap(runs, monkeypatch, quiet, stop, cap):
    def stops(ctx):
        raise stop
    mock_that(monkeypatch, stops)
    cli.main(["mock", "janitorai", "--allow-fixtures", "--budget", "deep", "--usd-cap", "12.5", "--fixture",
              f"model={GOLDEN}"])
    command = (latest(runs) / "needs-human.md").read_text().split("**Continue with:** `")[-1].split("`")[0]
    again = reopened(command)
    assert command.count("--usd-cap") == 1 and (again.budget, again.usd_cap) == ("deep", cap)


# ---------- calls running together reach the cap in no fixed order ----------

def two_callers_turned_away(cache, first):
    """Two threads share one Budget, as QA's critics do, and the cap turns both away; `first` reaches it first."""
    def run(ctx):
        budget = llm.Budget.for_stage("mock", ctx.run_dir / "trace.jsonl", ctx.usd_cap)
        stopped = threading.Event()

        def call(step, max_tokens):
            if step != first:
                stopped.wait(5)
            try:
                llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="mock", step=step, model=HAIKU, effort=None,
                         system="", messages=[{"role": "user", "content": [{"type": "text", "text": step}]}],
                         max_tokens=max_tokens, budget=budget, replay=ctx.replay, cache_dir=cache, attempts=1)
            except llm.CapReached:
                stopped.set()
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(call, ["a", "b"], [64_000, 32_000]))
    return run


def test_parallel_cap_stops_replay_to_the_same_reasons_in_either_order(runs, monkeypatch, quiet, tmp_path):
    no_model(monkeypatch)
    mock_that(monkeypatch, two_callers_turned_away(tmp_path / "cache", first="a"))
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "0.1"])
    run_dir = latest(runs)
    live = runfolder.read_done(run_dir / "mock").outcome
    assert live.status == "partial" and len(live.reasons) == 2
    mock_that(monkeypatch, two_callers_turned_away(tmp_path / "cache", first="b"))
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", "--replay"]) == 0
    stops = [line.step for line in read_trace(run_dir / "trace.jsonl") if line.outcome == "cap"]
    assert stops == ["a", "b", "b", "a"], "the replay reached the cap in the other order"
    assert runfolder.read_done(run_dir / "mock").outcome.reasons == live.reasons


def test_a_new_judge_revision_cap_reruns_the_judge_and_no_other_stage(runs, monkeypatch):
    ctx = cli.open_run(cli.parser().parse_args(["run", "janitorai", "--allow-fixtures"]))
    before = {s: runfolder.params_hash(cli.stage_params(s, ctx)) for s in STAGES}
    profiles = config.profiles()
    monkeypatch.setattr(config, "profiles", lambda: {**profiles, "judge_revision_cap": 2})
    after = {s: runfolder.params_hash(cli.stage_params(s, ctx)) for s in STAGES}
    assert [s for s in STAGES if before[s] != after[s]] == ["judge"]
