import base64
import hashlib
import io
import re

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from simula.contracts import ProductModel
from simula.creative.assemble import SKIP, SPONSORED, assemble, concept, proof_box, visible_strings, write_variant
from simula.creative.facts import build_facts
from simula.creative.lines import build_content
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
        proof=Proof(screen_id="s15", claims=[Claim(text="I am Teacher, your personal tutor.", evidence_id="s15.e02")]),
        cta="Install Now")


def toki_content() -> Content:
    """The Toki variant as build_content makes it from the host table, with teacher_content's lines."""
    facts = build_facts(RUN)
    toki = next(h for h in facts.hosts if h.id == "toki")
    return build_content(facts, toki, "help_host", 7, teacher_content().copy_)


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
            "From Teacher's screen in Luzia", content.copy_.end_headline, "Luzia", "Install Now", SPONSORED,
            "Concept ad, not affiliated with Luzia, not served", "Skip puzzles"} <= shown
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


def model_state(screen_id):
    model = ProductModel.model_validate_json((RUN / "model" / "product_model.json").read_text())
    return next(s for s in model.states if s.id == screen_id)


def holds(box, rect) -> bool:
    return box[0] <= rect.x and box[1] <= rect.y and rect.x + rect.w <= box[2] and rect.y + rect.h <= box[3]


def meets(box, rect) -> bool:
    return rect.x < box[2] and box[0] < rect.x + rect.w and rect.y < box[3] and box[1] < rect.y + rect.h


def test_the_teacher_proof_crop_is_the_greeting_under_the_screen_header():
    state = model_state("s15")
    rects = {e.id: e.rect_dp for e in state.elements}
    box = proof_box(state, {"s15.e02"}, (411, 838))
    assert holds(box, rects["s15.e02"]) and holds(box, rects["s15.e13"]) and box[1] == 0
    assert box[3] < rects["s15.e06"].y


def test_the_toki_proof_crop_holds_its_claim_and_no_piece_of_the_line_below():
    state = model_state("s01")
    rects = {e.id: e.rect_dp for e in state.elements}
    box = proof_box(state, {"s01.e26"}, (411, 838))
    assert holds(box, rects["s01.e26"])
    assert not meets(box, rects["s01.e27"]) and not meets(box, rects["s01.e23"])


@pytest.mark.parametrize("make", [teacher_content, toki_content], ids=["teacher", "toki"])
def test_the_proof_card_quotes_its_claim_under_an_attribution_on_a_legible_crop(browser, tmp_path, make):
    content = make()
    path = write_variant(content, RUN, tmp_path / "v")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.install(time=0)
    page.goto(path.resolve().as_uri())
    page.clock.run_for(3000)
    for puzzle in content.puzzles:
        page.click(f'.option[data-index="{puzzle.answer}"]')
        page.clock.run_for(800)
    assert page.evaluate("() => window.simulaCreative.state()") == "proof"
    label, quotes, scale = page.evaluate("""() => {
        const shot = document.getElementById('proof-shot');
        return [document.getElementById('proof-label').textContent,
                [...document.querySelectorAll('#claims .claim')].map(q => [q.tagName, q.textContent]),
                shot.getBoundingClientRect().width / shot.naturalWidth]; }""")
    assert label == f"From {content.host.name}'s screen in Luzia"
    assert quotes == [["Q", claim.text] for claim in content.proof.claims]
    assert scale >= 0.8  # content dp to CSS px: an app's 14 dp body text stays at least 11 px tall
    assert errors == []
    page.close()


@pytest.mark.parametrize("leave", ["skip", "idle"])
def test_the_end_card_names_the_app_when_the_player_leaves_the_puzzles(browser, tmp_path, leave):
    path = write_variant(teacher_content(), RUN, tmp_path / "v")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.clock.install(time=0)
    page.goto(path.resolve().as_uri())
    page.clock.run_for(4000)
    if leave == "skip":
        page.click("#skip")
    else:
        page.clock.run_for(10_000)
    assert page.evaluate("() => window.simulaCreative.state()") == "end"
    assert page.locator("#end-app").inner_text() == "Luzia" and page.locator("#end-app").is_visible()
    page.close()


def test_the_skip_and_concept_lines_name_what_they_mean():
    rows = visible_strings(teacher_content())
    assert (SKIP, concept("Luzia")) == ("Skip puzzles", "Concept ad, not affiliated with Luzia, not served")
    assert {("all", SKIP, "template"), ("all", concept("Luzia"), "template"), ("end", "Luzia", "code"),
            ("proof", "From Teacher's screen in Luzia", "template")} <= set(rows)


def test_the_toki_art_is_inlined_cut_to_its_crop_box():
    content = toki_content()
    art = re.search(r'"art": "data:image/png;base64,([A-Za-z0-9+/=]+)"', assemble(content, RUN)).group(1)
    x0, y0, x1, y1 = content.host.art_crop
    with Image.open(io.BytesIO(base64.b64decode(art))) as image:
        assert image.size == (x1 - x0, y1 - y0)
