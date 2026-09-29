"""The mock carries its own fonts: at build, code copies the Google Fonts CSS and woff2 files into mock/assets/fonts/,
and a fetch that fails leaves a mock with system fonts and one trace line. No test touches the network."""

import urllib.error

import pytest

from simula import llm
from simula.contracts import ContractReport
from simula.runlog import read_trace
from simula.stages import mock
from tests.conftest import APPS
from tests.mock_fake import golden, seed_model
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


def fake_google(calls: list):
    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.startswith("https://fonts.googleapis.com/css2?family="):
            family = url.split("family=")[1].split(":")[0].replace("+", " ")
            return CSS.replace("{family}", family).replace("{slug}", family.lower().replace(" ", "")).encode()
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
    link = mock.vendor_fonts(ctx_for(run_dir, APPS[0]), run_dir / "mock", ["Roboto", "Lobster"])
    css = (run_dir / "mock" / "assets" / "fonts" / "fonts.css").read_text()
    assert link == '<link rel="stylesheet" href="assets/fonts/fonts.css">'
    assert "'Roboto'" in css and "Lobster" not in css
    [line] = read_trace(run_dir / "trace.jsonl")
    assert "Lobster: HTTP Error 400" in line.note


def test_css_that_isnt_split_by_subset_keeps_every_face():
    css = "@font-face { font-family: 'X'; src: url(https://fonts.gstatic.com/x.woff2); }"
    assert mock.latin_faces(css) == css
