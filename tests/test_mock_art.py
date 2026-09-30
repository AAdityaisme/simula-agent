"""Stage 3 reuses the real pictures painted inside containers: found by code, cropped from the state's screenshot."""

import json

import numpy as np
import pytest
from PIL import Image

from simula import llm
from simula.contracts import ContractReport, Element, ProductModel, Rect
from simula.render import render_and_validate
from simula.stages import mock
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import ctx_for, fake_builder

FLAT = "#223344"


@pytest.fixture(params=APPS)
def app(request):
    return request.param


def element(eid: str, x: float, y: float, w: float, h: float, text: str = "", asset: str | None = None) -> Element:
    r = Rect(x=x, y=y, w=w, h=h)
    return Element(id=eid, mcp_ref=None, type="View", text=text, label="", source="mcp", rect_px=r, rect_dp=r,
                   role="container", asset_png=asset, fg_hex=None, bg_hex=None, font_px=None, font_guess="unknown",
                   in_mock=True, repeat_group=None)


def screenshot(*pictures: Rect) -> Image.Image:
    """A flat content-area screenshot with noise painted over each picture rect."""
    device = golden("janitorai").device
    image = Image.new("RGB", (device.w_px, device.content_bottom_px - device.content_top_px), FLAT)
    noise = np.random.default_rng(0).integers(0, 256, (image.height, image.width, 3), dtype=np.uint8)
    for r in pictures:
        box = tuple(round(v * device.scale) for v in (r.x, r.y, r.x + r.w, r.y + r.h))
        image.paste(Image.fromarray(noise).crop(box), box[:2])
    return image


def art_in(app: str, elements: list[Element], image: Image.Image) -> dict[str, Rect]:
    model = golden(app)
    state = model.states[0].model_copy(update={"elements": elements})
    return mock.find_art(state, image, model.device)


def overlaps(a: Rect, b: Rect) -> bool:
    return mock.area(mock.overlap(a, b)) > 0


def test_the_art_is_the_picture_between_the_text_rows(app):
    card, title, caption = Rect(x=20, y=100, w=200, h=240), Rect(x=20, y=100, w=200, h=24), Rect(x=20, y=310, w=200, h=30)
    elements = [element("s01.e01", **card.model_dump()), element("s01.e02", **title.model_dump(), text="Title"),
                element("s01.e03", **caption.model_dump(), text="caption")]
    art = art_in(app, elements, screenshot(card))
    assert art == {"s01.e01": Rect(x=20, y=124, w=200, h=186)}
    assert not overlaps(art["s01.e01"], title) and not overlaps(art["s01.e01"], caption)


def test_a_small_picture_is_found_beside_a_larger_flat_area(app):
    row, picture = Rect(x=20, y=100, w=380, h=90), Rect(x=26, y=106, w=70, h=70)
    elements = [element("s01.e01", **row.model_dump()), element("s01.e02", 100, 110, 200, 20, text="Name"),
                element("s01.e03", 100, 140, 100, 20, text="1 chat")]
    assert art_in(app, elements, screenshot(picture)) == {"s01.e01": Rect(x=20, y=100, w=80, h=90)}


def test_a_text_element_never_gets_art_even_when_its_line_is_picture_like(app):
    line = Rect(x=16, y=158, w=380, h=80)
    image = screenshot(line)
    assert mock.is_picture(mock.crop_px(image, line, golden(app).device.scale))
    assert art_in(app, [element("s01.e01", **line.model_dump(), text="Good night \U0001F319\u2728\U0001F496\U0001F60D")], image) == {}


def test_a_flat_container_has_no_art(app):
    elements = [element("s01.e01", 20, 100, 200, 240), element("s01.e02", 20, 100, 200, 24, text="Title")]
    assert art_in(app, elements, screenshot()) == {}


def test_art_of_any_size_is_found_when_no_words_or_image_sit_on_it(app):
    picture = Rect(x=0, y=24, w=411, h=676)
    elements = [element("s01.e01", 0, 0, 411, 700), element("s01.e02", 0, 0, 411, 24, text="Title")]
    assert not mock.under_wallpaper_limit(picture, golden(app).device)
    assert art_in(app, elements, screenshot(Rect(x=0, y=0, w=411, h=700))) == {"s01.e01": picture}


def test_a_wordless_container_the_mock_would_not_draw_still_gets_its_art(app):
    card = Rect(x=20, y=100, w=200, h=240)
    undrawn = element("s01.e01", **card.model_dump()).model_copy(update={"in_mock": False})
    assert art_in(app, [undrawn], screenshot(card)) == {"s01.e01": card}


def test_a_big_asset_is_usable_only_when_no_other_words_or_image_sit_on_it(app):
    device = golden(app).device
    hero = element("s01.e01", 0, 0, 411, 600, asset="assets/s01.e01.png")
    caption = element("s01.e02", 20, 500, 200, 30, text="caption")
    icon = element("s01.e03", 20, 20, 48, 48, asset="assets/s01.e03.png")
    assert not mock.under_wallpaper_limit(hero.rect_dp, device)
    assert mock.usable_asset(hero, [hero], device)
    assert not mock.usable_asset(hero, [hero, caption], device)
    assert not mock.usable_asset(hero, [hero, icon], device)
    edge_caption = element("s01.e06", 300, 580, 200, 40, text="half on the picture")
    assert not mock.usable_asset(hero, [hero, edge_caption], device)
    card = element("s01.e07", 0, 0, 411, 800).model_copy(update={"label": "character card"})
    assert mock.usable_asset(hero, [card, hero], device)
    thumb = element("s01.e04", 0, 0, 200, 200, asset="assets/s01.e04.png")
    assert mock.usable_asset(thumb, [thumb, element("s01.e05", 10, 10, 50, 20, text="x")], device)
    assert not mock.usable_asset(hero.model_copy(update={"in_mock": False}), [hero], device)


@pytest.mark.parametrize("grow", [0, 1])
def test_a_region_an_asset_already_covers_is_skipped(app, grow):
    card = Rect(x=20, y=100, w=200, h=240)
    elements = [element("s01.e01", **card.model_dump()), element("s01.e02", 20, 100, 200, 24, text="Title")]
    image = screenshot(card)
    assert art_in(app, elements, image)
    asset = element("s01.e03", card.x - grow, card.y - grow, card.w + 2 * grow, card.h + 2 * grow, asset="assets/s01.e03.png")
    assert art_in(app, [asset] + elements, image) == {}


def test_every_golden_crop_is_a_picture_clear_of_text(tmp_path, app):
    model = golden(app)
    scope = mock.pick_scope(model)
    mock.copy_assets(FIXTURES / "golden" / app, tmp_path, scope, model.device)
    art = mock.crop_art(FIXTURES / "golden" / app, tmp_path, scope, model.device)
    listed = {mock.art_src(eid): r.model_dump() for eid, r in art.items()}
    assert json.loads((tmp_path / "art.json").read_text()) == {"schema_version": 1, "art": listed}
    elements = {e.id: e for s in scope for e in s.elements}
    for eid, rect in art.items():
        container = elements[eid]
        assert mock.contains(container.rect_dp, rect) and min(rect.w, rect.h) >= mock.ART_MIN_SIDE
        state = next(s for s in scope if container in s.elements)
        assert not any(overlaps(o.rect_dp, rect) for o in state.elements if mock.drawn_over(o, container))
        crop = Image.open(tmp_path / mock.art_src(eid))
        assert crop.size == (round((rect.x + rect.w) * model.device.scale) - round(rect.x * model.device.scale),
                             round((rect.y + rect.h) * model.device.scale) - round(rect.y * model.device.scale))


def test_the_brief_offers_art_on_its_element_and_in_image_files(app):
    model = golden(app)
    scope = mock.pick_scope(model)
    target = next(e for s in scope for e in s.elements if e.in_mock and not e.asset_png)
    rect = Rect(x=target.rect_dp.x, y=target.rect_dp.y, w=48, h=48)
    screens = [s.id for s in scope]
    data = json.loads(mock.brief(model, scope, screens, {target.id: rect}).split("\n\n", 1)[1])
    entry = next(e for s in data["screens"] for e in s["elements"] if e["id"] == target.id)
    assert entry["art"] == {"src": mock.art_src(target.id), "rect": rect.model_dump()}
    assert mock.art_src(target.id) in data["image_files"]


def test_image_files_lists_every_art_file_the_stage_wrote(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    calls = []
    monkeypatch.setattr(llm, "call", fake_builder(calls))
    mock.run(ctx_for(run_dir, app))
    briefs = [json.loads(call["messages"][0]["content"][-1]["text"].split("\n\n", 1)[1]) for call in calls]
    image_files = [src for data in briefs for src in data["image_files"]]
    art = json.loads((run_dir / "mock" / "art.json").read_text())["art"]
    assert set(art) <= set(image_files)
    assert all((run_dir / "mock" / src).exists() for src in image_files)
    offered = {e["art"]["src"]: e["art"]["rect"] for data in briefs for s in data["screens"] for e in s["elements"]
               if "art" in e}
    assert offered == art


def test_the_contract_check_accepts_an_art_src(tmp_path, app):
    model = golden(app)
    scope = mock.pick_scope(model)
    mock_dir = tmp_path / "mock"
    mock.copy_assets(FIXTURES / "golden" / app, mock_dir, scope, model.device)
    src = mock.art_src(f"{scope[0].id}.e01")
    Image.new("RGB", (126, 126), FLAT).save(mock_dir / src)
    img = f'<img src="{src}" style="position:absolute;left:0;top:0;width:48px;height:48px">'
    html = skeleton_html(model).replace("</section>", img + "</section>", 1)
    (mock_dir / "index.html").write_text(mock.with_runtime(html, scope[0].id))
    report = render_and_validate(mock_dir, model, [s.id for s in scope])
    assert report.passed, report.errors


def test_the_brief_lists_a_container_the_mock_would_not_draw_only_when_it_has_art(app):
    model = golden(app)
    state = mock.pick_scope(model)[0]
    target = next(e for e in state.elements if not e.text and not e.asset_png)
    state = state.model_copy(update={"elements": [e.model_copy(update={"in_mock": False}) if e.id == target.id else e
                                                  for e in state.elements]})
    rect = Rect(x=target.rect_dp.x, y=target.rect_dp.y, w=48, h=48)
    entry = next(e for e in mock.state_brief(state, model.device, {target.id: rect})["elements"] if e["id"] == target.id)
    assert entry["art"] == {"src": mock.art_src(target.id), "rect": rect.model_dump()} and entry["tag"] is False
    assert target.id not in {e["id"] for e in mock.state_brief(state, model.device, {})["elements"]}


def wallpaper_errors(tmp_path, model, img: str, section: int = 0) -> list:
    """Validates the golden's skeleton page with img added to the section-th screen; returns its wallpaper errors."""
    scope = mock.pick_scope(model)
    mock_dir = tmp_path / "mock"
    mock.copy_assets(FIXTURES / "golden" / model.app, mock_dir, scope, model.device)
    html = skeleton_html(model)
    closing = [i for i in range(len(html)) if html.startswith("</section>", i)][section]
    html = html[:closing] + img + html[closing:]
    (mock_dir / "index.html").write_text(mock.with_runtime(html, scope[0].id))
    report = render_and_validate(mock_dir, model, [s.id for s in scope], crops=mock.crop_origins(model, mock_dir))
    return [e for e in report.errors if e.kind == "wallpaper"]


def write_art(tmp_path, eid: str, rect: Rect) -> str:
    (tmp_path / "mock" / "assets").mkdir(parents=True, exist_ok=True)
    src = mock.art_src(eid)
    Image.new("RGB", (100, 100), FLAT).save(tmp_path / "mock" / src)
    (tmp_path / "mock" / "art.json").write_text(json.dumps({"schema_version": 1, "art": {src: rect.model_dump()}}))
    return src


@pytest.mark.parametrize("left, passes", [(0, True), (3, True), (30, False)])
def test_a_big_art_crop_passes_only_over_the_rect_it_was_cut_from(tmp_path, app, left, passes):
    model = golden(app)
    first = mock.pick_scope(model)[0]
    src = write_art(tmp_path, first.elements[0].id, Rect(x=0, y=0, w=411, h=600))
    img = f'<img src="{src}" style="position:absolute;left:{left}px;top:0;width:411px;height:600px">'
    errors = wallpaper_errors(tmp_path, model, img)
    assert (errors == []) == passes
    assert passes or {e.screen for e in errors} == {first.id}


def test_a_big_art_crop_drawn_on_another_screen_is_wallpaper(tmp_path, app):
    model = golden(app)
    scope = mock.pick_scope(model)
    src = write_art(tmp_path, scope[0].elements[0].id, Rect(x=0, y=0, w=411, h=600))
    img = f'<img src="{src}" style="position:absolute;left:0;top:0;width:411px;height:600px">'
    assert {e.screen for e in wallpaper_errors(tmp_path, model, img, section=1)} == {scope[1].id}


@pytest.mark.parametrize("holds_text", [False, True])
def test_a_big_element_asset_is_wallpaper_exactly_when_words_sit_inside_it(tmp_path, app, holds_text):
    model = golden(app)
    first = mock.pick_scope(model)[0]
    hero = element(f"{first.id}.e900", 0, 0, 411, 600, asset=f"assets/{first.id}.e900.png")
    hero = hero.model_copy(update={"in_mock": False})
    extra = [hero] + ([element(f"{first.id}.e901", 20, 500, 200, 30, text="caption")] if holds_text else [])
    states = [s.model_copy(update={"elements": extra}) if s.id == first.id else s for s in model.states]
    model = model.model_copy(update={"states": states})
    (tmp_path / "mock" / "assets").mkdir(parents=True)
    Image.new("RGB", (100, 100), FLAT).save(tmp_path / "mock" / hero.asset_png)
    img = f'<img src="{hero.asset_png}" style="position:absolute;left:0;top:0;width:411px;height:600px">'
    assert bool(wallpaper_errors(tmp_path, model, img)) == holds_text


def test_the_mock_stage_lets_a_big_clean_asset_through_at_its_rect(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    path = run_dir / "model" / "product_model.json"
    model = ProductModel.model_validate_json(path.read_text())
    first = mock.pick_scope(model)[0]
    hero = element(f"{first.id}.e900", 0, 0, 411, 600, asset=f"assets/{first.id}.e900.png")
    states = [s.model_copy(update={"elements": [hero]}) if s.id == first.id else s for s in model.states]
    path.write_text(model.model_copy(update={"states": states}).model_dump_json())
    Image.new("RGB", (100, 100), FLAT).save(run_dir / "model" / hero.asset_png)
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))
    report = ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text())
    assert (run_dir / "mock" / hero.asset_png).exists()
    assert not [e for e in report.errors if e.kind == "wallpaper"]
