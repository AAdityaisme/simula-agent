"""Stage 3 draws its scope in parallel batches: batching, stitching, failure isolation, cache replay, the $ cap."""

import json
import random
import threading
import time

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
    """Batches of 2, so every golden draws in at least three batches."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 2)


def state(sid: str, kind: str = "screen", parent: str | None = None) -> State:
    return golden("janitorai").states[0].model_copy(update={"id": sid, "kind": kind, "parent_id": parent, "elements": []})


def dense(sid: str, tagged: int, kind: str = "screen", parent: str | None = None) -> State:
    """A state with `tagged` elements that carry data-el."""
    element = next(e for e in golden("janitorai").states[0].elements if e.in_mock)
    elements = [element.model_copy(update={"id": f"{sid}.e{i}", "repeat_group": None}) for i in range(tagged)]
    return state(sid, kind, parent).model_copy(update={"elements": elements})


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


def test_dense_screens_form_smaller_batches_while_sparse_ones_fill_a_batch():
    half = mock.BATCH_TAGGED // 2
    scope = [dense("s01", half), dense("s02", half), dense("s03", half)] + [dense(f"s0{i}", 5) for i in range(4, 8)]
    assert ids(mock.batches(scope)) == [["s01", "s02"], ["s03", "s04", "s05", "s06"], ["s07"]]


def test_a_dialog_stays_with_its_parent_and_a_unit_denser_than_a_batch_stands_alone():
    scope = [dense("s01", 10), dense("s02", mock.BATCH_TAGGED), dense("s03", 10, "modal", "s02"), dense("s04", 10)]
    assert ids(mock.batches(scope)) == [["s01"], ["s02", "s03"], ["s04"]]


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


def screens_of(messages) -> list[str]:
    """The screens a batch's request asks for, on its first call or its retry."""
    content = [p for p in messages[0]["content"] if p.get("text") != mock.SHORTER]
    return batch_screens({"messages": [{"role": "user", "content": content}]})


def drawn(model, model_id, messages, tokens_out=1000, stop="end_turn") -> llm.Reply:
    page = without_edges(skeleton_html(model, screens_of(messages)))
    return llm.Reply(text=f"```html\n{page}\n```", model=model_id, tokens_in=5000, tokens_out=tokens_out,
                     stop_reason=stop)


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


def priced(tmp_path, monkeypatch, app, calls: list, usd: float = 0.5) -> tuple:
    """A run folder where each batch's call holds $1 while in flight and costs `usd` once answered."""
    run_dir = seed_model(tmp_path / "run", app)
    with_cache_in(tmp_path, monkeypatch)
    model = golden(app)
    monkeypatch.setattr(llm, "worst_case_usd", lambda model_id, tokens_in, tokens_out: 1.0)
    monkeypatch.setattr(llm, "usd", lambda model_id, tokens_in, tokens_out, tokens_cached=0: usd)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(model, calls))
    return run_dir, len(mock.batches(mock.pick_scope(model)))


def kept(run_dir) -> int:
    return json.loads((run_dir / "mock" / "plan.json").read_text())["keep"]


def test_a_batch_is_admitted_when_its_hold_fits_beside_the_calls_in_flight(tmp_path, monkeypatch, app):
    """Planned before any call ran, every batch counted at its worst case: a $4 cap with $1 worst cases kept three
    batches and a spare, though each answer cost $0.20. Admitted at reserve time, a hold goes back at what its call
    cost, so with two calls in flight and one spare every batch fits."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 1)
    monkeypatch.setattr(mock, "PARALLEL_BATCHES", 2)
    calls = []
    run_dir, n = priced(tmp_path, monkeypatch, app, calls, usd=0.2)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 4.0
    mock.run(ctx)
    assert n > 3 and len(calls) == n and kept(run_dir) == n
    assert "screen not drawn" not in (run_dir / "mock" / "index.html").read_text()


def capped_run(tmp_path, monkeypatch, app, calls: list):
    """Every call holds $1 and costs $1, so a $2 cap admits batch 1 with its retry's $1 spare and turns batch 2 away,
    whichever call ends first."""
    run_dir, _ = priced(tmp_path, monkeypatch, app, calls, usd=1.0)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 2.0
    mock.run(ctx)
    return run_dir


def test_once_the_cap_turns_a_batch_away_no_later_batch_is_asked_for(tmp_path, monkeypatch, app, two_batches):
    calls = []
    run_dir = capped_run(tmp_path, monkeypatch, app, calls)

    first, *left_out = mock.batches(mock.pick_scope(golden(app)))
    assert len(calls) == 1
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    undrawn = [e for e in report.errors if e.kind == "undrawn_screen"]
    assert [e.screen for e in undrawn] == [s.id for batch in left_out for s in batch]
    assert all(e.detail.startswith(f"screen not drawn: $ cap reached: over budget: batches 2-{len(left_out) + 1} "
                                   "were left out") for e in undrawn)
    trace = read_trace(run_dir / "trace.jsonl")
    [plan] = [t for t in trace if t.step == "plan"]
    assert plan.note == (f"1 of {len(left_out) + 1} batches kept in priority order under the $2.00 cap with $0.00 "
                         "already spent, each holding its worst case while in flight and one more free for a retry")
    assert [t.step for t in trace if t.outcome == "cap"] == ["batch2", "over_budget"]
    [over] = [t for t in trace if t.step == "over_budget"]
    assert over.note == mock.not_drawn([s for b in left_out for s in b], mock.OVER_BUDGET)
    exhibit = (run_dir / "exhibits" / "03-mock.md").read_text()
    assert f"Batch plan: {plan.note}." in exhibit
    assert f"| 1 | {' '.join(s.id for s in first)} | drawn |" in exhibit
    assert f"| 2 | {' '.join(s.id for s in left_out[0])} | not drawn: $ cap reached: over budget" in exhibit


def test_batches_are_admitted_in_priority_order_whichever_reaches_the_cap_first(tmp_path, monkeypatch, app,
                                                                                two_batches):
    """Batch 1's call comes last to the $ check, yet the cap still admits it and turns batch 2 away: a batch's first
    hold waits for every batch before it to take theirs."""
    calls = []
    run_dir, _ = priced(tmp_path, monkeypatch, app, calls, usd=1.0)
    real = mock.generate

    def first_comes_late(ctx, content, budget, step):
        if step == "batch1":
            time.sleep(0.3)
        return real(ctx, content, budget, step)
    monkeypatch.setattr(mock, "generate", first_comes_late)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 2.0
    mock.run(ctx)
    first, *left_out = mock.batches(mock.pick_scope(golden(app)))
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    assert [e.screen for e in report.errors if e.kind == "undrawn_screen"] == [s.id for b in left_out for s in b]
    assert len(calls) == 1 and kept(run_dir) == 1


def test_a_batch_whose_first_answer_is_cut_off_keeps_room_for_its_retry(tmp_path, monkeypatch, app):
    """Red team probe A: batch 1's first answer runs out of tokens while batch 2 would hold. Without a spare, batch 2
    took the room and batch 1's retry was turned away, so the home screen went undrawn while batch 2 drew. A first
    hold now leaves one worst case free, so batch 2 is the one left out and batch 1 is redrawn."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 1)
    monkeypatch.setattr(mock, "PARALLEL_BATCHES", 2)
    run_dir, _ = priced(tmp_path, monkeypatch, app, [])
    monkeypatch.setattr(llm, "usd", lambda model_id, tokens_in, tokens_out, tokens_cached=0:
                        0.2 if tokens_out >= 100000 else 0.5)
    model = golden(app)
    first, second = [batch[0].id for batch in mock.batches(mock.pick_scope(model))[:2]]

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        screens = screens_of(messages)
        retry = any(p.get("text") == mock.SHORTER for p in messages[0]["content"])
        if screens[0] == first and not retry:
            time.sleep(0.1)
            return drawn(model, model_id, messages, tokens_out=128000, stop="max_tokens")
        if screens[0] == second:
            time.sleep(0.5)
        return drawn(model, model_id, messages)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 2.1
    mock.run(ctx)
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    undrawn = {e.screen for e in report.errors if e.kind == "undrawn_screen"}
    assert first not in undrawn and second in undrawn and kept(run_dir) == 1


class Strict(threading.Condition):
    """A condition whose wait fails once nothing has woken it for 5 s: production waits have no timeout, so a wait only
    a timeout ends is a batch that would wait for ever, even if its condition holds by then (a lost wakeup)."""

    def wait_for(self, predicate, timeout=None):
        while not predicate():
            assert self.wait(5), "a batch waited 5 s unwoken: in production it waits for ever"
        return True


def draw_on_fake_budget(tmp_path, monkeypatch, cap: float, calls: dict, ended: list, parallel: int = 2,
                        after: dict[str, tuple[str, ...]] | None = None):
    """draw_batches over one-screen batches whose generate holds and charges the budget as llm.call does. `calls`
    gives each batch's calls in order as (worst case, cost, seconds, cut off at max_tokens); `ended` collects the
    calls in the order they ended. `after` holds a call, once it holds its worst case, until the events it names have
    happened, whatever the machine's load: another call's name ("batch2", "batch2 retry") once it ended, or its name
    and " held" once it holds."""
    monkeypatch.setattr(mock, "PARALLEL_BATCHES", parallel)
    monkeypatch.setattr(mock.threading, "Condition", Strict)
    names = [step + (" retry" if n else "") for step, specs in calls.items() for n in range(len(specs))]
    happened = {event: threading.Event() for name in names for event in (name, f"{name} held")}

    def generate(ctx, content, budget, step):
        for n, (worst, cost, seconds, cut_off) in enumerate(calls[step]):
            name = step + (" retry" if n else "")
            if n:
                budget.spare = False
            budget.reserve(worst, step=step)
            happened[f"{name} held"].set()
            for event in (after or {}).get(name, ()):
                assert happened[event].wait(5), f"{name} waited 5 s for {event}"
            time.sleep(seconds)
            budget.charge(cost, worst)
            ended.append(name)
            happened[name].set()
            if not cut_off:
                return f'<section data-screen="{content[0]["text"]}"></section>'
        raise llm.LLMFailure("max_tokens", "cut off")
    monkeypatch.setattr(mock, "generate", generate)
    groups = [[state(f"s{n:02d}")] for n in range(1, len(calls) + 1)]
    contents = [[{"type": "text", "text": batch[0].id}] for batch in groups]
    return mock.draw_batches(ctx_for(tmp_path, "janitorai"), groups, contents, llm.Budget("mock", cap), None)


SOL_6 = {"batch1": [(2.0, 1.5, 0.1, True), (2.0, 0.5, 0, False)], "batch2": [(1.0, 0.5, 0.4, False)],
         "batch3": [(1.0, 0.5, 0, False)]}


def test_a_cut_off_batchs_retry_waits_for_a_later_batch_to_settle_and_is_drawn(tmp_path, monkeypatch):
    """Sol 6: batch 1's answer is cut off, and its $2 retry doesn't fit beside batch 2's $1 hold under a $4 cap. It
    used to be left undrawn while batch 2 drew; now it waits for batch 2 to settle, and what batch 2 gave back holds
    it. Batch 3 can't start meanwhile, so it doesn't take that room."""
    ended = []
    drawn = draw_on_fake_budget(tmp_path, monkeypatch, 4.0, SOL_6, ended)
    assert ended == ["batch1", "batch2", "batch1 retry"]
    assert [batch[0].id for batch, _ in drawn.lost] == ["s03"] and drawn.plan.keep == 2


def test_a_retry_that_still_doesnt_fit_leaves_out_its_batch_and_every_later_one_already_drawn(tmp_path, monkeypatch):
    """Sol 6 again, with batch 2 costing $1: the room it gives back can't hold batch 1's retry, so batch 1 is left out,
    and batch 2 with it though it drew, so the batches drawn stay the top-ranked ones."""
    ended = []
    with pytest.raises(llm.CapReached, match="over budget: batches 1-3 were left out"):
        draw_on_fake_budget(tmp_path, monkeypatch, 4.0, {**SOL_6, "batch2": [(1.0, 1.0, 0.4, False)]}, ended)
    assert ended == ["batch1", "batch2"]
    assert json.loads((tmp_path / "mock" / "plan.json").read_text())["keep"] == 0


def test_two_cut_off_batches_near_the_cap_both_draw_without_hanging(tmp_path, monkeypatch):
    """Red team rt-37 (and Greptile): batch 1's $2 retry waits on batch 2's $1 hold under a $4 cap; then batch 2 is cut
    off too and its retry waits behind batch 1. Nothing is in flight any more, so batch 1 must be woken to go first;
    without that both batches wait for ever."""
    ended = []
    drawn = draw_on_fake_budget(tmp_path, monkeypatch, 4.0, {
        "batch1": [(2.0, 1.5, 0.1, True), (2.0, 0.5, 0, False)],
        "batch2": [(1.0, 0.5, 0.4, True), (1.0, 0.3, 0, False)]}, ended)
    assert ended == ["batch1", "batch2", "batch1 retry", "batch2 retry"]
    assert drawn.lost == [] and drawn.plan.keep == 2


def batch_rows(tmp_path, plan: mock.Plan, undrawn_from: int, reason: str = "$ cap reached: over budget") -> str:
    """The exhibit of three one-screen batches, all but the first `undrawn_from - 1` not drawn for `reason`."""
    model = golden("janitorai")
    scope = mock.pick_scope(model)
    undrawn = {s.id: reason for s in scope[undrawn_from - 1:3]}
    report = ContractReport(passed=False, screens=[s.id for s in scope], errors=[])
    return mock.exhibit(ctx_for(tmp_path, "janitorai"), model, scope, [[s] for s in scope[:3]], undrawn, "", report,
                        plan)


DREW_PAST = {"batch1": [(1.0, 0.5, 0, False)], "batch2": [(1.0, 0.9, 0, True), (2.0, 0.5, 0, False)],
             "batch3": [(1.0, 0.9, 0, False)]}
# Batch 2's first answer ends once batch 1 has and batch 3 holds, so its $2 retry can't fit beside batch 3; batch 3
# ends after it.
DREW_PAST_ORDER = {"batch2": ("batch1", "batch3 held"), "batch3": ("batch2",)}


def test_a_batch_that_drew_but_was_left_out_to_keep_the_prefix_is_reported_with_its_cost(tmp_path, monkeypatch):
    """Red team rt-37: batch 2's retry still doesn't fit once batch 3, drawn for $0.90, has settled, so batch 3 is left
    out to keep the kept batches a prefix. The plan line and its exhibit row say it drew, what it cost, and that a
    rerun with a raised cap takes it from the cache free unless run with --no-cache (Greptile)."""
    ended = []
    drawn = draw_on_fake_budget(tmp_path, monkeypatch, 4.0, DREW_PAST, ended, after=DREW_PAST_ORDER)
    assert ended == ["batch1", "batch2", "batch3"] and drawn.plan.keep == 1 and drawn.plan.dropped == ((3, 0.9, None),)
    note = ("drew ($0.90) and was left out to keep the kept batches top-ranked; a rerun with a raised cap takes it "
            "from the cache free unless run with --no-cache")
    assert drawn.plan.line(3) == ("1 of 3 batches kept in priority order under the $4.00 cap with $0.00 already spent, "
                                  "each holding its worst case while in flight and one more free for a retry; batch 3 "
                                  + note)
    rows = batch_rows(tmp_path, drawn.plan, 2)
    scope = mock.pick_scope(golden("janitorai"))
    assert f"| 2 | {scope[1].id} | not drawn: $ cap reached: over budget |" in rows
    assert f"| 3 | {scope[2].id} | {note} |" in rows


def test_a_batch_that_ran_and_failed_past_the_prefix_keeps_its_cost_and_its_own_error(tmp_path, monkeypatch):
    """Greptile: batch 3's call ran and was charged $0.90 but was cut off with no retry left, past batch 2, which the
    cap left out. Its cost and its own error stay in the plan line and its exhibit row, not just the cap's reason."""
    ended = []
    drawn = draw_on_fake_budget(tmp_path, monkeypatch, 4.0, {**DREW_PAST, "batch3": [(1.0, 0.9, 0, True)]}, ended,
                                after=DREW_PAST_ORDER)
    error = mock.failure_reason(llm.LLMFailure("max_tokens", "cut off"))
    assert ended == ["batch1", "batch2", "batch3"] and drawn.plan.dropped == ((3, 0.9, error),)
    assert drawn.plan.line(3).endswith(f"; batch 3 ran ($0.90) and failed: {error}")
    scope = mock.pick_scope(golden("janitorai"))
    assert f"| 3 | {scope[2].id} | ran ($0.90) and failed: {error} |" in batch_rows(tmp_path, drawn.plan, 2)


def test_a_failure_that_spans_lines_stays_on_its_row_of_the_batch_table(tmp_path):
    """Greptile: a provider's error with a newline (or a |) split the exhibit's markdown row."""
    plan = mock.Plan(1, 4.0, 0.0, ((3, 0.9, "overloaded\nretry | later"),))
    rows = batch_rows(tmp_path, plan, 2, reason="overloaded\nretry | later")
    scope = mock.pick_scope(golden("janitorai"))
    assert f"| 2 | {scope[1].id} | not drawn: overloaded retry / later |" in rows
    assert f"| 3 | {scope[2].id} | ran ($0.90) and failed: overloaded retry / later |" in rows


FUZZ_SEEDS = (251, 348, 376, 482, 657, 827, 896, 1376, *range(8))  # rt-37's seven hangs and its float edge


@pytest.mark.parametrize("seed", FUZZ_SEEDS)
def test_random_schedules_keep_a_prefix_never_hang_and_refuse_only_with_nothing_in_flight(tmp_path, monkeypatch,
                                                                                          seed):
    """Red team rt-37's fuzz: 3 to 8 batches, 2 to 4 at once, 60% of first answers cut off, any cap. The kept batches
    are a priority prefix, no wait is lost (Strict), no later first call is held while an earlier batch waits, and the
    cap turns a call away only with nothing held."""
    rnd = random.Random(seed)
    calls = {}
    for n in range(1, rnd.randint(3, 8) + 1):
        worst = round(rnd.uniform(0.5, 2.0), 2)
        first = (worst, round(worst * rnd.uniform(0.2, 1.0), 2), rnd.uniform(0, 0.04), rnd.random() < 0.6)
        calls[f"batch{n}"] = [first, (worst, round(worst * rnd.uniform(0.2, 1.0), 2), rnd.uniform(0, 0.04), False)]
    cap, parallel = round(rnd.uniform(2.0, 9.0), 2), rnd.randint(2, 4)
    firsts, refused = [], []
    turn_reserve, budget_reserve = llm.Turn.reserve, llm.Budget.reserve

    def first_hold(self, worst_usd, **where):  # under BatchTurn's lock
        if not self.held:
            firsts.append((self.rank, sorted(self.waiting)))
        return turn_reserve(self, worst_usd, **where)

    def watched(self, worst_usd, **where):
        try:
            return budget_reserve(self, worst_usd, **where)
        except llm.CapReached:
            refused.append(self.held)
            raise
    monkeypatch.setattr(llm.Turn, "reserve", first_hold)
    monkeypatch.setattr(llm.Budget, "reserve", watched)
    try:
        drawn = draw_on_fake_budget(tmp_path, monkeypatch, cap, calls, [], parallel)
        keep, lost = drawn.plan.keep, [int(batch[0].id[1:]) for batch, _ in drawn.lost]
    except llm.CapReached:
        keep, lost = 0, list(range(1, len(calls) + 1))
    assert lost == list(range(keep + 1, len(calls) + 1))
    assert [(rank, ahead) for rank, ahead in firsts if any(r < rank for r in ahead)] == []
    assert all(abs(held) < 1e-9 for held in refused)


def test_a_batch_turned_away_while_calls_are_in_flight_is_drawn_once_they_settle(tmp_path, monkeypatch):
    """Fable M10: batch 3's $1 hold and its spare don't fit beside batches 1 and 2 in flight under a $3.50 cap. It used
    to be left out, and every batch after it; now it waits for them to settle, and what they gave back holds it."""
    ended = []
    calls = {f"batch{n}": [(1.0, 0.2, 0.3 if n < 3 else 0, False)] for n in range(1, 5)}
    drawn = draw_on_fake_budget(tmp_path, monkeypatch, 3.5, calls, ended, parallel=3)
    assert sorted(ended[:2]) == ["batch1", "batch2"] and sorted(ended[2:]) == ["batch3", "batch4"]
    assert drawn.lost == [] and drawn.plan.keep == 4


def test_a_retry_keeps_no_spare_even_as_its_batchs_first_hold(tmp_path, monkeypatch, app):
    """Red team probe R: run 1 cuts batch 1's first answer off (a known failure, cached) and loses its retry to a
    timeout. On the rerun the retry is the batch's first hold; it kept a spare for a retry that can't follow, so with
    $1.50 of room and $1 holds the cap turned it away and every later batch with it. Now it draws."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 1)
    monkeypatch.setattr(mock, "PARALLEL_BATCHES", 1)
    run_dir, _ = priced(tmp_path, monkeypatch, app, [])
    monkeypatch.setattr(llm, "usd", lambda model_id, tokens_in, tokens_out, tokens_cached=0:
                        0.9 if tokens_out >= 100000 else 0.3)
    model = golden(app)
    first = mock.batches(mock.pick_scope(model))[0][0].id
    lose = [True]

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        retry = any(p.get("text") == mock.SHORTER for p in messages[0]["content"])
        if first in screens_of(messages) and not retry:
            return drawn(model, model_id, messages, tokens_out=128000, stop="max_tokens")
        if first in screens_of(messages) and lose[0]:
            raise llm.LLMFailure("timeout", "stream idle")
        return drawn(model, model_id, messages)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 100.0
    mock.run(ctx)
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    assert first in {e.screen for e in report.errors if e.kind == "undrawn_screen"}

    lose[0] = False
    ctx.usd_cap = llm.Budget.for_stage("mock", run_dir / "trace.jsonl").spent + 1.5
    mock.run(ctx)
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    assert not [e for e in report.errors if e.kind == "undrawn_screen"]


def test_a_capped_run_replays_to_the_same_page_whatever_cap_or_batching_rule_the_replay_has(tmp_path, monkeypatch,
                                                                                            app, two_batches):
    """A batch the cap left out has no cache entry, and batches grouped another way ask other questions. The replay
    reads the live run's batches and how many it admitted from mock/plan.json, before it builds a request, so it asks
    the same calls with no --usd-cap or any other, and whatever the batch size is now."""
    calls = []
    run_dir = capped_run(tmp_path, monkeypatch, app, calls)
    groups = mock.batches(mock.pick_scope(golden(app)))
    assert json.loads((run_dir / "mock" / "plan.json").read_text()) == {
        "batches": [[s.id for s in batch] for batch in groups], "keep": 1}
    first = (run_dir / "mock" / "index.html").read_text()

    for usd_cap, screens in ((None, 2), (2.0, 1), (40.0, 4)):
        monkeypatch.setattr(mock, "BATCH_SCREENS", screens)
        replay = ctx_for(run_dir, app)
        replay.usd_cap, replay.replay = usd_cap, True
        mock.run(replay)
        assert len(calls) == 1
        assert (run_dir / "mock" / "index.html").read_text() == first


@pytest.mark.parametrize("record, says", [(None, "no recorded batch plan for this scope"),
                                          ({"keep": 1}, "recorded before batches were recorded")],
                         ids=["no-record", "no-batches"])
def test_a_replay_with_no_recorded_batches_stops(tmp_path, monkeypatch, app, two_batches, record, says):
    run_dir = capped_run(tmp_path, monkeypatch, app, [])
    path = run_dir / "mock" / "plan.json"
    if record is None:
        path.rename(tmp_path / "plan-moved-aside.json")
    else:  # a run recorded before the batches were
        path.write_text(json.dumps(record))
    replay = ctx_for(run_dir, app)
    replay.replay = True
    with pytest.raises(llm.ReplayMiss, match=says):
        mock.run(replay)


def rerun(tmp_path, monkeypatch, app, new_keys: bool = True) -> tuple:
    """A live mock whose cap covers every batch's hold, so it draws them all and leaves $0.50 per batch spent, then a
    second live one in the same run folder: with every cache key new (as after a builder prompt change), or with every
    answer already cached."""
    calls = []
    run_dir, n = priced(tmp_path, monkeypatch, app, calls)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = n + 1.0
    mock.run(ctx)
    ctx.no_cache = new_keys
    mock.run(ctx)
    return run_dir, n, calls


def test_a_rerun_whose_answers_are_cached_draws_every_batch_again_for_free(tmp_path, monkeypatch, app, two_batches):
    """The first run's spend counts against the cap, but a cached answer costs nothing and holds nothing, so a rerun
    with nothing changed draws the same page, even under a cap below what is spent."""
    run_dir, n, calls = rerun(tmp_path, monkeypatch, app, new_keys=False)
    assert len(calls) == n and kept(run_dir) == n
    plan = [t for t in read_trace(run_dir / "trace.jsonl") if t.step == "plan"][-1]
    assert plan.note.startswith(f"{n} of {n} batches kept in priority order under the ${n + 1:.2f} cap with "
                                f"${0.5 * n:.2f} already spent")
    assert "screen not drawn" not in (run_dir / "mock" / "index.html").read_text()
    lowered = ctx_for(run_dir, app)
    lowered.usd_cap = 0.25
    mock.run(lowered)
    assert len(calls) == n and "screen not drawn" not in (run_dir / "mock" / "index.html").read_text()


def test_raising_the_cap_after_an_over_budget_run_draws_the_rest(tmp_path, monkeypatch, app, two_batches):
    """needs-human's way out of an over-budget mock: rerun with a higher --usd-cap. Batch 1 is cached and free, so the
    new cap only has to hold the batches not drawn yet and one spare on top of what was spent."""
    calls = []
    run_dir = capped_run(tmp_path, monkeypatch, app, calls)
    n = len(mock.batches(mock.pick_scope(golden(app))))
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = 1.0 + (n - 1) + 1.0
    mock.run(ctx)
    assert len(calls) == n and kept(run_dir) == n
    assert "screen not drawn" not in (run_dir / "mock" / "index.html").read_text()


def test_a_batch_that_timed_out_once_is_free_once_a_rerun_answered_it(tmp_path, monkeypatch, app, two_batches):
    """A cached answer holds nothing: the call reads the latest try the model answered, past a lost call. So once a
    rerun fills in a batch that timed out, a later rerun draws everything for free, even under a cap below the
    spend."""
    calls = []
    run_dir, n = priced(tmp_path, monkeypatch, app, calls)
    model = golden(app)
    first = mock.batches(mock.pick_scope(model))[0][0].id
    drawing, lost = provider_drawing(model, calls), []

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        if first in batch_screens({"messages": messages}) and not lost:
            lost.append(first)
            raise llm.LLMFailure("timeout", "stream idle")
        return drawing(model_id, system, messages, effort, schema, max_tokens, total_timeout)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = n + 1.0
    mock.run(ctx)
    mock.run(ctx)
    made = len(calls)
    lowered = ctx_for(run_dir, app)
    lowered.usd_cap = 0.25
    mock.run(lowered)
    assert len(calls) == made and kept(run_dir) == n
    assert "screen not drawn" not in (run_dir / "mock" / "index.html").read_text()


@pytest.mark.parametrize("first_answer", ["max_tokens_then_the_retry_answers", "refusal"])
def test_a_batch_whose_every_call_settles_from_the_cache_is_free_even_when_one_failed(tmp_path, monkeypatch, app,
                                                                                    two_batches, first_answer):
    """A cached known failure costs nothing: the call raises it again with no hold. After a first answer cut off at
    max_tokens the retry's record decides; a refusal is a placeholder at no cost. So a rerun with every call cached
    draws the same page, even under a cap below the spend."""
    calls = []
    run_dir, n = priced(tmp_path, monkeypatch, app, calls)
    model = golden(app)
    first = mock.batches(mock.pick_scope(model))[0][0].id

    def provider(model_id, system, messages, effort, schema, max_tokens, total_timeout=None):
        calls.append(model_id)
        retry = any(p.get("text") == mock.SHORTER for p in messages[0]["content"])
        if first in screens_of(messages) and not retry:
            stop = "refusal" if first_answer == "refusal" else "max_tokens"
            return drawn(model, model_id, messages, tokens_out=128000, stop=stop)
        return drawn(model, model_id, messages)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider)
    ctx = ctx_for(run_dir, app)
    ctx.usd_cap = n + 2.0
    mock.run(ctx)
    made, page = len(calls), (run_dir / "mock" / "index.html").read_text()
    lowered = ctx_for(run_dir, app)
    lowered.usd_cap = 0.25
    mock.run(lowered)
    assert len(calls) == made and (run_dir / "mock" / "index.html").read_text() == page
    assert kept(run_dir) == n


def test_a_replay_after_a_rerun_draws_the_reruns_page(tmp_path, monkeypatch, app, two_batches):
    """The replay's trace holds both live runs' spend, so admitting again could admit fewer batches than the rerun
    drew."""
    run_dir, _, _ = rerun(tmp_path, monkeypatch, app)
    page = (run_dir / "mock" / "index.html").read_text()
    replay = ctx_for(run_dir, app)
    replay.replay = True
    mock.run(replay)
    assert (run_dir / "mock" / "index.html").read_text() == page


def test_a_cap_that_covers_every_batchs_worst_case_never_turns_one_away(tmp_path, monkeypatch, app):
    """One screen per batch, PARALLEL_BATCHES in flight together, and every answer costs its call's full worst case:
    a cap of exactly every batch's worst case and one spare still draws them all."""
    monkeypatch.setattr(mock, "BATCH_SCREENS", 1)
    run_dir = seed_model(tmp_path / "run", app)
    with_cache_in(tmp_path, monkeypatch)
    model, ctx = golden(app), ctx_for(run_dir, app)
    scope = mock.pick_scope(model)
    # The art goes in the brief, so build it as run() does: without it the cap comes out too tight.
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    art = mock.crop_art(run_dir / "model", run_dir / "mock", scope, model.device)
    requests = [mock.builder_requests(ctx, mock.batch_content(ctx, model, batch, [s.id for s in scope], art,
                                                              mock.shared_style(scope)))[0]
                for batch in mock.batches(scope)]
    worst = [llm.worst_case_usd(r["model"], llm.estimate_tokens_in(r["system"], r["messages"]), r["max_tokens"])
             for r in requests]
    # sum() rounds differently from the budget's running total (it compensates), so allow a nanodollar either way.
    ctx.usd_cap = sum(worst) + max(worst) + 1e-9
    # Fewer slots than batches, whatever the golden's scope size, so some batches wait for a slot.
    monkeypatch.setattr(mock, "PARALLEL_BATCHES", min(mock.PARALLEL_BATCHES, len(worst) - 1))
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
    assert 1 <= mock.PARALLEL_BATCHES < len(worst)
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
