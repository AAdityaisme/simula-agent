"""Stage 3 draws its scope in parallel batches: batching, stitching, failure isolation, cache replay, the $ cap."""

import threading

import pytest

from simula import llm
from simula.contracts import ContractReport, State
from simula.runlog import read_trace
from simula.stages import mock
from tests.conftest import APPS
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import batch_screens, ctx_for, fake_builder, without_edges


@pytest.fixture(params=APPS)
def app(request):
    return request.param


@pytest.fixture
def two_batches(monkeypatch):
    """Batches of 2, so every golden draws in at least two batches (AOL's whole scope fits one batch of 4)."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 2)


def state(sid: str, kind: str = "screen", parent: str | None = None) -> State:
    return golden("janitorai").states[0].model_copy(update={"id": sid, "kind": kind, "parent_id": parent, "elements": []})


def ids(groups: list[list[State]]) -> list[list[str]]:
    return [[s.id for s in batch] for batch in groups]


# ---------- batching ----------

def test_batches_are_consecutive_screens_of_at_most_the_batch_size():
    scope = [state(f"s{i:02d}") for i in range(1, 11)]
    groups = ids(mock.batches(scope))
    assert [sid for batch in groups for sid in batch] == [s.id for s in scope]
    assert all(len(batch) <= mock.BATCH_SCREENS for batch in groups)
    assert len(groups) == -(-len(scope) // mock.BATCH_SCREENS)


def test_a_dialog_joins_its_parents_batch_wherever_the_order_puts_it():
    scope = [state("s01"), state("s09", "modal", "s05"), state("s02"), state("s03"), state("s04"), state("s05"),
             state("s06", "sheet", "s01"), state("s07", "modal", "s06")]
    assert ids(mock.batches(scope)) == [["s01", "s06", "s07"], ["s09", "s05", "s02", "s03"], ["s04"]]


def test_a_dialog_whose_parent_is_out_of_scope_stands_alone():
    groups = ids(mock.batches([state("s01"), state("s02", "modal", "s99"), state("s03")]))
    assert groups == [["s01", "s02", "s03"]]


def test_a_screen_with_more_dialogs_than_a_batch_holds_gets_one_batch_of_its_own():
    dialogs = [state(f"s1{i}", "modal", "s02") for i in range(mock.BATCH_SCREENS)]
    groups = ids(mock.batches([state("s01"), state("s02")] + dialogs + [state("s03")]))
    assert groups == [["s01"], ["s02"] + [d.id for d in dialogs], ["s03"]]


def test_golden_batches_cover_the_scope_once_with_every_dialog_by_its_parent(app):
    scope = mock.pick_scope(golden(app))
    groups = ids(mock.batches(scope))
    assert sorted(sid for batch in groups for sid in batch) == sorted(s.id for s in scope)
    assert all(len(batch) <= mock.BATCH_SCREENS for batch in groups)
    batch_of = {sid: n for n, batch in enumerate(groups) for sid in batch}
    for s in scope:
        if s.kind in mock.DIALOGS and s.parent_id in batch_of:
            assert batch_of[s.id] == batch_of[s.parent_id]


def test_mock_order_sets_the_scope_order_and_unlisted_states_follow(app):
    model = golden(app)
    screens = [s.id for s in mock.pick_scope(model)]
    ordered = model.model_copy(update={"mock_order": ["s99"] + screens[1:][::-1]})
    assert [s.id for s in mock.pick_scope(ordered)] == screens[1:][::-1] + screens[:1]


# ---------- stitching ----------

def styled_builder(calls: list):
    """The fake builder, plus one scoped <style> per batch."""
    fake = fake_builder(calls)

    def call(**kwargs):
        text, reply = fake(**kwargs)
        rule = "".join(f'section[data-screen="{sid}"] p{{margin:0}}' for sid in batch_screens(kwargs))
        return text.replace("```html\n", f"```html\n<style>{rule}</style>\n", 1), reply
    return call


def test_the_stitched_page_has_every_screen_once_one_runtime_and_one_shared_style(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    calls = []
    monkeypatch.setattr(llm, "call", styled_builder(calls))
    mock.run(ctx_for(run_dir, app))

    html = (run_dir / "mock" / "index.html").read_text()
    scope = mock.pick_scope(golden(app))
    sections = [t["attrs"]["data-screen"] for t in mock.StartTags(html).tags if "data-screen" in t["attrs"]]
    assert sorted(sections) == sorted(s.id for s in scope)
    assert html.count('id="simula-runtime"') == html.count('id="simula-runtime-js"') == 1
    assert html.count('id="simula-shared"') == 1 and html.count(":root{") == 1
    assert html.count("<style data-batch=") == len(calls) == len(mock.batches(scope))

    shared = {call["messages"][0]["content"][-2]["text"] for call in calls}
    assert len(shared) == 1 and mock.shared_style(scope) in shared.pop()
    assert ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text()).passed


# ---------- failure isolation ----------

def failing_on(first_screen: str, calls: list):
    """The fake builder, except the batch holding first_screen is refused."""
    fake = fake_builder(calls)

    def call(**kwargs):
        if first_screen in batch_screens(kwargs):
            raise llm.LLMFailure("refusal", "no")
        return fake(**kwargs)
    return call


def test_a_failed_batch_becomes_placeholders_and_the_rest_ship(tmp_path, monkeypatch, app, two_batches):
    run_dir = seed_model(tmp_path / "run", app)
    model = golden(app)
    scope = mock.pick_scope(model)
    failed, *drawn = mock.batches(scope)
    monkeypatch.setattr(llm, "call", failing_on(scope[0].id, []))
    mock.run(ctx_for(run_dir, app))

    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    undrawn = [e for e in report.errors if e.kind == "undrawn_screen"]
    assert [e.screen for e in undrawn] == [s.id for s in failed] and not report.passed
    assert all(e.detail == "screen not drawn: refusal: no" for e in undrawn)

    html = (run_dir / "mock" / "index.html").read_text()
    assert html.count("screen not drawn: refusal: no") == len(failed)
    placed = {t["attrs"]["data-el"] for t in mock.StartTags(html).tags if "data-el" in t["attrs"]}
    assert placed == {eid for batch in drawn for s in batch for eid in mock.tagged_ids(s)}
    assert sorted(p.stem for p in (run_dir / "mock" / "renders").glob("*.png")) == sorted(s.id for s in scope)

    [line] = [t for t in read_trace(run_dir / "trace.jsonl") if t.step == "batch1"]
    assert line.outcome == "refusal" and line.note.startswith(f"not drawn: {' '.join(s.id for s in failed)}")
    exhibit = (run_dir / "exhibits" / "03-mock.md").read_text()
    assert "| 1 | " + " ".join(s.id for s in failed) + " | not drawn: refusal: no |" in exhibit
    assert "| 2 | " + " ".join(s.id for s in drawn[0]) + " | drawn |" in exhibit


def test_the_stage_fails_only_when_every_batch_fails(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)

    def call(**kwargs):
        raise llm.LLMFailure("refusal", "no")
    monkeypatch.setattr(llm, "call", call)
    with pytest.raises(llm.LLMFailure):
        mock.run(ctx_for(run_dir, app))
    assert not (run_dir / "mock" / "index.html").exists()



def test_when_every_batch_fails_a_cap_failure_is_the_one_raised(tmp_path, monkeypatch, app, two_batches):
    run_dir = seed_model(tmp_path / "run", app)
    first = mock.pick_scope(golden(app))[0].id

    def call(**kwargs):
        if first in batch_screens(kwargs):
            raise llm.LLMFailure("refusal", "no")
        raise llm.CapReached("mock: next call is over the cap")
    monkeypatch.setattr(llm, "call", call)
    with pytest.raises(llm.CapReached):
        mock.run(ctx_for(run_dir, app))

# ---------- through llm.call: cache replay and the $ cap ----------

def provider_drawing(model, calls: list, tokens_out: int = 1000):
    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model_id)
        page = without_edges(skeleton_html(model, batch_screens({"messages": messages})))
        return llm.Reply(text=f"```html\n{page}\n```", model=model_id, tokens_in=5000, tokens_out=tokens_out)
    return provider


def with_cache_in(tmp_path, monkeypatch):
    real = llm.call
    monkeypatch.setattr(llm, "call", lambda **kwargs: real(**kwargs, cache_dir=tmp_path / "cache"))


def test_each_batch_replays_from_the_cache(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    with_cache_in(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(app), calls))
    mock.run(ctx_for(run_dir, app))
    first = (run_dir / "mock" / "index.html").read_text()
    batches = len(mock.batches(mock.pick_scope(golden(app))))
    assert len(calls) == batches

    replay = ctx_for(run_dir, app)
    replay.replay = True
    mock.run(replay)
    assert len(calls) == batches
    assert (run_dir / "mock" / "index.html").read_text() == first
    hits = [t for t in read_trace(run_dir / "trace.jsonl") if t.step.startswith("batch") and t.cache_hit]
    assert sorted(t.step for t in hits) == [f"batch{n}" for n in range(1, batches + 1)]


def test_affordable_is_the_priority_prefix_that_fits_with_one_retry_spare():
    assert mock.affordable([2.72] * 10, 25.0) == 8
    assert mock.affordable([2.0, 3.0, 1.0], 8.0) == 2  # the spare is the largest batch, not the next one
    assert mock.affordable([3.0, 1.0], 5.5) == 0  # never skips batch 1 to fit batch 2
    assert (mock.affordable([2.5, 2.5], 7.5), mock.affordable([2.5, 2.5], 7.4)) == (2, 1)


def capped_run(tmp_path, monkeypatch, app, calls: list):
    """Every batch's worst case is $1 and the cap $2, so the plan keeps batch 1 plus a $1 retry spare, and no more."""
    run_dir = seed_model(tmp_path / "run", app)
    with_cache_in(tmp_path, monkeypatch)
    monkeypatch.setattr(mock, "worst_usd", lambda ctx, content: 1.0)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(app), calls))
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 2.0
    mock.run(ctx)
    return run_dir


def test_batches_past_the_usd_cap_are_skipped_in_priority_order_before_any_call(tmp_path, monkeypatch, app,
                                                                                 two_batches):
    calls = []
    run_dir = capped_run(tmp_path, monkeypatch, app, calls)

    first, *skipped = mock.batches(mock.pick_scope(golden(app)))
    assert len(calls) == 1
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    undrawn = [e for e in report.errors if e.kind == "undrawn_screen"]
    assert [e.screen for e in undrawn] == [s.id for batch in skipped for s in batch]
    assert all(e.detail.startswith("screen not drawn: $ cap reached: over budget") for e in undrawn)
    trace = read_trace(run_dir / "trace.jsonl")
    [plan] = [t for t in trace if t.step == "plan"]
    assert (plan.outcome, plan.note) == ("cap", f"1 of {len(skipped) + 1} batches fit the $2.00 cap at worst case: "
                                                "$1.00 + $1.00 spare for one retry")
    assert {t.step for t in trace if t.outcome == "cap"} == {"plan"} | {f"batch{n}" for n in range(2, len(skipped) + 2)}
    exhibit = (run_dir / "exhibits" / "03-mock.md").read_text()
    assert f"Batch plan: {plan.note}." in exhibit
    assert f"| 1 | {' '.join(s.id for s in first)} | drawn | $1.00 |" in exhibit
    assert f"| 2 | {' '.join(s.id for s in skipped[0])} | not drawn: $ cap reached: over budget" in exhibit


def test_a_capped_run_replays_to_the_same_page(tmp_path, monkeypatch, app, two_batches):
    """A batch the plan left out has no cache entry. The replay plans against the same cap, not what is left of it,
    so it leaves the same batches out instead of asking the cache for them."""
    calls = []
    run_dir = capped_run(tmp_path, monkeypatch, app, calls)
    first = (run_dir / "mock" / "index.html").read_text()

    replay = ctx_for(run_dir, app)
    replay.usd_cap, replay.replay = 2.0, True
    mock.run(replay)
    assert len(calls) == 1
    assert (run_dir / "mock" / "index.html").read_text() == first


def test_when_the_plan_fits_the_lock_never_turns_a_batch_away(tmp_path, monkeypatch, app):
    """One screen per batch, PARALLEL_BATCHES in flight together, and every answer costs its call's full worst case:
    a cap of exactly every batch's worst case plus one spare still draws them all."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 1)
    run_dir = seed_model(tmp_path / "run", app)
    with_cache_in(tmp_path, monkeypatch)
    model, ctx = golden(app), ctx_for(run_dir, app)
    scope = mock.pick_scope(model)
    # The art goes in the brief, so build it as run() does: without it the cap comes out too tight.
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    art = mock.crop_art(run_dir / "model", run_dir / "mock", scope, model.device)
    worst = [mock.worst_usd(ctx, mock.batch_content(ctx, model, batch, [s.id for s in scope], art,
                                                    mock.shared_style(scope))) for batch in mock.batches(scope)]
    # sum() rounds differently from the plan's running total (it compensates), so allow a nanodollar either way.
    ctx.usd_cap = sum(worst) + max(worst) + 1e-9
    together = threading.Barrier(mock.PARALLEL_BATCHES, timeout=1)

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        try:
            together.wait()
        except threading.BrokenBarrierError:
            pass  # the last batches, fewer than PARALLEL_BATCHES, go alone
        page = without_edges(skeleton_html(model, batch_screens({"messages": messages})))
        return llm.Reply(text=f"```html\n{page}\n```", model=model_id,
                         tokens_in=llm.estimate_tokens_in(system, messages), tokens_out=max_tokens)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    mock.run(ctx)

    trace = [t for t in read_trace(run_dir / "trace.jsonl") if t.stage == "mock"]
    assert len(worst) > mock.PARALLEL_BATCHES
    assert [t.step for t in trace if t.outcome == "cap"] == []
    assert sum(t.usd for t in trace) <= ctx.usd_cap


def test_a_cap_too_small_for_any_batch_stops_the_stage(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    with_cache_in(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(app), calls))
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 0.0001
    with pytest.raises(llm.CapReached):
        mock.run(ctx)
    assert calls == []



# ---------- a batch reaching outside its own screens ----------

def first_batch_adds(extra: str):
    """The fake builder, with extra markup at the top of the first batch's body."""
    fake = fake_builder([])

    def call(**kwargs):
        text, reply = fake(**kwargs)
        return (text.replace("<body>", f"<body>{extra}", 1) if kwargs["step"] == "batch1" else text), reply
    return call


def contract_errors(run_dir) -> list:
    return ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text()).errors


def test_a_batch_rule_outside_its_own_screens_is_a_contract_error(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    first = mock.batches(mock.pick_scope(golden(app)))[0]
    monkeypatch.setattr(llm, "call", first_batch_adds("<style>p{color:red}</style>"))
    mock.run(ctx_for(run_dir, app))
    [error] = contract_errors(run_dir)
    assert (error.kind, error.screen) == ("unscoped_css", first[0].id) and error.detail.startswith("'p'")


def test_a_section_for_another_batchs_screen_is_a_contract_error(tmp_path, monkeypatch, app, two_batches):
    run_dir = seed_model(tmp_path / "run", app)
    first, second = mock.batches(mock.pick_scope(golden(app)))[:2]
    monkeypatch.setattr(llm, "call", first_batch_adds(f'<section data-screen="{second[0].id}"></section>'))
    mock.run(ctx_for(run_dir, app))
    [error] = contract_errors(run_dir)
    assert (error.kind, error.screen) == ("foreign_screen", first[0].id) and second[0].id in error.detail
