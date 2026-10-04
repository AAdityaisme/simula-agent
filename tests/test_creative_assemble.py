import base64
import hashlib
import re

import pytest
from playwright.sync_api import sync_playwright

from simula.creative.assemble import CONCEPT, SPONSORED, assemble, visible_strings, write_variant
from simula.creative.schema import Character, Claim, Content, Copy, Proof, Puzzle
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
RIGHT_PATH = ["DISPLAYED", "CHALLENGE_STARTED", "CHALLENGE_PASS_1", "CHALLENGE_PASS_2", "CHALLENGE_PASS_3",
              "CHALLENGE_SOLVED", "ENDCARD_SHOWN", "CTA_CLICKED"]
VISIBLE_TEXT = """() => [...document.querySelectorAll('body *')]
  .filter(e => e.children.length === 0 && e.textContent.trim() && e.checkVisibility())
  .map(e => e.textContent.trim())"""


def teacher_content(**copy) -> Content:
    """The Teacher variant built by hand (E's generator is not needed here)."""
    lines = {"intro": "Three quick ones. Can you get them all?",
             "captions": ["Find x", "What comes next?", "Unscramble the word"], "right_line": "That's it!",
             "wrong_hint": "Not quite, try again", "end_headline": "Keep learning every day"} | copy
    return Content(
        variant_id="teacher-challenge", app_name="Luzia",
        host=Character(kind="app_persona", name="Teacher", art_ref="s01.e13.art.png", evidence_id="s01.e13"),
        hook="challenge", seed=7,
        puzzles=[Puzzle(family="solve_for_x", prompt="3x + 4 = 19", options=["4", "5", "6"], answer=1),
                 Puzzle(family="next_in_sequence", prompt="2, 5, 8, 11, ?", options=["17", "14", "15"], answer=1),
                 Puzzle(family="unscramble_word", prompt="LCNEPI", options=["PENCIL", "RULER", "CHALK"], answer=0)],
        copy=Copy(**lines),
        proof=Proof(screen_id="s15", claims=[
            Claim(text="I am Teacher, your personal tutor.", evidence_id="s15.e02"),
            Claim(text="I'm here to resolve your doubts, clarify those difficult concepts", evidence_id="s15.e02")]),
        cta="Install Now")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def open_page(browser, tmp_path, content, query, width=390):
    """The creative written to tmp_path and opened with query; returns the page and its console errors."""
    path = write_variant(content, RUN, tmp_path / content.variant_id)
    page = browser.new_page(viewport={"width": width, "height": 844})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.resolve().as_uri() + query)
    return page, errors


def s04_hashes() -> set[str]:
    files = [*(RUN / "qa" / "approved" / "assets").glob("s04.*"), *(RUN / "model" / "assets").glob("s04.*"),
             RUN / "model" / "states" / "s04.png"]
    return {hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def test_the_teacher_variant_has_no_external_url_and_no_s04_bytes():
    html = assemble(teacher_content(), RUN)
    assert re.findall(r"(?i)https?:", html) == []
    embedded = [hashlib.sha256(base64.b64decode(b)).hexdigest()
                for b in re.findall(r"data:[\w/+.-]+;base64,([A-Za-z0-9+/=]+)", html)]
    assert len(embedded) == 3
    assert not set(embedded) & s04_hashes()
    assert hashlib.sha256((RUN / "qa" / "approved" / "assets" / "s01.e13.art.png").read_bytes()).hexdigest() in embedded


def test_fast_mode_emits_the_right_path_events_in_order(browser, tmp_path):
    content = teacher_content()
    page, errors = open_page(browser, tmp_path, content, "?fast=1")
    for puzzle in content.puzzles:
        page.click(f'.option[data-index="{puzzle.answer}"]')
    page.click("#cta")
    events = page.evaluate("() => window.simulaCreative.events()")
    assert [e["name"] for e in events] == RIGHT_PATH
    assert [(e["n"], e["attempts"]) for e in events if e["name"].startswith("CHALLENGE_PASS_")] == [(1, 1), (2, 1), (3, 1)]
    assert errors == []
    page.close()


def test_every_string_on_screen_is_a_review_row(browser, tmp_path):
    content = teacher_content()
    path = write_variant(content, RUN, tmp_path / "v")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.clock.install(time=0)
    page.goto(path.resolve().as_uri() + "?hold=1")
    page.clock.pause_at(1000)
    page.evaluate("() => window.simulaCreative.start()")
    shown = set(page.evaluate(VISIBLE_TEXT))
    page.clock.run_for(4000)
    for puzzle in content.puzzles:
        page.click(f'.option[data-index="{next(k for k in range(3) if k != puzzle.answer)}"]')
        shown |= set(page.evaluate(VISIBLE_TEXT))
        page.click(f'.option[data-index="{puzzle.answer}"]')
        shown |= set(page.evaluate(VISIBLE_TEXT))
        page.clock.run_for(800)
    shown |= set(page.evaluate(VISIBLE_TEXT))
    page.clock.run_for(4000)
    shown |= set(page.evaluate(VISIBLE_TEXT))
    assert page.evaluate("() => window.simulaCreative.state()") == "end"
    assert shown - {string for _, string, _ in visible_strings(content)} == set()
    assert {"Teacher", content.copy_.intro, content.copy_.wrong_hint, content.proof.claims[0].text,
            content.copy_.end_headline, "Install Now", SPONSORED, CONCEPT} <= shown
    page.close()


def test_markup_in_a_generated_line_is_shown_as_text_never_run(browser, tmp_path):
    hostile = '</script><img src=x onerror="console.error(1)">'
    page, errors = open_page(browser, tmp_path, teacher_content(intro=hostile), "?hold=1")
    assert page.evaluate("() => document.getElementById('intro-line').textContent") == hostile
    assert page.evaluate("() => document.querySelectorAll('img').length") == 3
    assert errors == []
    page.close()


def test_host_art_is_never_drawn_above_125_percent_of_its_pixels(browser, tmp_path):
    page, _ = open_page(browser, tmp_path, teacher_content(), "?hold=1", width=430)
    page.evaluate("() => window.simulaCreative.start()")
    page.wait_for_function("() => document.getElementById('host-art').naturalWidth > 0")
    width, natural = page.evaluate("""() => { const i = document.getElementById('host-art');
        return [i.getBoundingClientRect().width, i.naturalWidth]; }""")
    assert natural == 318 and 0 < width <= natural * 1.25
    page.close()
