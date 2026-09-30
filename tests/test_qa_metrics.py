"""QA's pixel and bounds metrics, checked offline before any loop runs on a real mock. Every test runs on all three
test apps; the identity render runs on the 5 screens the merge measured."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from simula import qa_metrics
from simula.contracts import Device, Rect
from simula.render import open_mock
from simula.stages import mock
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden
from tests.test_render import write_mock

IDENTITY_SCREENS = ["janitorai/j04_tab1", "janitorai/j01_launch", "janitorai/j07_tab4", "luzia/luzia-a", "aol/aol-a"]
SCALE = Device().scale


@pytest.fixture(params=APPS)
def real(request) -> Image.Image:
    """The app's root screen, content-cropped (1080x2201), as the model stage stores it."""
    root = mock.pick_scope(golden(request.param))[0]
    return Image.open(FIXTURES / "golden" / request.param / root.canonical_png).convert("RGB")


def shifted(image: Image.Image, px: int) -> Image.Image:
    """The image moved down by px device pixels, its top row repeated into the gap."""
    a = np.asarray(image)
    return Image.fromarray(np.concatenate([np.repeat(a[:1], px, axis=0), a[:-px]]))


def painted(image: Image.Image, box: Rect) -> Image.Image:
    """A copy with a content-dp box filled in the color furthest from what the box held."""
    device = [round(v * SCALE) for v in (box.x, box.y, box.x + box.w, box.y + box.h)]
    dark = np.asarray(image.crop(device)).mean() < 128
    out = image.copy()
    ImageDraw.Draw(out).rectangle(device, fill="white" if dark else "black")
    return out


def ssim(real: Image.Image, mock_image: Image.Image, masked: list[Rect] = ()) -> float | None:
    return qa_metrics.compare(real, mock_image, list(masked))["ssim"]


def test_an_identical_pair_scores_1(real):
    assert ssim(real, real.copy()) == pytest.approx(1.0)


def test_shifts_lower_ssim_and_the_drop_grows_with_the_shift(real):
    scores = [ssim(real, shifted(real, px)) for px in (1, 2, 5, 10)]
    assert 1.0 > scores[0] > scores[1] > scores[2] > scores[3]


def test_a_color_change_in_one_box_is_localized_to_that_box(real):
    box = Rect(x=150, y=300, w=80, h=40)
    result = qa_metrics.compare(real, painted(real, box), [])
    near_box = ~qa_metrics.unmasked([Rect(x=box.x - qa_metrics.HALO, y=box.y - qa_metrics.HALO,
                                          w=box.w + 2 * qa_metrics.HALO, h=box.h + 2 * qa_metrics.HALO)],
                                     result["map"].shape)
    hot = result["map"] < 0.5
    assert hot[round(box.y):round(box.y + box.h), round(box.x):round(box.x + box.w)].mean() > 0.5
    assert not (hot & ~near_box).any()
    assert result["map"][~near_box].mean() > 0.999


def test_copied_art_is_masked_so_a_wrong_button_beside_it_still_counts(real):
    art = Rect(x=0, y=0, w=411, h=500)
    button = Rect(x=40, y=640, w=120, h=48)
    wrong = painted(real, button)
    whole, masked = ssim(real, wrong), ssim(real, wrong, [art])
    assert masked < whole < 1.0
    assert 1 - masked > 2 * (1 - whole)


def test_art_covering_90_percent_earns_no_pixel_score(real):
    art = Rect(x=0, y=0, w=411, h=755)
    result = qa_metrics.compare(real, real.copy(), [art])
    assert result["coverage"] < qa_metrics.MIN_COVERAGE
    assert result["ssim"] is None


def test_a_heatmap_is_content_dp_and_marks_the_changed_box(real):
    box = Rect(x=150, y=300, w=80, h=40)
    result = qa_metrics.compare(real, painted(real, box), [])
    heat = np.asarray(qa_metrics.heatmap(real, result["map"], result["keep"])).astype(int)
    assert heat.shape == (838, 411, 3)
    inside, outside = heat[310:330, 160:220], heat[600:700, 20:100]
    assert (inside[..., 0] - inside[..., 1]).mean() > 150
    assert (outside[..., 0] - outside[..., 1]).mean() < 20


@pytest.mark.parametrize("name", IDENTITY_SCREENS)
def test_identity_render_passes_the_gate(name):
    real = Image.open(FIXTURES / "trees" / f"{name}.png")
    rendered = qa_metrics.identity_render(real)
    assert rendered.size == (1079, 2399)
    assert ssim(real, rendered) >= qa_metrics.IDENTITY_GATE


@pytest.mark.parametrize("app", APPS)
def test_bounds_round_trip_within_1_dp(tmp_path, app):
    model = golden(app)
    mock_dir, screens = write_mock(tmp_path, model)
    checked = 0
    with open_mock(mock_dir) as (page, _):
        for state in mock.pick_scope(model):
            page.evaluate("id => window.simula.go(id)", state.id)
            boxes, _ = qa_metrics.screen_dom(page, state.id)
            for e in state.elements:
                if e.id in mock.tagged_ids(state) and e.rect_dp.w > 0 and e.rect_dp.h > 0:
                    assert qa_metrics.within(boxes.get(e.id), e.rect_dp, tolerance=1.0), e.id
                    checked += 1
    assert checked


def test_dynamic_regions_move_from_device_px_to_content_dp():
    device = Device()
    region = Rect(x=0, y=device.content_top_px + 262.5, w=1080, h=525)
    assert qa_metrics.device_to_dp(region, device) == Rect(x=0, y=100, w=1080 / 2.625, h=200)
