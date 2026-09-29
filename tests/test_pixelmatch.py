"""simula.pixelmatch counts exactly the pixels pixelmatch-py 0.4.0 does: on real screens from all three test apps
(identity renders, shifts, a changed box) and on small random images full of equal colors, ties and edges."""

import numpy as np
import pytest
from PIL import Image
from pixelmatch.contrib.PIL import pixelmatch as reference

from simula import pixelmatch, qa_metrics
from simula.contracts import Rect
from simula.render import content_dp
from simula.stages import mock
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden
from tests.test_qa_metrics import IDENTITY_SCREENS, painted, shifted


def assert_same_pixels(a: Image.Image, b: Image.Image, threshold: float = qa_metrics.PIXELMATCH_THRESHOLD) -> None:
    out = Image.new("RGBA", a.size)
    count = reference(a, b, out, threshold=threshold, includeAA=False, diff_mask=True)
    want = (np.asarray(out) == (255, 0, 0, 255)).all(axis=-1)
    got = pixelmatch.differing(np.asarray(a), np.asarray(b), threshold)
    assert int(want.sum()) == count
    assert np.array_equal(got, want), f"{int((got != want).sum())} of {got.size} pixels disagree with pixelmatch-py"


@pytest.mark.parametrize("app", APPS)
def test_a_real_screen_shifted_or_changed_differs_where_the_reference_says(app):
    real = Image.open(FIXTURES / "golden" / app / mock.pick_scope(golden(app))[0].canonical_png).convert("RGB")
    for other in (shifted(real, 1), shifted(real, 10), painted(real, Rect(x=150, y=300, w=80, h=40))):
        assert_same_pixels(content_dp(real), content_dp(other))


@pytest.mark.parametrize("name", IDENTITY_SCREENS)
def test_an_identity_render_differs_where_the_reference_says(name):
    real = Image.open(FIXTURES / "trees" / f"{name}.png")
    assert_same_pixels(content_dp(real), content_dp(qa_metrics.identity_render(real)))


@pytest.mark.parametrize("seed", range(6))
def test_small_random_images_with_ties_and_edges_match_the_reference(seed):
    """Six colors on a 31 x 23 grid: many equal neighbours, darkest and brightest ties, and a large share of edge
    pixels, which is where a port slips."""
    rng = np.random.default_rng(seed)
    palette = rng.integers(0, 256, (6, 3), dtype=np.uint8)
    a = palette[rng.integers(0, 6, (23, 31))]
    b = a.copy()
    changed = rng.random((23, 31)) < 0.3
    b[changed] = palette[rng.integers(0, 6, int(changed.sum()))]
    for threshold in (0.05, 0.1, 0.3):
        assert_same_pixels(Image.fromarray(a), Image.fromarray(b), threshold)
    assert not pixelmatch.differing(a, a.copy(), 0.1).any()
