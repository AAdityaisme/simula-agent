import json
import shutil

import pytest

from simula.creative.facts import build_facts
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
SAFE = ["s01", "s02", "s03", "s05", "s06", "s07", "s08", "s09", "s11", "s13", "s14", "s15"]


def copy_run(tmp_path, edit=None):
    """The committed run's files facts reads, with the product model changed by edit."""
    run = tmp_path / "run"
    (run / "model").mkdir(parents=True)
    shutil.copy(RUN / "manifest.json", run / "manifest.json")
    model = json.loads((RUN / "model" / "product_model.json").read_text())
    if edit:
        edit(model)
    (run / "model" / "product_model.json").write_text(json.dumps(model))
    shutil.copytree(RUN / "qa" / "approved", run / "qa" / "approved")
    shutil.copy(RUN / "qa" / "qa_report.json", run / "qa" / "qa_report.json")
    return run


def undraw(run, sid):
    """Swaps sid's section for the placeholder stage 3 draws when a batch fails, and records it in the QA report."""
    mock = run / "qa" / "approved" / "index.html"
    html = mock.read_text()
    start = html.index(f'<section data-screen="{sid}"')
    end = html.index("</section>", start) + len("</section>")
    placeholder = f'<section data-screen="{sid}"><p>screen not drawn: refusal</p></section>'
    mock.write_text(html[:start] + placeholder + html[end:])
    report = run / "qa" / "qa_report.json"
    qa = json.loads(report.read_text())
    qa["undrawn_screens"] = [{"screen": sid, "reason": "refusal"}]
    report.write_text(json.dumps(qa))


def test_luzia_scope_unsafe_flag_hosts_and_allowed_strings():
    facts = build_facts(RUN)
    assert facts.safe_scope == SAFE
    assert facts.excluded == {"s04": "unsafe", "s10": "out_of_mock_scope", "s12": "out_of_mock_scope"}
    assert facts.has_unsafe_screens is True
    allowed = {a.evidence_id: a for a in facts.allowed}
    assert allowed["s15.e02"].text.startswith("Hello! I am Teacher, your personal tutor.")
    assert allowed["s01.e26"].text == "Meet Toki, your virtual pet!"
    assert allowed["s01.e26"].cited_by == ["host:toki", "m2"]
    assert "s06.e18" not in allowed
    assert all(a.screen in SAFE and a.text.strip() for a in facts.allowed)
    assert [h.id for h in facts.hosts] == ["teacher", "toki"]
    assert facts.terms == ["Toki", "Custom Bestie", "Routines", "Remix", "Luzia"]
    assert (facts.app_name, facts.app_package, facts.run_id) == ("Luzia", "co.thewordlab.luzia", "20260929-204554-1f19585")
    assert facts.ratings["s04"] == "unsafe" and facts.ratings["s15"] == "safe"


@pytest.mark.parametrize("status, allowed", [("unknown", False), ("observed", True)])
def test_a_string_only_an_unknown_mechanic_cites_is_not_allowed(tmp_path, status, allowed):
    def cite(model):
        m1 = next(m for m in model["mechanics"] if m["id"] == "m1")
        m1["evidence_ids"].append("s01.e08")
        m1["status"] = status
    facts = build_facts(copy_run(tmp_path, cite))
    assert ("s01.e08" in {a.evidence_id for a in facts.allowed}) is allowed


def test_a_state_the_approved_mock_does_not_draw_is_out_of_scope(tmp_path):
    run = copy_run(tmp_path)
    mock = run / "qa" / "approved" / "index.html"
    mock.write_text(mock.read_text().replace('<section data-screen="s07"', '<section data-gone="s07"'))
    facts = build_facts(run)
    assert facts.excluded["s07"] == "not_drawn" and "s07" not in facts.safe_scope


@pytest.mark.parametrize("attr, drawn", [("data-screen='s07'", True), ("data-screen=s07", True),
                                         ('data-screen = "s07"', True), ('data-old-data-screen="s07"', False)])
def test_a_screen_is_drawn_only_by_a_section_whose_data_screen_attribute_names_it(tmp_path, attr, drawn):
    run = copy_run(tmp_path)
    mock = run / "qa" / "approved" / "index.html"
    mock.write_text(mock.read_text().replace('data-screen="s07"', attr))
    assert ("s07" in build_facts(run).safe_scope) is drawn


def test_a_section_inside_an_html_comment_is_not_drawn(tmp_path):
    run = copy_run(tmp_path)
    mock = run / "qa" / "approved" / "index.html"
    html = mock.read_text()
    start = html.index('<section data-screen="s07"')
    end = html.index("</section>", start) + len("</section>")
    mock.write_text(html[:start] + "<!--" + html[start:end] + "-->" + html[end:])
    facts = build_facts(run)
    assert facts.excluded["s07"] == "not_drawn" and all(a.screen != "s07" for a in facts.allowed)


def test_a_placeholder_for_an_undrawn_screen_is_out_of_scope(tmp_path):
    run = copy_run(tmp_path)
    undraw(run, "s07")
    facts = build_facts(run)
    assert facts.excluded["s07"] == "not_drawn" and "s07" not in facts.safe_scope
    assert all(a.screen != "s07" for a in facts.allowed)


@pytest.mark.parametrize("hide", [lambda section: f"<template>{section}</template>",
                                  lambda section: section.replace('"s07"', '"s99" data-screen="s07"', 1)],
                         ids=["template", "duplicate_attribute"])
def test_a_screen_qa_found_missing_in_the_browser_is_out_of_scope(tmp_path, hide):
    run = copy_run(tmp_path)
    mock = run / "qa" / "approved" / "index.html"
    html = mock.read_text()
    start = html.index('<section data-screen="s07"')
    end = html.index("</section>", start) + len("</section>")
    mock.write_text(html[:start] + hide(html[start:end]) + html[end:])
    report = run / "qa" / "qa_report.json"
    qa = json.loads(report.read_text())
    qa["contract_errors"].append({"kind": "missing_screen", "detail": "no s07 section", "screen": "s07"})
    report.write_text(json.dumps(qa))
    facts = build_facts(run)
    assert facts.excluded["s07"] == "not_drawn" and all(a.screen != "s07" for a in facts.allowed)


def test_a_host_whose_proof_screen_is_undrawn_is_refused(tmp_path):
    run = copy_run(tmp_path)
    undraw(run, "s15")
    with pytest.raises(SystemExit, match="s15"):
        build_facts(run)


@pytest.mark.parametrize("text", ["I am Teacher, your personal tutor!", "am Teacher, your personal tu"])
def test_a_host_proof_pick_that_is_not_a_word_boundary_excerpt_of_its_element_is_refused(tmp_path, text):
    table = json.loads((ROOT / "simula" / "creative" / "hosts" / "luzia.json").read_text())
    table["hosts"][0]["proof"][0]["text"] = text
    path = tmp_path / "hosts.json"
    path.write_text(json.dumps(table))
    with pytest.raises(SystemExit, match="s15.e02"):
        build_facts(RUN, path)


def test_a_run_without_a_qa_report_is_refused(tmp_path):
    run = copy_run(tmp_path)
    (run / "qa" / "qa_report.json").unlink()
    with pytest.raises(SystemExit, match="qa_report.json"):
        build_facts(run)


def test_a_host_whose_art_comes_from_an_unsafe_screen_is_refused(tmp_path):
    table = json.loads((ROOT / "simula" / "creative" / "hosts" / "luzia.json").read_text())
    table["hosts"][0]["art"] = "s04.e04.art.png"
    path = tmp_path / "hosts.json"
    path.write_text(json.dumps(table))
    with pytest.raises(SystemExit, match="s04.e04"):
        build_facts(RUN, path)
