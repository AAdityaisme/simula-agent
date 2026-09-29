"""The mock carries its own fonts: at build, code copies the Google Fonts CSS and woff2 files into mock/assets/fonts/,
and a fetch that fails leaves a mock with system fonts and one trace line. No test touches the network."""

import base64
import http.client
import json
import re
import urllib.error

import pytest

from simula import llm, render
from simula.contracts import ContractReport, ProductModel
from simula.runlog import read_trace
from simula.stages import mock
from tests.conftest import APPS, FIXTURES
from tests.mock_fake import golden, seed_model
from tests.test_mock_batches import provider_drawing, with_cache_in
from tests.test_mock_isolation import ctx_for, fake_builder

CSS = """/* cyrillic */
@font-face {
  font-family: '{family}';
  font-weight: 400;
  src: url(https://fonts.gstatic.com/s/{slug}/v1/cyrillic-400.woff2) format('woff2');
  unicode-range: U+0400-045F;
}
/* latin */
@font-face {
  font-family: '{family}';
  font-weight: 400;
  src: url(https://fonts.gstatic.com/s/{slug}/v1/latin-400.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
/* latin */
@font-face {
  font-family: '{family}';
  font-weight: 700;
  src: url(https://fonts.gstatic.com/s/{slug}/v1/latin-700.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
"""


def fake_google(calls: list, apache=(), unlicensed=()):
    """Google Fonts plus Google's font repository, which files a family under ofl/ unless it is in `apache`, and
    has no license file at all for the families in `unlicensed`."""
    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.startswith("https://fonts.googleapis.com/css2?family="):
            family = url.split("family=")[1].split(":")[0].replace("+", " ")
            return CSS.replace("{family}", family).replace("{slug}", family.lower().replace(" ", "")).encode()
        if url.startswith("https://raw.githubusercontent.com/google/fonts/main/"):
            kind, slug = url.split("/main/")[1].split("/")[:2]
            if slug in unlicensed or kind != ("apache" if slug in apache else "ofl"):
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            return f"Copyright The {slug} Authors. Licensed under {kind}.".encode()
        return b"wOF2 " + url.encode()
    return fetch


def offline(calls: list):
    def fetch(url: str) -> bytes:
        calls.append(url)
        raise urllib.error.URLError("no network")
    return fetch


def with_fonts(run_dir, app, families):
    """The golden, its in-mock elements guessed as the given fonts in turn."""
    model = golden(app)
    guess = iter(families * 10_000)
    states = [s.model_copy(update={"elements": [e.model_copy(update={"font_guess": next(guess)}) if e.in_mock else e
                                                for e in s.elements]}) for s in model.states]
    (run_dir / "model" / "product_model.json").write_text(model.model_copy(update={"states": states}).model_dump_json())


@pytest.mark.parametrize("app", APPS)
def test_the_mock_links_its_own_copy_of_the_latin_faces(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    with_fonts(run_dir, app, ["Roboto", "Open Sans"])
    calls = []
    monkeypatch.setattr(mock, "fetch", fake_google(calls))
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))

    html = (run_dir / "mock" / "index.html").read_text()
    fonts = run_dir / "mock" / "assets" / "fonts"
    css = (fonts / "fonts.css").read_text()
    assert '<link rel="stylesheet" href="assets/fonts/fonts.css">' in html and "googleapis" not in html
    assert "gstatic" not in css and "cyrillic" not in css and css.count("@font-face") == 4
    assert "font-family: 'Roboto'" in css and "font-family: 'Open Sans'" in css
    files = sorted(p.name for p in fonts.glob("*.woff2"))
    assert len(files) == 4 and all(f"url({name})" in css for name in files)
    assert not any("cyrillic" in url for url in calls)
    assert ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text()).passed
    assert "fonts" not in [line.step for line in read_trace(run_dir / "trace.jsonl")]


@pytest.mark.parametrize("app", APPS)
def test_fonts_that_cant_be_fetched_leave_system_fonts_and_one_trace_line(tmp_path, monkeypatch, app):
    run_dir = seed_model(tmp_path / "run", app)
    with_fonts(run_dir, app, ["Roboto", "Open Sans"])
    calls = []
    monkeypatch.setattr(mock, "fetch", offline(calls))
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, app))

    html = (run_dir / "mock" / "index.html").read_text()
    assert "<link" not in html and not (run_dir / "mock" / "assets" / "fonts").exists()
    assert len(calls) == 4
    [line] = [t for t in read_trace(run_dir / "trace.jsonl") if t.step == "fonts"]
    assert line.outcome == "error" and "Roboto: " in line.note and "Open Sans: " in line.note
    assert ContractReport.model_validate_json((run_dir / "mock" / "contract_report.json").read_text()).passed


def test_one_family_that_fails_is_left_out_and_the_others_are_kept(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", APPS[0])
    google = fake_google([])

    def fetch(url: str) -> bytes:
        if "Lobster" in url:
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
        return google(url)
    monkeypatch.setattr(mock, "fetch", fetch)
    link = mock.vendor_fonts(ctx_for(run_dir, APPS[0]), run_dir / "mock", ["Roboto", "Lobster"], set("Hi"))
    css = (run_dir / "mock" / "assets" / "fonts" / "fonts.css").read_text()
    assert link == '<link rel="stylesheet" href="assets/fonts/fonts.css">'
    assert "'Roboto'" in css and "Lobster" not in css
    [line] = read_trace(run_dir / "trace.jsonl")
    assert "Lobster: HTTP Error 400" in line.note


def test_a_download_cut_off_mid_read_skips_the_family_and_never_fails_the_stage(tmp_path, monkeypatch):
    """IncompleteRead is an http.client.HTTPException, not an OSError."""
    run_dir = seed_model(tmp_path / "run", APPS[0])
    with_fonts(run_dir, APPS[0], ["Roboto"])
    google = fake_google([])

    def fetch(url: str) -> bytes:
        if url.endswith(".woff2"):
            raise http.client.IncompleteRead(b"wOF2", 36_000)
        return google(url)
    monkeypatch.setattr(mock, "fetch", fetch)
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, APPS[0]))

    assert "<link" not in (run_dir / "mock" / "index.html").read_text()
    [line] = [t for t in read_trace(run_dir / "trace.jsonl") if t.step == "fonts"]
    assert line.outcome == "error" and line.note.startswith("webfonts skipped, system fonts used: Roboto: IncompleteRead")


SCRIPTS_CSS = """/* cyrillic */
@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x/cyrillic.woff2); unicode-range: U+0400-045F; }
/* greek */
@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x/greek.woff2); unicode-range: U+0370-03FF; }
@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x/cjk-7.woff2); unicode-range: U+65e5, U+672c-672d; }
@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x/cjk-8.woff2); unicode-range: U+20-7e, U+8a00-8aff; }
/* latin */
@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x/latin.woff2); unicode-range: U+0000-00FF; }
"""


def kept_files(css: str) -> list[str]:
    return [url.rsplit("/", 1)[1] for url in mock.FONT_FILE.findall(css)]


@pytest.mark.parametrize("text, files", [
    ("Chat now", ["latin.woff2"]),
    ("Чат now", ["cyrillic.woff2", "latin.woff2"]),
    ("日本 chat", ["cjk-7.woff2", "latin.woff2"]),
    ("日本語", ["cjk-7.woff2", "cjk-8.woff2", "latin.woff2"]),
    ("", ["latin.woff2"]),
], ids=["latin", "cyrillic", "japanese", "japanese-2", "no-text"])
def test_the_latin_faces_are_kept_and_another_subset_only_for_characters_the_screens_show(text, files):
    """CJK faces come numbered and uncommented, so the range decides, never a subset name or the app. A CJK face that
    also covers ASCII (as Noto Sans JP's do) isn't downloaded for text Latin already covers."""
    assert kept_files(mock.used_faces(SCRIPTS_CSS, set(text))) == files


def test_a_screen_in_another_script_gets_that_scripts_font_files(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", APPS[0])
    with_fonts(run_dir, APPS[0], ["Roboto"])
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    drawn = next(e for e in mock.pick_scope(model)[0].elements if e.in_mock)
    states = [s.model_copy(update={"elements": [e.model_copy(update={"text": "Привет"}) if e is drawn else e
                                                for e in s.elements]}) for s in model.states]
    (run_dir / "model" / "product_model.json").write_text(model.model_copy(update={"states": states}).model_dump_json())
    calls = []
    monkeypatch.setattr(mock, "fetch", fake_google(calls))
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, APPS[0]))

    assert sorted(url.rsplit("/", 1)[1] for url in calls if url.endswith(".woff2")) == \
        ["cyrillic-400.woff2", "latin-400.woff2", "latin-700.woff2"]
    assert (run_dir / "mock" / "assets" / "fonts" / "fonts.css").read_text().count("@font-face") == 3


def test_text_the_builder_copies_from_a_screenshot_alone_gets_its_scripts_font(tmp_path, monkeypatch):
    """Greptile on fd7e87f: the builder copies text a screenshot shows that no element carries, so the faces follow
    the drawn page, not only the elements' text."""
    run_dir = seed_model(tmp_path / "run", APPS[0])
    with_fonts(run_dir, APPS[0], ["Roboto"])
    draw = fake_builder([])

    def builder(**kwargs):
        text, reply = draw(**kwargs)
        return text.replace("</section>", "<p>Привет</p></section>", 1), reply
    calls = []
    monkeypatch.setattr(mock, "fetch", fake_google(calls))
    monkeypatch.setattr(llm, "call", builder)
    mock.run(ctx_for(run_dir, APPS[0]))

    assert "cyrillic-400.woff2" in [url.rsplit("/", 1)[1] for url in calls]
    assert "Привет" in (run_dir / "mock" / "index.html").read_text()


def test_a_rerun_leaves_only_the_font_files_it_fetched(tmp_path, monkeypatch):
    """QA's replay key hashes all of mock/assets, so a file the page no longer uses would still change it."""
    run_dir = seed_model(tmp_path / "run", APPS[0])
    ctx, fonts = ctx_for(run_dir, APPS[0]), run_dir / "mock" / "assets" / "fonts"
    monkeypatch.setattr(mock, "fetch", fake_google([]))
    mock.vendor_fonts(ctx, run_dir / "mock", ["Roboto"], set("Hi"))
    google = fake_google([])
    monkeypatch.setattr(mock, "fetch", lambda url: google(url) + (b" v2" if url.endswith(".woff2") else b""))
    mock.vendor_fonts(ctx, run_dir / "mock", ["Roboto"], set("Hi"))
    css = (fonts / "fonts.css").read_text()
    assert sorted(p.name for p in fonts.iterdir()) == sorted(["LICENSE.txt", "fonts.css",
                                                              *re.findall(r"url\(([^)]+)\)", css)])
    assert all(p.read_bytes().endswith(b" v2") for p in fonts.glob("*.woff2"))

    monkeypatch.setattr(mock, "fetch", offline([]))
    assert mock.vendor_fonts(ctx, run_dir / "mock", ["Roboto"], set("Hi")) == "" and not fonts.exists()


def test_each_vendored_family_ships_its_license_text(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", APPS[0])
    monkeypatch.setattr(mock, "fetch", fake_google([], apache={"opensans"}))
    mock.vendor_fonts(ctx_for(run_dir, APPS[0]), run_dir / "mock", ["Roboto", "Open Sans"], set("Hi"))
    text = (run_dir / "mock" / "assets" / "fonts" / "LICENSE.txt").read_text()
    repo = "https://raw.githubusercontent.com/google/fonts/main"
    assert f"Roboto: {repo}/ofl/roboto/OFL.txt\n\nCopyright The roboto Authors. Licensed under ofl." in text
    assert f"Open Sans: {repo}/apache/opensans/LICENSE.txt\n\nCopyright The opensans Authors. Licensed under apache." in text


def test_a_family_whose_license_cant_be_found_is_not_shipped(tmp_path, monkeypatch):
    run_dir = seed_model(tmp_path / "run", APPS[0])
    monkeypatch.setattr(mock, "fetch", fake_google([], unlicensed={"lobster"}))
    mock.vendor_fonts(ctx_for(run_dir, APPS[0]), run_dir / "mock", ["Roboto", "Lobster"], set("Hi"))
    fonts = run_dir / "mock" / "assets" / "fonts"
    assert "Lobster" not in (fonts / "fonts.css").read_text() + (fonts / "LICENSE.txt").read_text()
    [line] = read_trace(run_dir / "trace.jsonl")
    assert "Lobster: no license file for Lobster in Google's font repository" in line.note


def test_a_family_whose_css_still_points_outside_the_mock_is_skipped(tmp_path, monkeypatch):
    """If Google ever quotes its url()s, nothing is rewritten; the family must fail, not ship a blocked request."""
    run_dir = seed_model(tmp_path / "run", APPS[0])
    google = fake_google([])

    def fetch(url: str) -> bytes:
        data = google(url)
        return re.sub(rb"url\((https://[^)]+)\)", rb'url("\1")', data) if "css2" in url else data
    monkeypatch.setattr(mock, "fetch", fetch)
    link = mock.vendor_fonts(ctx_for(run_dir, APPS[0]), run_dir / "mock", ["Roboto"], set("Hi"))
    assert link == "" and not (run_dir / "mock" / "assets" / "fonts").exists()
    [line] = read_trace(run_dir / "trace.jsonl")
    assert "Roboto: its CSS still loads https://fonts.gstatic.com/s/roboto/v1/latin-400.woff2" in line.note


def test_a_real_vendored_font_loads_in_chromium(tmp_path, monkeypatch):
    """A real woff2 (Roboto, OFL, subset to a few letters; license in tests/fixtures/fonts/OFL.txt), not a stand-in
    Chromium would drop silently: the page's own copy must reach `loaded` with no request leaving the mock."""
    run_dir = seed_model(tmp_path / "run", APPS[0])
    with_fonts(run_dir, APPS[0], ["Roboto"])
    woff2 = (FIXTURES / "fonts" / "roboto-subset.woff2").read_bytes()
    google = fake_google([])
    monkeypatch.setattr(mock, "fetch", lambda url: woff2 if url.endswith(".woff2") else google(url))
    monkeypatch.setattr(llm, "call", fake_builder([]))
    mock.run(ctx_for(run_dir, APPS[0]))

    with render.open_mock(run_dir / "mock") as (page, log):
        statuses = page.evaluate("""async () => {
            await document.fonts.ready;
            return [...document.fonts].filter(f => f.family.replace(/["']/g, "") === "Roboto").map(f => f.status);
        }""")
    assert "loaded" in statuses and log == {"console": [], "blocked": [], "failed": []}


def test_css_that_isnt_split_by_subset_keeps_every_face():
    css = "@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x.woff2); }"
    assert mock.used_faces(css, set("Hi")) == css


# ---------- --replay: fonts come from the record, never the network ----------

def refused_family(family: str, calls: list):
    """Fake Google, except the family Google refuses (a 400, as for a family it doesn't have). Roboto's license is
    filed under apache/, so its first license fetch is a 404 that the replay must take too."""
    google = fake_google(calls, apache={"roboto"})

    def fetch(url: str) -> bytes:
        if family.replace(" ", "+") in url:
            calls.append(url)
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
        return google(url)
    return fetch


def no_network(url: str) -> bytes:
    raise AssertionError(f"--replay fetched {url}")


def mock_files(run_dir) -> dict[str, bytes]:
    mock_dir = run_dir / "mock"
    return {str(p.relative_to(mock_dir)): p.read_bytes() for p in sorted(mock_dir.rglob("*")) if p.is_file()}


def live_build_with_fonts(tmp_path, monkeypatch, app) -> tuple:
    """A live build through the real llm.call (stub provider) with Roboto fetched and Lobster refused."""
    run_dir = seed_model(tmp_path / "run", app)
    with_fonts(run_dir, app, ["Roboto", "Lobster"])
    with_cache_in(tmp_path, monkeypatch)
    fetched, drawn = [], []
    monkeypatch.setattr(mock, "fetch", refused_family("Lobster", fetched))
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", provider_drawing(golden(app), drawn))
    mock.run(ctx_for(run_dir, app))
    return run_dir, fetched, drawn


def replay(run_dir, app) -> None:
    ctx = ctx_for(run_dir, app)
    ctx.replay = True
    mock.run(ctx)


def test_a_live_build_records_every_font_fetch_and_its_result(tmp_path, monkeypatch):
    run_dir, fetched, _ = live_build_with_fonts(tmp_path, monkeypatch, APPS[0])
    records = [json.loads(p.read_text()) for p in (run_dir / "mock" / mock.FONT_RECORDS).glob("*.json")]
    assert sorted(r["url"] for r in records) == sorted(set(fetched)) and len(records) == 6
    [refused] = [r for r in records if "Lobster" in r["url"]]
    assert refused == {"url": refused["url"], "error": "HTTP Error 400: Bad Request"}
    fonts = run_dir / "mock" / "assets" / "fonts"
    assert sorted(base64.b64decode(r["data"]) for r in records if r["url"].endswith(".woff2")) == \
        sorted(p.read_bytes() for p in fonts.glob("*.woff2"))


@pytest.mark.parametrize("app", APPS)
def test_a_replay_rebuilds_the_same_mock_with_no_network(tmp_path, monkeypatch, app):
    """The fetched family comes back byte for byte, and the refused one is refused again, with the same trace note."""
    run_dir, fetched, drawn = live_build_with_fonts(tmp_path, monkeypatch, app)
    live = mock_files(run_dir)
    assert any(name.endswith(".woff2") for name in live)
    [live_note] = [t.note for t in read_trace(run_dir / "trace.jsonl") if t.step == "fonts"]

    monkeypatch.setattr(mock, "fetch", no_network)
    replay(run_dir, app)
    assert mock_files(run_dir) == live
    assert len(drawn) == len(mock.batches(mock.pick_scope(golden(app))))
    notes = [t.note for t in read_trace(run_dir / "trace.jsonl") if t.step == "fonts"]
    assert notes == [live_note, live_note] and "Lobster: HTTP Error 400: Bad Request" in live_note


def test_a_later_build_of_another_run_leaves_this_runs_replay_alone(tmp_path, monkeypatch):
    """Greptile on fd7e87f: records keyed by URL alone were shared by every run, so a later live build whose fetches
    failed changed what an earlier, successful build replayed. Each run keeps its own records."""
    run_dir, _, _ = live_build_with_fonts(tmp_path, monkeypatch, APPS[0])
    live = mock_files(run_dir)
    later = seed_model(tmp_path / "later", APPS[0])
    with_fonts(later, APPS[0], ["Roboto", "Lobster"])
    monkeypatch.setattr(mock, "fetch", offline([]))
    mock.run(ctx_for(later, APPS[0]))
    assert not (later / "mock" / "assets" / "fonts").exists()

    monkeypatch.setattr(mock, "fetch", no_network)
    replay(run_dir, APPS[0])
    assert mock_files(run_dir) == live


def test_a_replay_with_a_font_record_missing_is_a_replay_miss(tmp_path, monkeypatch):
    run_dir, _, _ = live_build_with_fonts(tmp_path, monkeypatch, APPS[0])
    [(url, record)] = [(r["url"], p) for p in (run_dir / "mock" / mock.FONT_RECORDS).glob("*.json")
                       if (r := json.loads(p.read_text()))["url"].endswith("latin-700.woff2")]
    record.unlink()

    monkeypatch.setattr(mock, "fetch", no_network)
    with pytest.raises(llm.ReplayMiss, match=f"--replay: no recorded fetch of {re.escape(url)}"):
        replay(run_dir, APPS[0])
