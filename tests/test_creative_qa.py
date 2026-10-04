import pytest

from simula.creative.assemble import write_variant
from simula.creative.facts import build_facts
from simula.creative.lines import HostLines, build_content
from simula.creative.qa import grounding, playthrough, tier
from simula.creative.schema import Claim, Proof
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
LINES = HostLines(intro="Three quick ones. Can you get them all?",
                  captions=["Find x", "What comes next?", "Unscramble the word"], right_line="That's it!",
                  wrong_hint="Not quite, try again", end_headline="Keep learning every day")
ONE_RUN = {"paths": ("right",), "widths": (390,), "modes": ("fast",)}


@pytest.fixture(scope="module")
def facts():
    return build_facts(RUN)


def content_for(facts, host_id="teacher", lines=LINES):
    return build_content(facts, next(h for h in facts.hosts if h.id == host_id), "challenge", 7, lines)


@pytest.fixture(scope="module")
def creative(facts, tmp_path_factory):
    content = content_for(facts)
    return write_variant(content, RUN, tmp_path_factory.mktemp("creative")), content


def test_both_hosts_pass_grounding_and_tier(facts):
    for host_id in ("teacher", "toki"):
        content = content_for(facts, host_id)
        assert grounding(content, facts) == []
        assert tier(content, facts) == ("sfw", [])


def test_s04_art_fails_tier(facts):
    content = content_for(facts)
    s04_art = content.model_copy(update={"host": content.host.model_copy(update={"art_ref": "s04.e04.art.png"})})
    assert tier(s04_art, facts) == (None, ["s04 is rated unsafe; only safe screens and art may feed a creative"])


def test_an_s04_proof_screen_fails_grounding_and_tier(facts):
    content = content_for(facts)
    s04_proof = content.model_copy(update={"proof": Proof(screen_id="s04", claims=content.proof.claims)})
    assert "proof screen s04 is not a safe-scope screen" in grounding(s04_proof, facts)
    assert tier(s04_proof, facts)[0] is None


@pytest.mark.parametrize("text, evidence_id", [
    ("I am Teacher, your personal tutor!", "s15.e02"),
    ("I am Teach", "s15.e02"),
    ("Ask me for advice, answers", "s15.e02"),
    ("Your best study buddy", "s15.e02"),
    ("Intimate", "s04.e26"),
])
def test_a_claim_that_is_not_an_exact_observed_string_fails_grounding(facts, text, evidence_id):
    content = content_for(facts)
    claimed = content.model_copy(update={"proof": Proof(screen_id="s15", claims=[Claim(text=text,
                                                                                         evidence_id=evidence_id)])})
    assert len(grounding(claimed, facts)) == 1


def test_an_adult_keyword_in_a_generated_line_fails_tier(facts):
    content = content_for(facts, lines=LINES.model_copy(update={"end_headline": "Not for adult eyes"}))
    assert tier(content, facts) == (None, ['generated text uses the adult keyword "adult"'])


def test_a_clean_creative_passes_all_36_playthroughs(creative):
    html, content = creative
    runs = playthrough(html, content)
    assert len(runs) == 36
    assert {run: found for run, found in runs.items() if found} == {}


def test_an_injected_console_error_fails_the_bot(creative, tmp_path):
    html, content = creative
    broken = tmp_path / "creative.html"
    broken.write_text(html.read_text().replace("</body>", '<script>console.error("injected")</script></body>'))
    assert "console error: injected" in playthrough(broken, content, **ONE_RUN)["right/fast/390"]


def test_a_request_to_the_network_fails_the_bot(creative, tmp_path):
    html, content = creative
    leaky = tmp_path / "creative.html"
    leaky.write_text(html.read_text().replace("</body>", '<img src="https://example.com/pixel.png"></body>'))
    assert "network request: https://example.com/pixel.png" in playthrough(leaky, content, **ONE_RUN)["right/fast/390"]


def test_a_creative_that_ends_too_soon_fails_the_timed_right_path(creative, tmp_path):
    html, content = creative
    quick = tmp_path / "creative.html"
    quick.write_text(html.read_text().replace("proof: 4000", "proof: 0").replace("intro: 3000", "intro: 0"))
    runs = playthrough(quick, content, paths=("right",), widths=(390,), modes=("timers",))
    assert any("it must take 15-25 s" in problem for problem in runs["right/timers/390"])
