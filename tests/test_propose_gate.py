"""PR 5's merge gate on each app's latest run. Produce the runs first, on the real profile the gate is defined on:
    uv run simula run APP --new --allow-fixtures --fixture model=tests/fixtures/golden/APP --from propose \
        --profile real
Propose needs only the product model (mock's contract report, when mock has run, only narrows the screens), so the
run needs no mock. The command goes on to the later stages; the gate reads only propose's outputs.
"""

import pytest

from simula import runfolder
from simula.contracts import CandidatesFile, LensesFile, ProductModel
from simula.runlog import read_trace
from simula.stages.propose import anchor_ids
from tests.conftest import APPS

pytestmark = pytest.mark.live


@pytest.fixture(params=APPS)
def run(request):
    run_dir = runfolder.RUNS / request.param / "latest"
    if not (run_dir / "propose" / "candidates.json").exists():
        pytest.fail(f"no propose output in {run_dir}; run the command in this file's docstring first")
    return run_dir


def test_merge_gate(run):
    model = ProductModel.model_validate_json((run / "model" / "product_model.json").read_text())
    lenses = LensesFile.model_validate_json((run / "propose" / "lenses.json").read_text()).lenses
    live = [c for c in CandidatesFile.model_validate_json((run / "propose" / "candidates.json").read_text()).candidates
            if not c.dropped_reason]
    answered = {t.step for t in read_trace(run / "trace.jsonl")
                if t.stage == "propose" and t.step.startswith("lens:") and t.outcome == "ok"}
    states = {s.id for s in model.states}
    elements = {e.id for s in model.states for e in s.elements}
    assert len(live) >= 4
    assert len(answered) >= 3
    assert all(set(c.anchor_evidence_ids) <= elements and c.trigger_state_id in states for c in live)
    assert all(c.economics and c.economics.assumption_line for c in live)
    if any(i.kind == "actor" for i in model.value_ledger):
        assert any(l.kind == "ledger" for l in lenses)
    if anchor_ids(model):
        assert {c.kind for c in live} == {"existing_anchor", "product_change"}
