"""Step 4a's arrival calibration, recomputed on every run: the model's recorded verdicts on labeled screen pairs from
all three test apps, with the structure and the identifying-text check computed now. A change to either that moves a
count fails here. Labels follow Q6 and Aadi's ruling: a scrolled page, and a chip set on the same tab, are the same
screen."""

import json
from pathlib import Path

import pytest

from simula.contracts import Device, Rect
from simula.device import observe as ob
from simula.device.mcp import parse_elements

FIX = Path(__file__).parent / "fixtures"
PAIRS = json.loads((FIX / "arrival" / "pairs.json").read_text())
DEVICE = Device()


def elements(name: str) -> list[dict]:
    return parse_elements(json.loads((FIX / f"{name}.elements.json").read_text()))


def judged(row: dict) -> tuple[float, bool]:
    """The structure now, and whether the explorer's rule accepts arrival given the model's recorded answer."""
    now = elements(row["now"])
    structure = ob.structure(elements(row["target"]), now, DEVICE, [Rect(**d) for d in row["target_dynamic"]])
    read = not row["identifying_text"].strip() or ob.shows_text(now, row["identifying_text"], DEVICE)
    return structure, row["model"] == "same" and read and structure >= ob.STRUCTURE_SAME


@pytest.fixture(scope="module")
def results() -> list[tuple[dict, float, bool]]:
    return [(row, *judged(row)) for row in PAIRS]


def test_the_structure_is_what_step_4a_measured(results):
    assert all(abs(structure - row["structure"]) < 0.001 for row, structure, _ in results)


@pytest.mark.parametrize("app, same, kept, false_same", [("janitorai", 29, 28, 1), ("luzia", 12, 12, 0),
                                                         ("aol", 28, 26, 0)])
def test_arrival_per_app(results, app, same, kept, false_same):
    mine = [(row["truth"] == "same", arrived) for row, _, arrived in results if row["app"] == app]
    assert (sum(t for t, _ in mine), sum(t and a for t, a in mine), sum(a and not t for t, a in mine)) \
        == (same, kept, false_same)


def test_the_threshold_is_the_highest_that_keeps_every_true_pair_the_model_called_same(results):
    lowest = min(structure for row, structure, _ in results if row["truth"] == "same" and row["model"] == "same")
    assert ob.STRUCTURE_SAME <= lowest < ob.STRUCTURE_SAME + 0.01


def test_two_items_of_one_layout_never_arrive_though_the_structure_alone_would(results):
    template = [(structure, arrived) for row, structure, arrived in results if row["same_template"]]
    assert len(template) == 9 and not any(arrived for _, arrived in template)
    assert sum(structure >= ob.STRUCTURE_SAME for structure, _ in template) == 8
