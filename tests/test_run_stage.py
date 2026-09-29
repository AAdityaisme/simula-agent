"""The stage wrapper: a stage refuses to run on an upstream stage that isn't done or while a loader could read a
file git doesn't track, and every way a stage can exit still leaves failure.json and a trace line."""

import importlib
import os
import subprocess
import sys

import anthropic
import httpx2
import pytest

from simula import cli, llm, runfolder, runlog
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


def test_a_stage_runs_once_its_upstream_is_done(runs, mock_stage):
    run_dir = seeded_run(runs)
    assert len(mock_stage[1]) == 1 and (run_dir / "mock" / "done.json").exists()


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


@pytest.mark.parametrize("behavior, reason", [(refused_by_the_budget, "mock: next call could cost $5.00"),
                                              (planned_away, "batches 2-3 don't fit the $1.00 mock cap")],
                         ids=["refused_call", "planned_batches"])
def test_a_stage_its_cap_cut_short_is_partial_and_says_how_to_continue(runs, monkeypatch, quiet, capsys, behavior,
                                                                      reason):
    mock_that(monkeypatch, behavior)
    cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}", "--usd-cap", "1"])
    run_dir = latest(runs)
    marker = runfolder.read_done(run_dir / "mock")
    assert marker.outcome.status == "partial"
    assert any(r.startswith(reason) for r in marker.outcome.reasons), marker.outcome.reasons
    assert marker.outcome.resume == (f"simula mock janitorai --run {run_dir.name} --profile real --budget transfer "
                                     "--allow-fixtures --usd-cap <higher>"), "it reruns the run as it was opened"
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


def test_the_manifest_never_lists_a_stage_whose_marker_failed_to_write(runs, mock_stage, monkeypatch):
    run_dir = seeded_run(runs)
    assert "mock" in read_manifest(run_dir).stages_done
    writes = runfolder.write_done

    def unhashable(*args, **kwargs):
        raise OSError("an output can't be read")
    monkeypatch.setattr(runfolder, "write_done", unhashable)
    with pytest.raises(OSError):
        cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures"])
    assert not (run_dir / "mock" / "done.json").exists() and "mock" not in read_manifest(run_dir).stages_done
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


def test_a_stage_that_cannot_replay_keeps_its_committed_marker(runs, monkeypatch, quiet):
    def drives_the_device(ctx):
        if ctx.replay:  # as explore does: --replay reuses a finished explore/ folder
            raise llm.ReplayMiss("explore drives the device; --replay reuses a finished explore/ folder")
    mock_that(monkeypatch, drives_the_device)
    run_dir = seeded_run(runs)
    committed = (run_dir / "mock" / "done.json").read_bytes()
    assert cli.main(["mock", "janitorai", "--run", run_dir.name, "--allow-fixtures", "--replay"]) == cli.EXIT_CAP
    assert (run_dir / "mock" / "done.json").read_bytes() == committed, "the committed record survives"
    assert "ReplayMiss" in (run_dir / "mock" / "failure.json").read_text()
    assert cli.upstream_problem(run_dir, "qa").startswith("mock failed after it last finished")


def test_simula_runs_check_reruns_a_stage_whose_marker_is_damaged(runs, mock_stage):
    run_dir = seeded_run(runs)
    (run_dir / "mock" / "done.json").write_text("{not json")
    assert rerun(run_dir) and len(mock_stage[1]) == 2
    assert runfolder.read_done(run_dir / "mock").outcome.status == "complete"


def test_os_metadata_in_a_loader_folder_is_neither_hashed_nor_blocking(runs, checkout, mock_stage):
    (checkout / ".gitignore").write_text(".DS_Store\n")
    (checkout / "bible" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (checkout / "bible" / ".obsidian").mkdir()
    (checkout / "bible" / ".obsidian" / "app.json").write_text("{}")
    assert runfolder.expand([checkout / "bible"]) == [checkout / "bible" / "BIBLE.md"]
    assert cli.main(["mock", "janitorai", "--allow-fixtures", "--fixture", f"model={GOLDEN}"]) == 0
