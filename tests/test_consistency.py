"""The cross-screen check: shared chrome (data-chrome) renders the same on every screen that draws it, and a shared
value (data-value) reads the same wherever it appears. Its failures reach the critic and the fixer like any QA
finding, never enter the score, and leave the approved version qa_incomplete. Stage-level tests run on all three
test apps."""

import json
import re
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from simula import llm, qa_metrics, render
from simula.contracts import ContractReport, Critique, CrossScreenValue, Fix, QAMetrics, Rect
from simula.stages import mock, qa
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import ctx_for
from tests.test_qa_loop import OPTIONS, fake_critic, measured_twelve, records, told, twelve  # noqa: F401 (fixtures)

BAR_DP = 56
BAR = "#20252b"
BAND_DP = 80  # the real screens' shared band is taller than the mock's bar, so resampling at its edge stays outside
# Band colors that each differ from every other by most of a channel, so no two bands look alike to SSIM.
PALETTE = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 0, 255), (0, 160, 0), (255, 200, 0), (120, 0, 160),
           (0, 200, 200)]


def run_with(tmp_path, app: str, model, edit) -> tuple:
    """A run of the app's golden (model as given) whose mock is the offline builder's page changed by edit, as stage 3
    would leave it. Returns the run folder, the scope, and the page."""
    run_dir = seed_model(tmp_path / "run", app)
    (run_dir / "model" / "product_model.json").write_text(model.model_dump_json())
    scope = mock.pick_scope(model)
    ids = [s.id for s in scope]
    mock.copy_assets(run_dir / "model", run_dir / "mock", scope, model.device)
    html = qa.rebuild(edit(skeleton_html(model)), model, ids)
    (run_dir / "mock" / "index.html").write_text(html)
    (run_dir / "mock" / "contract_report.json").write_text(
        ContractReport(passed=True, screens=ids, errors=[]).model_dump_json())
    return run_dir, scope, html


def at_top(html: str, tags: dict[str, str]) -> str:
    """Adds a tag at the start of each named screen's section."""
    return re.sub(r'(<section data-screen="([^"]+)"[^>]*>)', lambda m: m[1] + tags.get(m[2], ""), html)


def bars(model, colors: dict[str, str] = {}) -> dict[str, str]:
    """A tab bar marked data-chrome on every in-scope screen, the same unless colors gives a screen its own."""
    def bar(color: str) -> str:
        return (f'<nav data-chrome="tabbar" style="position:absolute;left:0;top:{838.2 - BAR_DP}px;width:411px;'
                f"height:{BAR_DP}px;background:{color};color:#fff;z-index:2;pointer-events:none;display:flex;"
                f'justify-content:space-around;align-items:center"><span>Home</span><span>Chats</span>'
                f"<span>You</span></nav>")
    return {s.id: bar(colors.get(s.id, BAR)) for s in mock.pick_scope(model)}


def share_band(run_dir, model, own_colors: bool = False) -> None:
    """Gives every in-scope real screen one bottom band in the color bars() draws (the real app draws one bar on every
    screen, the one the mock draws), or with own_colors, a band of its own color on each (the app draws its bar
    differently on every screen)."""
    for i, s in enumerate(mock.pick_scope(model)):
        path = run_dir / "model" / s.canonical_png
        image = Image.open(path).convert("RGB")
        image.paste(PALETTE[i] if own_colors else BAR,
                    (0, image.height - round(BAND_DP * model.device.scale), image.width, image.height))
        image.save(path)


def chrome(version: qa.Version) -> list[dict]:
    return [f for f in version.cross_screen if f["kind"] == "chrome"]


# ---------- data-chrome ----------

@pytest.mark.parametrize("app", APPS)
def test_a_tab_bar_drawn_differently_on_one_screen_fails_on_that_screen_only(tmp_path, app):
    model = golden(app)
    scope = mock.pick_scope(model)
    last = scope[-1].id
    run_dir, scope, html = run_with(tmp_path, app, model, lambda h: at_top(h, bars(model, {last: "#b03060"})))
    share_band(run_dir, model)
    version = qa.measure(ctx_for(run_dir, app), model, scope, 0, html)
    assert [f["screen"] for f in chrome(version)] == [last]
    assert all(s.id in chrome(version)[0]["detail"] for s in scope)
    assert version.metrics.cross_screen_failures == [f["detail"] for f in version.cross_screen]


@pytest.mark.parametrize("app", APPS)
def test_a_screen_that_leaves_the_tab_bar_off_fails_though_it_carries_no_mark(tmp_path, app):
    model = golden(app)
    last = mock.pick_scope(model)[-1].id
    run_dir, scope, html = run_with(tmp_path, app, model, lambda h: at_top(h, {**bars(model), last: ""}))
    share_band(run_dir, model)
    failures = chrome(qa.measure(ctx_for(run_dir, app), model, scope, 0, html))
    assert [f["screen"] for f in failures] == [last]
    assert f'{last} has no data-chrome="tabbar"' in failures[0]["detail"]
    assert f"draw it as {scope[0].id} does" in failures[0]["detail"]
    assert failures[0]["detail"].endswith('marked data-chrome="tabbar"')


@pytest.mark.parametrize("app", APPS)
def test_a_tab_bar_drawn_over_the_content_on_one_screen_fails_on_that_screen(app):
    """The app draws one bar at the bottom of every screen; the last mock screen draws it over its content instead, and
    shows what its own screenshot shows at the bottom. Every other mock screen is its real screen, so only the
    misplaced bar decides, though the bar's own box holds different content on every real screen."""
    screens = real_screens(app)
    real = {sid: banded(image) for sid, image in screens.items()}
    last = list(real)[-1]
    bottom = Rect(x=0, y=838 - BAR_DP, w=411, h=BAR_DP)
    over = Rect(x=0, y=300, w=411, h=BAR_DP)
    moved = screens[last].copy()
    ImageDraw.Draw(moved).rectangle((0, over.y, 410, over.y + BAR_DP - 1), fill=BAR)
    chrome = {sid: {"tabbar": over if sid == last else bottom} for sid in real}
    assert [f["screen"] for f in qa_metrics.chrome_failures(chrome, {**real, last: moved}, real)] == [last]


def banded(image: Image.Image) -> Image.Image:
    """A content-dp real screen with the shared bottom band share_band paints."""
    out = image.copy()
    ImageDraw.Draw(out).rectangle((0, 838 - BAND_DP, 410, 837), fill=BAR)
    return out


@pytest.mark.parametrize("app", APPS)
def test_the_same_tab_bar_on_every_screen_passes(tmp_path, app):
    model = golden(app)
    run_dir, scope, html = run_with(tmp_path, app, model, lambda h: at_top(h, bars(model)))
    share_band(run_dir, model)
    assert chrome(qa.measure(ctx_for(run_dir, app), model, scope, 0, html)) == []


@pytest.mark.parametrize("app", APPS)
def test_a_bar_the_real_app_draws_differently_on_every_screen_is_not_held_to_one_look(tmp_path, app):
    """A highlighted tab or a screen title differs between real screens too; the mock may differ there."""
    model = golden(app)
    last = mock.pick_scope(model)[-1].id
    run_dir, scope, html = run_with(tmp_path, app, model, lambda h: at_top(h, bars(model, {last: "#b03060"})))
    share_band(run_dir, model, own_colors=True)
    assert chrome(qa.measure(ctx_for(run_dir, app), model, scope, 0, html)) == []


@pytest.mark.parametrize("app", APPS)
def test_the_screen_whose_bar_differs_from_its_own_real_screen_is_the_one_to_fix_even_when_it_comes_first(app):
    real = render.content_dp(Image.open(FIXTURES / "golden" / app / mock.pick_scope(golden(app))[0].canonical_png))
    odd = real.copy()
    ImageDraw.Draw(odd).rectangle((0, 780, 411, 838), fill="#b03060")
    box = Rect(x=0, y=780, w=411, h=58)
    parts = {sid: {"tabbar": box} for sid in ("a", "b", "c")}
    failures = qa_metrics.chrome_failures(parts, {"a": odd, "b": real, "c": real}, dict.fromkeys(parts, real))
    assert [f["screen"] for f in failures] == ["a"] and "from b, c" in failures[0]["detail"]


def copies(failures: list[dict]) -> dict[str, str]:
    """Each flagged screen and the screen it is told to draw its part like."""
    return {f["screen"]: f["detail"].split("draw it as ")[1].split(" ")[0] for f in failures}


def test_a_screen_drawn_as_the_app_draws_it_is_never_told_to_change_though_most_screens_are_drawn_wrong():
    """s01 and s02 draw the bar wrong the same way and agree with each other; s03 draws it as the app does."""
    screen = Image.new("RGB", (411, 838), "#101014")
    right, wrong = with_tab_bar(screen, lit=0), with_tab_bar(screen, lit=0, fill="#3a2030")
    box = Rect(x=0, y=780, w=411, h=58)
    mocks = {"s01": wrong, "s02": wrong, "s03": right}
    failures = qa_metrics.chrome_failures({sid: {"tabbar": box} for sid in mocks}, mocks, dict.fromkeys(mocks, right))
    assert copies(failures) == {"s01": "s03", "s02": "s03"}


def test_of_two_screens_the_one_drawn_wrong_is_told_to_change_whichever_comes_first():
    screen = Image.new("RGB", (411, 838), "#101014")
    right, wrong = with_tab_bar(screen, lit=0), with_tab_bar(screen, lit=0, fill="#3a2030")
    box = Rect(x=0, y=780, w=411, h=58)
    for mocks in ({"s01": wrong, "s02": right}, {"s01": right, "s02": wrong}):
        failures = qa_metrics.chrome_failures({sid: {"tabbar": box} for sid in mocks}, mocks,
                                              dict.fromkeys(mocks, right))
        odd = next(sid for sid, image in mocks.items() if image is wrong)
        assert copies(failures) == {odd: next(sid for sid in mocks if sid != odd)}


def test_a_bar_the_app_itself_resizes_passes_when_the_mock_resizes_it_the_same():
    """Mock and real are compared over the same box: at the taller bar's box the resize costs both the same."""
    screen = Image.new("RGB", (411, 838), "#101014")
    def shorter(fill: str = "#202226") -> Image.Image:
        bar = with_tab_bar(screen, lit=0, fill=fill)
        ImageDraw.Draw(bar).rectangle((0, 780, 410, 785), fill="#101014")
        return bar
    real = {"a": with_tab_bar(screen, lit=0), "b": shorter()}
    parts = {"a": {"tabbar": Rect(x=0, y=780, w=411, h=58)}, "b": {"tabbar": Rect(x=0, y=786, w=411, h=52)}}
    assert qa_metrics.SAME_PART <= alike_share(real["a"], real["b"], parts["a"]["tabbar"]) < 1
    assert qa_metrics.chrome_failures(parts, real, real) == []
    recolored = {**real, "b": shorter(fill="#3a2030")}
    assert [f["screen"] for f in qa_metrics.chrome_failures(parts, recolored, real)] == ["b"]


def test_the_screen_to_copy_is_the_one_drawn_closest_to_its_real_screen_never_another_odd_one():
    """b and c are each drawn their own way; d, e and f draw the bar as the app does. b and c copy d, never each
    other."""
    screen = Image.new("RGB", (411, 838), "#101014")
    real = with_tab_bar(screen, lit=0)
    box = Rect(x=0, y=780, w=411, h=58)
    mocks = {"b": with_tab_bar(screen, lit=0, fill="#3a2030"), "c": with_tab_bar(screen, lit=0, fill="#203a30"),
             "d": real, "e": real, "f": real}
    failures = qa_metrics.chrome_failures({sid: {"tabbar": box} for sid in mocks}, mocks, dict.fromkeys(mocks, real))
    assert copies(failures) == {"b": "d", "c": "d"}


def test_a_screen_with_a_failure_of_its_own_is_never_the_one_to_copy_though_it_scores_highest():
    """a draws the bar in the wrong color. b draws a red icon where the app highlights a's tab, so the pair a-b never
    compares it and b scores a perfect 1 there, but b fails against c. c draws one icon a shade off. a copies c."""
    screen = Image.new("RGB", (411, 838), "#101014")
    real = {"a": with_tab_bar(screen, lit=0), "b": with_tab_bar(screen, lit=1), "c": with_tab_bar(screen, lit=2)}
    red_icon, off_icon = real["b"].copy(), real["c"].copy()
    ImageDraw.Draw(red_icon).rectangle((28, 786, 74, 832), fill="#ff0000")
    ImageDraw.Draw(off_icon).rectangle((328, 786, 374, 832), fill="#7a7a7a")
    mocks = {"a": with_tab_bar(screen, lit=0, fill="#3a2030"), "b": red_icon, "c": off_icon}
    box = Rect(x=0, y=780, w=411, h=58)
    failures = qa_metrics.chrome_failures({sid: {"tabbar": box} for sid in mocks}, mocks, real)
    assert copies(failures) == {"a": "c", "b": "c"}


def test_a_screen_that_carries_no_mark_is_checked_at_the_box_a_later_screen_marks():
    real = with_tab_bar(Image.new("RGB", (411, 838), "#101014"), lit=0)
    chrome = {"s01": {}, "s02": {"tabbar": Rect(x=0, y=780, w=411, h=58)}}
    failures = qa_metrics.chrome_failures(chrome, {"s01": Image.new("RGB", (411, 838), "#101014"), "s02": real},
                                          dict.fromkeys(chrome, real))
    assert copies(failures) == {"s01": "s02"}


def test_a_pair_is_held_to_the_box_where_it_differs_though_it_passes_at_the_other():
    """b marks its bar 20 dp taller than a does and draws red in those 20 dp, outside a's box."""
    real = with_tab_bar(Image.new("RGB", (411, 838), "#101014"), lit=0)
    red_above = real.copy()
    ImageDraw.Draw(red_above).rectangle((300, 762, 360, 778), fill="#e03030")
    parts = {"a": {"tabbar": Rect(x=0, y=780, w=411, h=58)}, "b": {"tabbar": Rect(x=0, y=760, w=411, h=78)}}
    failures = qa_metrics.chrome_failures(parts, {"a": real, "b": red_above}, {"a": real, "b": real})
    assert copies(failures) == {"b": "a"}


def alike_share(a: Image.Image, b: Image.Image, box: Rect) -> float:
    """The share of a box two real screens draw alike."""
    return float(qa_metrics.alike(np.asarray(a), np.asarray(b), box).mean())


def with_tab_bar(screen: Image.Image, lit: int, fill: str = "#202226") -> Image.Image:
    """A content-dp screen with a dark tab bar of four icons at the bottom, icon lit highlighted."""
    out = screen.copy()
    draw = ImageDraw.Draw(out)
    draw.rectangle((0, 780, 410, 837), fill=fill)
    for i in range(4):
        draw.rectangle((28 + i * 100, 786, 74 + i * 100, 832), fill="#a070ff" if i == lit else "#8a8a8a")
    return out


def test_a_highlighted_tab_may_move_as_it_does_in_the_app_but_the_bar_may_not_change():
    """The bar fills the whole box, so the screen under it doesn't matter."""
    screen = Image.new("RGB", (411, 838), "#101014")
    real = {"a": with_tab_bar(screen, lit=0), "b": with_tab_bar(screen, lit=1)}
    box = Rect(x=0, y=780, w=411, h=58)
    parts = {"a": {"tabbar": box}, "b": {"tabbar": box}}
    share = alike_share(real["a"], real["b"], box)
    assert qa_metrics.SAME_PART <= share < 1
    assert qa_metrics.chrome_failures(parts, real, real) == []
    recolored = {"a": real["a"], "b": with_tab_bar(screen, lit=1, fill="#3a2030")}
    failures = qa_metrics.chrome_failures(parts, recolored, real)
    assert [f["screen"] for f in failures] == ["b"]
    assert f"over the {share:.0%} of the box the real screens draw alike" in failures[0]["detail"]


def test_what_the_app_itself_changes_never_decides_which_screen_is_told_to_change():
    """a draws its highlighted tab in the wrong color, which only the per-screen SSIM sees; b draws one plain icon in
    the wrong gray, where the app draws both screens alike. b is the one to fix."""
    screen = Image.new("RGB", (411, 838), "#101014")
    real = {"a": with_tab_bar(screen, lit=0), "b": with_tab_bar(screen, lit=1)}
    wrong_highlight = with_tab_bar(screen, lit=0)
    ImageDraw.Draw(wrong_highlight).rectangle((28, 786, 74, 832), fill="#ff0000")
    wrong_icon = with_tab_bar(screen, lit=1)
    ImageDraw.Draw(wrong_icon).rectangle((328, 786, 374, 832), fill="#3a3a3a")
    box = Rect(x=0, y=780, w=411, h=58)
    failures = qa_metrics.chrome_failures({"a": {"tabbar": box}, "b": {"tabbar": box}},
                                          {"a": wrong_highlight, "b": wrong_icon}, real)
    assert copies(failures) == {"b": "a"}


def test_two_different_bars_on_a_plain_band_are_not_held_to_one_look():
    """AOL's home screen and its article screen each draw their own bar on a white band. The home bar drawn 6 dp off
    is a placement miss the bounds check reports, not a reason to draw it like the article toolbar."""
    real = real_screens("aol")
    box = Rect(x=0, y=780, w=411, h=58)
    shifted = np.asarray(real["s01"]).copy()
    shifted[780:] = np.roll(shifted[780:], 6, axis=1)
    chrome = {sid: {"tabbar": box} for sid in ("s01", "s02", "s05")}
    assert qa_metrics.SAME_PART > alike_share(real["s01"], real["s02"], box)
    assert qa_metrics.chrome_failures(chrome, {**real, "s01": Image.fromarray(shifted)}, real) == []


def test_a_bar_drawn_lower_on_one_screen_fails_there_though_it_looks_the_same():
    """The app draws one bar at y 700 over content that changes down the screen; b draws the same bar 20 dp lower."""
    image = np.zeros((838, 411, 3), np.uint8)
    image[:, :, 1] = np.arange(838)[:, None] % 256
    content = Image.fromarray(image)

    def bar_at(y: int) -> Image.Image:
        out = content.copy()
        ImageDraw.Draw(out).rectangle((0, y, 410, y + 55), fill="#20252b")
        return out
    real = bar_at(700)
    parts = {"a": {"tabbar": Rect(x=0, y=700, w=411, h=56)}, "b": {"tabbar": Rect(x=0, y=720, w=411, h=56)}}
    failures = qa_metrics.chrome_failures(parts, {"a": real, "b": bar_at(720)}, {"a": real, "b": real})
    assert copies(failures) == {"b": "a"}


# Where each test app's own screenshots draw its bottom bar, read off the goldens, and the box it fills. The other
# in-scope screens carry no mark: a sheet, a menu or a paywall over the bar, or a screen without one.
REAL_BARS = {"janitorai": (["s01", "s03", "s05", "s06", "s07", "s08"], Rect(x=0, y=775, w=411, h=63)),
             "luzia": (["s01", "s05", "s06"], Rect(x=0, y=770, w=411, h=68)),
             "aol": (["s02", "s05"], Rect(x=0, y=780, w=411, h=58))}


def real_screens(app: str) -> dict[str, Image.Image]:
    """Every screen of the app's golden, in content dp: which ones the mock draws is the mock's call, not this
    check's."""
    return {s.id: render.content_dp(Image.open(FIXTURES / "golden" / app / s.canonical_png))
            for s in golden(app).states}


def tinted(image: Image.Image, box: Rect) -> Image.Image:
    """The box blended 30% toward a rose: the same bar drawn in another color."""
    pixels = np.asarray(image).astype(float)
    cut = (slice(int(box.y), int(box.y + box.h)), slice(int(box.x), int(box.x + box.w)))
    pixels[cut] = 0.7 * pixels[cut] + 0.3 * np.array([176, 48, 96])
    return Image.fromarray(pixels.round().astype(np.uint8))


@pytest.mark.parametrize("app", APPS)
def test_each_apps_own_bar_passes_as_its_screens_draw_it_and_fails_on_the_one_screen_drawn_in_another_color(app):
    """On the unedited real screens, whatever each app changes between screens (Luzia lights a large circle behind
    the current tab) is left out, so a mock that draws every screen as the app does passes, and a bar drawn in another
    color on any one screen fails there."""
    real = real_screens(app)
    screens, box = REAL_BARS[app]
    chrome = {sid: {"tabbar": box} if sid in screens else {} for sid in real}
    assert qa_metrics.chrome_failures(chrome, real, real) == []
    for odd in (screens[0], screens[-1]):
        failures = qa_metrics.chrome_failures(chrome, {**real, odd: tinted(real[odd], box)}, real)
        assert [f["screen"] for f in failures] == [odd]
        assert all(s in failures[0]["detail"] for s in screens if s != odd)
        assert copies(failures)[odd] in screens


def floor(image: Image.Image, value: int) -> Image.Image:
    """Every channel raised to at least value: black drawn a little off."""
    return Image.fromarray(np.maximum(np.asarray(image), value).astype(np.uint8))


def test_a_dialog_is_never_held_to_the_chrome_its_parent_shows_through_its_backdrop(tmp_path):
    """Luzia's pet intro (s04) is a modal over the chats home (s01): its render shows s01's tab bar through its
    backdrop, drawn by s01's own section. Every barred screen draws the bar's black a little off, the same on each,
    and the backdrop lifts it less on the modal. No barred screen is told to draw its bar like the modal's, and the
    modal isn't told to draw a bar of its own."""
    model = golden("luzia")
    real = real_screens("luzia")
    screens, box = REAL_BARS["luzia"]
    mocks = {**real, **{sid: floor(real[sid], 10) for sid in screens}, "s04": floor(real["s04"], 4)}
    for kind, images in (("real", real), ("mock", mocks)):
        (tmp_path / kind).mkdir()
        for sid, image in images.items():
            image.save(tmp_path / kind / f"{sid}.png")
    dom = {sid: qa_metrics.ScreenDom(boxes={}, images=[], chrome={"tabbar": box} if sid in screens else {}, values=[])
           for sid in real}
    assert [f for f in qa.cross_screen_failures(model, tmp_path, dom) if f["kind"] == "chrome"] == []


# ---------- data-value ----------

@pytest.mark.parametrize(("value", "text", "shown"), [
    ("0", "0\nFollowing", True), ("0", "0, Following", True), ("120", "💎 120", True), ("7 chats", "7 chats", True),
    ("$1.99", "US$1.99 / week", True), ("1 200", "1 200", True), ("Pro", "PRO", True), ("Pro", "Pro plan", True),
    ("0", "10", False), ("0", "1.0", False), ("200", "1,200", False), ("120", "95", False), ("120", "", False),
    ("120 coins", "coins", False), ("120 coins", "120", False), ("120 coins", "120 gems", False),
    ("$1.99", "€1.99", False), ("Pro", "Proton", False), ("", "", False),
])
def test_whole_words_decide_whether_a_tag_shows_a_value(value, text, shown):
    assert qa_metrics.shows(value, text) is shown


@pytest.mark.parametrize("app", APPS)
def test_a_shared_value_that_reads_differently_or_goes_unmarked_fails_on_its_screen(tmp_path, app):
    """The golden's own values carry their tags (the offline builder follows the contract), so they pass."""
    model = golden(app)
    a, b, c, d = [s.id for s in mock.pick_scope(model)][:4]
    coins = CrossScreenValue(id="v90", label="Coins", value_text="120", evidence_ids=[a, b, c])
    model = model.model_copy(update={"cross_screen_values": [*model.cross_screen_values, coins]})
    tag = '<b data-value="{}" style="position:absolute;left:8px;top:8px;z-index:3">{}</b>'
    tags = {a: tag.format("v90", "120 coins"), b: tag.format("v90", "95"), d: tag.format("v91", "7")}
    run_dir, scope, html = run_with(tmp_path, app, model, lambda h: at_top(h, tags))
    version = qa.measure(ctx_for(run_dir, app), model, scope, 0, html)

    failures = {f["screen"]: f["detail"] for f in version.cross_screen}
    assert [f["kind"] for f in version.cross_screen] == ["value"] * 3 and set(failures) == {b, c, d}
    assert f"reads '95', not the model's '120' as on {a}" in failures[b]
    assert 'no tag there carries data-value="v90"' in failures[c]
    assert 'data-value="v91"' in failures[d] and "names no cross-screen value" in failures[d]


# ---------- where failures go ----------

@pytest.mark.parametrize("twelve", APPS, indirect=True)
def test_a_failure_goes_to_the_critic_shown_its_screen_and_to_the_fixer_when_a_fix_names_it(twelve, monkeypatch):
    app, run_dir, model, ids = twelve
    failure = {"kind": "chrome", "screen": ids[9], "detail": f'data-chrome="tabbar" on {ids[9]} renders differently'}
    version = measured_twelve(run_dir, model)
    version.cross_screen = [failure]
    calls = []
    monkeypatch.setattr(llm, "call", fake_critic(calls))
    qa.criticize(ctx_for(run_dir, app), llm.Budget("qa", 12.0), version, [], 1)
    told_groups = {c["seen"][0]: c["told"]["cross_screen_failures"] for c in calls}
    assert told_groups == {ids[0]: [], ids[4]: [], ids[8]: [failure]}

    for named, sent in ((ids[9], [failure]), (ids[0], [])):
        calls.clear()
        critique = Critique(fixes=[Fix(element_id=named, problem="p", fix="f")], summary="s")
        qa.fix(ctx_for(run_dir, app), llm.Budget("qa", 12.0), model, version, critique, 1)
        assert calls[-1]["told"]["cross_screen_failures"] == sent


@pytest.mark.parametrize("app", APPS)
def test_a_cross_screen_failure_leaves_the_approved_version_qa_incomplete_and_is_listed(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    (run_dir / "mock" / "assets").mkdir(parents=True)
    (run_dir / "mock" / "index.html").write_text("<html><body>v0</body></html>")
    screens = [s.id for s in mock.pick_scope(golden(app))]
    (run_dir / "mock" / "contract_report.json").write_text(
        ContractReport(passed=True, screens=screens, errors=[]).model_dump_json())
    failure = {"kind": "value", "screen": screens[1], "detail": f'data-value="v90" (Coins) on {screens[1]} reads 95'}

    def measure(ctx, model, scope, n, html):
        metrics = QAMetrics(round=n, screens=[], cross_screen_failures=[failure["detail"]], score=5.0)
        return qa.Version(n, html, metrics, [], [], [], [], [failure])

    def refuse(**kwargs):
        raise llm.LLMFailure("refusal", "no")
    monkeypatch.setattr(qa, "measure", measure)
    monkeypatch.setattr(llm, "call", refuse)
    qa.run(ctx_for(run_dir, app))
    report = json.loads((run_dir / "qa" / "qa_report.json").read_text())
    assert (report["status"], report["outcome"], report["keep_score"]) == ("qa_incomplete", "partial", 5.0)
    assert "cross-screen failures on the approved version: 1" in report["reasons"]
    assert report["resume"].startswith(f"simula qa {app} --run {run_dir.name} {OPTIONS}")
    assert report["cross_screen_failures"] == [failure] and report["rounds"][0]["cross_screen_failures"] == 1
    exhibit = (run_dir / "exhibits" / "04-qa.md").read_text()
    assert "cross-screen failures on the approved version: 1" in exhibit and failure["detail"] in exhibit
    assert "marks `data-chrome` on 0 screens and `data-value` on 0; 1 failures" in exhibit


def test_the_exhibit_counts_the_screens_whose_sections_carry_each_mark():
    html = ('<section data-screen="s01"><nav data-chrome="tabbar"><b data-value="v1">3</b></nav></section>'
            '<section data-screen="s02"><div><nav data-chrome="tabbar"></nav></div></section>'
            '<section data-screen="s03"><p>none</p></section>')
    assert qa.marked_screens(html) == {"data-chrome": {"s01", "s02"}, "data-value": {"s01"}}


def test_a_replay_takes_the_recorded_cross_screen_failures():
    failure = {"kind": "chrome", "screen": "s03", "detail": "d"}
    metrics = QAMetrics(round=0, screens=[], cross_screen_failures=["d"], score=5.0)
    version = qa.Version(0, "<html></html>", metrics, [], [], [], [], [failure])
    assert qa.version_from(json.loads(qa.version_record(version)), 0, version.html) == version


def test_a_measurement_recorded_before_the_cross_screen_check_is_a_replay_miss_not_a_crash(tmp_path, records):
    """A record from before the check has no cross_screen field; it was keyed without the record's version."""
    app = APPS[0]
    model = golden(app)
    run_dir, scope, html = run_with(tmp_path, app, model, lambda h: h)
    ctx = replace(ctx_for(run_dir, app), replay=True)
    metrics = QAMetrics(round=0, screens=[], cross_screen_failures=[], score=5.0)
    old = json.loads(qa.version_record(qa.Version(0, html, metrics, [], [], [], [])))
    del old["cross_screen"]
    qa.write_record(qa.record_path("measure", 0, html, qa.inputs_digest(ctx)), json.dumps(old))
    with pytest.raises(llm.ReplayMiss, match="no measurement record for round 0"):
        qa.measure_or_replay(ctx, model, scope, 0, html)
