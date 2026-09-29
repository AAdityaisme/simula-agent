"""The cross-screen check: shared chrome (data-chrome) renders the same on every screen that draws it, and a shared
value (data-value) reads the same wherever it appears. Its failures reach the critic and the fixer like any QA
finding, never enter the score, and leave the approved version qa_incomplete. Stage-level tests run on all three
test apps."""

import json
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw

from simula import llm, qa_metrics, render
from simula.contracts import ContractReport, Critique, CrossScreenValue, Fix, QAMetrics, Rect
from simula.stages import mock, qa
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, seed_model, skeleton_html
from tests.test_mock_isolation import ctx_for
from tests.test_qa_loop import fake_critic, measured_twelve, records, told, twelve  # noqa: F401 (fixtures)

BAR_DP = 56
BAND_DP = 80  # the real screens' shared band is taller than the mock's bar, so resampling at its edge stays outside
# Band colors that each differ from every other by most of a channel, so no two bands look alike to SSIM.
PALETTE = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 0, 255), (0, 160, 0), (255, 200, 0), (120, 0, 160), (0, 200, 200)]


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
    return {s.id: bar(colors.get(s.id, "#20252b")) for s in mock.pick_scope(model)}


def share_band(run_dir, model, own_colors: bool = False) -> None:
    """Gives every in-scope real screen the root's bottom band (the real app draws one bar on every screen), or with
    own_colors, a band of its own color on each (the app draws its bar differently on every screen)."""
    scope = mock.pick_scope(model)
    root = Image.open(run_dir / "model" / scope[0].canonical_png).convert("RGB")
    box = (0, root.height - round(BAND_DP * model.device.scale), root.width, root.height)
    band = root.crop(box)
    for i, s in enumerate(scope):
        path = run_dir / "model" / s.canonical_png
        image = Image.open(path).convert("RGB")
        image.paste(PALETTE[i] if own_colors else band, box)
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
    assert failures[0]["detail"].endswith(f'draw it as {scope[0].id} does, marked data-chrome="tabbar"')


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
def test_the_screen_that_matches_fewer_others_is_the_one_to_fix_even_when_it_comes_first(app):
    real = render.content_dp(Image.open(FIXTURES / "golden" / app / mock.pick_scope(golden(app))[0].canonical_png))
    odd = real.copy()
    ImageDraw.Draw(odd).rectangle((0, 780, 411, 838), fill="#b03060")
    box = Rect(x=0, y=780, w=411, h=58)
    parts = {sid: {"tabbar": box} for sid in ("a", "b", "c")}
    failures = qa_metrics.chrome_failures(parts, {"a": odd, "b": real, "c": real}, dict.fromkeys(parts, real))
    assert [f["screen"] for f in failures] == ["a"] and "from b, c" in failures[0]["detail"]


def test_a_bar_drawn_lower_on_one_screen_fails_though_it_looks_the_same():
    image = np.zeros((838, 411, 3), np.uint8)
    image[:, :, 1] = np.arange(838)[:, None] % 256
    real = Image.fromarray(image)
    one, other = real.copy(), real.copy()
    ImageDraw.Draw(one).rectangle((0, 700, 410, 755), fill="#20252b")
    ImageDraw.Draw(other).rectangle((0, 720, 410, 775), fill="#20252b")
    parts = {"a": {"tabbar": Rect(x=0, y=700, w=411, h=56)},
             "b": {"tabbar": Rect(x=0, y=720, w=411, h=56)}}
    assert [f["screen"] for f in qa_metrics.chrome_failures(parts, {"a": one, "b": other}, {"a": real, "b": real})] \
        == ["b"]


# ---------- data-value ----------

@pytest.mark.parametrize(("value", "text", "shown"), [
    ("0", "0\nFollowing", True), ("0", "0, Following", True), ("120 coins", "120", True), ("120", "💎 120", True),
    ("120 coins", "💎 120", True), ("$1.99", "US$1.99 / week", True), ("1 200", "1 200", True),
    ("Pro", "PRO", True), ("Pro", "Pro plan", True),
    ("0", "10", False), ("0", "1.0", False), ("200", "1,200", False), ("120", "95", False), ("120", "", False),
    ("120 coins", "coins", False), ("Pro", "Proton", False), ("Pro", "", False),
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
    assert report["status"] == "qa_incomplete" and report["score"] == 5.0
    assert report["cross_screen_failures"] == [failure] and report["rounds"][0]["cross_screen_failures"] == 1
    exhibit = (run_dir / "exhibits" / "04-qa.md").read_text()
    assert "1 cross-screen failures" in exhibit and failure["detail"] in exhibit
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
