"""Settle: two lists >= 1.5 s apart with the same skeleton while the list call is fast, then two small
screenshots within 4 bits; unsettled after 15 s."""

import json
from pathlib import Path

from simula.contracts import Device
from simula.device.mcp import parse_elements
from simula.device.observe import settle
from tests.fake_device import Clock

TREES = Path(__file__).parent / "fixtures" / "trees"
LOADING = parse_elements(json.loads((TREES / "janitorai" / "jr1.elements.json").read_text()))
LOADED = parse_elements(json.loads((TREES / "janitorai" / "janitorai-a.elements.json").read_text()))


def fake(trees, list_took, hashes, clock):
    trees, hashes = list(trees), list(hashes)

    def list_elements():
        clock.t += list_took(clock.t)
        tree = trees.pop(0) if len(trees) > 1 else trees[0]
        return {}, tree

    def small_hash():
        clock.t += 0.2
        return hashes.pop(0) if len(hashes) > 1 else hashes[0]
    return list_elements, small_hash


def test_waits_for_the_skeleton_to_stop_changing():
    clock = Clock()
    lists, shots = fake([LOADING, LOADED, LOADED], lambda t: 0.4, [0b1011], clock)
    result = settle(lists, shots, Device(), clock, clock.sleep)
    assert result.ok and result.elements is LOADED and len(result.list_seconds) == 3
    assert 3.0 <= result.seconds < 6.0


def test_a_slow_list_call_means_the_app_is_still_busy():
    clock = Clock()
    lists, shots = fake([LOADED], lambda t: 3.0 if t < 8 else 0.4, [0], clock)
    result = settle(lists, shots, Device(), clock, clock.sleep)
    assert result.ok and result.seconds > 8 and max(result.list_seconds) >= 3.0


def test_moving_pixels_keep_it_unsettled():
    clock = Clock()
    lists, shots = fake([LOADED], lambda t: 0.4, [0, 0xFFFF] * 40, clock)
    result = settle(lists, shots, Device(), clock, clock.sleep, max_s=15.0)
    assert not result.ok and 15.0 <= result.seconds < 18.0


def test_settled_pixels_within_four_bits_pass():
    clock = Clock()
    lists, shots = fake([LOADED], lambda t: 0.4, [0b0000, 0b1111], clock)
    assert settle(lists, shots, Device(), clock, clock.sleep).ok
