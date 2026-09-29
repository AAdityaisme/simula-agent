import pytest

from simula.contracts import LedgerItem
from simula.stages.propose import build_lenses
from tests.conftest import APPS
from tests.propose_fixtures import golden


@pytest.fixture(params=APPS)
def model(request):
    return golden(request.param)


def test_three_fixed_lenses_always(model):
    fixed = [l for l in build_lenses(model) if l.kind == "fixed"]
    assert [l.id for l in fixed] == ["free_at_limit", "subscriber", "drifting"]


def test_a_ledger_lens_for_each_actor(model):
    actors = [i.id for i in model.value_ledger if i.kind == "actor"]
    ledger = [l for l in build_lenses(model) if l.kind == "ledger"]
    assert [l.ledger_ids for l in ledger] == [[i] for i in actors[:2]]
    if actors:
        assert ledger, "the ledger has an actor, so a lens must come from it"


def test_at_most_two_ledger_lenses(model):
    extra = [LedgerItem(id=f"x{n}", kind="actor", verbatim=f"actor {n}", evidence_ids=[]) for n in range(3)]
    crowded = model.model_copy(update={"value_ledger": model.value_ledger + extra})
    lenses = build_lenses(crowded)
    assert len(lenses) <= 5 and sum(l.kind == "ledger" for l in lenses) == 2
