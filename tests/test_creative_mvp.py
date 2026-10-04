import csv
import importlib.util
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from simula import llm
from simula.creative import th1
from simula.creative.assemble import visible_strings
from simula.creative.schema import Content, CreativeAttributes
from simula.runlog import trace
from tests.conftest import ROOT

RUN = ROOT / "runs" / "luzia" / "20260929-204554-1f19585"
CLEAN = {"intro": "Three quick ones. Can you get them all?",
         "captions": ["Find x", "What comes next?", "Unscramble the word"], "right_line": "That's it!",
         "wrong_hint": "Not quite, try again", "end_headline": "Keep learning every day"}
ONE_RUN = {"paths": ("right",), "widths": (390,), "modes": ("fast",)}
DECISIONS = [{"row": 1, "n": 4, "e": 1, "p": [0.95, 0.05, 0.0, 0.0], "u": [0, 0, 1, 1], "c14": [0, 0, 1, 0], "gap": 0.02},
             {"row": 2, "n": 2, "e": 2, "p": [0.975, 0.025], "u": [1, 0], "c14": [1, 0], "gap": 0.0},
             {"row": 3, "n": 2, "e": 1, "p": [1.0, 0.0], "u": [0, 0], "c14": [0, 0], "gap": None}]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_starvation(out_dir, *, requests=10_000, seed=20261004, **_):
    """What th1.starvation writes for `requests` requests, without TH1: DECISIONS repeated with fresh row numbers (the
    tests ask for exactly those three), and th1_starvation's own summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [{**DECISIONS[i % len(DECISIONS)], "row": i + 1} for i in range(requests)]
    (out_dir / "starvation_decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in records))
    summary = load_tool("th1_starvation").summarize(
        records, seed=seed, feature_set="B", rows_all=1_000_000,
        windows={"refit": ["2014-10-21", "2014-10-29"], "test": ["2014-10-30", "2014-10-30"]},
        policy={"max_candidates": 50})
    (out_dir / "starvation.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(th1, "starvation", fake_starvation)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", lambda model, *args, **kwargs: seen.append(model) or llm.Reply(
        text=json.dumps(CLEAN), model=model, tokens_in=100, tokens_out=10))
    return seen


@pytest.fixture
def mvp():
    return load_tool("creative_mvp")


def run(mvp, tmp_path):
    return mvp.run(RUN, tmp_path / "out", seed=7, cache_dir=tmp_path / "cache", qa=ONE_RUN, requests=3)


def fill_review(mvp, out, first_verdict="edit"):
    """Two reviewers' sheets, merged: fable gives the first string first_verdict, astra says ok to every string."""
    sheets = {}
    for name, verdict in (("fable", first_verdict), ("astra", "ok")):
        rows = [dict(row, verdict="ok") for row in mvp.read_review(out)]
        rows[0]["verdict"], rows[0]["edit"] = verdict, "" if verdict == "ok" else "Sponsored mini-game"
        sheets[name] = write_rows(mvp, out.parent / f"{name}.csv", rows)
    mvp.merge(out, sheets)


def write_rows(mvp, path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=mvp.REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_run_finalize_and_check_recompute_every_number(mvp, calls, tmp_path):
    out = tmp_path / "out"
    report = run(mvp, tmp_path)
    assert (report["variants_total"], report["first_pass_accept"], report["final_accept"]) == (4, 4, None)
    assert report["status"] == "pending_review" and len(calls) == 4
    assert report["wrapper_min_propensity"] == pytest.approx(0.025)
    assert report["s04_fixture"]["rejected"] is True
    assert (report["exploration_set_mean"], report["pctr_gap_median"]) == (1.333, 0.01)
    assert mvp.check(out) == []
    contents = [Content.model_validate_json(p.read_text()) for p in sorted((out / "variants").glob("*/content.json"))]
    assert len(mvp.read_review(out)) == sum(len(visible_strings(c)) for c in contents) > 0
    record = CreativeAttributes.model_validate_json((out / "variants" / "teacher-challenge" / "creative.json").read_text())
    assert (record.gate_tier, record.qa.review_verdict, record.qa.reviewers) == ("suggestive", "pending", [])
    assert json.loads((out / "variants" / "toki-help_host" / "adset.json").read_text())["character_names"] == ["Toki"]
    fill_review(mvp, out)
    final = mvp.finalize(out)
    assert (final["status"], final["final_accept"], final["review_edits"]) == ("final", 4, 1)
    assert final["reviewers"] == ["fable", "astra"]
    record = CreativeAttributes.model_validate_json((out / "variants" / "teacher-challenge" / "creative.json").read_text())
    assert (record.qa.review_verdict, record.qa.reviewers) == ("accepted_with_edits", ["fable", "astra"])
    assert final["usd_per_accepted"] == pytest.approx(final["usd_total"] / 4, abs=1e-4)
    assert mvp.check(out) == []
    md = (out / "report.md").read_text()
    assert "On 4 variants, 4 passed every automatic check on the first draft and 4 after" in md
    assert "a read of every string by the model reviewers fable and astra, at $" in md
    assert "No person reads the strings" in md
    assert "human" not in md.lower() + (out / "report.json").read_text().lower()


def test_a_rejected_string_fails_its_variant(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out, first_verdict="reject")
    final = mvp.finalize(out)
    assert (final["final_accept"], final["review_edits"]) == (3, 0)
    rejected = mvp.read_review(out)[0]["variant"]
    assert json.loads((out / "variants" / rejected / "creative.json").read_text())["status"] == "failed"


def test_check_catches_a_hand_edited_number(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    md = (out / "report.md").read_text()
    (out / "report.md").write_text(md.replace("| variants_total | 4 |", "| variants_total | 5 |"))
    assert mvp.check(out) == ["report.md is not what report.json renders to"]


def test_a_rerun_counts_what_earlier_runs_spent_against_the_6_dollar_cap(mvp, calls, tmp_path):
    trace(tmp_path / "out" / "trace.jsonl", stage="creative", step="an earlier run", decider="model", usd=5.999)
    with pytest.raises(llm.CapReached):
        run(mvp, tmp_path)
    assert not (tmp_path / "out" / ".run.lock").exists()
    assert calls == []


def test_a_rerun_refuses_to_wipe_a_review_with_verdicts(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out)
    with pytest.raises(SystemExit, match="holds verdicts"):
        run(mvp, tmp_path)


def test_a_rerun_resumes_from_the_saved_drafts_without_calling_the_model(mvp, calls, tmp_path):
    first = run(mvp, tmp_path)
    (tmp_path / "cache").rename(tmp_path / "old-cache")
    assert run(mvp, tmp_path) == first and len(calls) == 4
    assert mvp.check(tmp_path / "out") == []


def snapshot(out):
    return {p: p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_a_rerun_with_another_seed_refuses_the_saved_drafts(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    before = snapshot(out)
    with pytest.raises(SystemExit, match="new --out"):
        mvp.run(RUN, out, seed=8, cache_dir=tmp_path / "cache", qa=ONE_RUN, requests=3)
    assert len(calls) == 4 and snapshot(out) == before


def test_a_rerun_on_a_changed_template_touches_nothing(mvp, calls, tmp_path, monkeypatch):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    before = snapshot(out)
    template = tmp_path / "quick_puzzles.html"
    template.write_text(mvp.TEMPLATE.read_text() + "<!-- changed -->")
    monkeypatch.setattr(mvp, "TEMPLATE", template)
    with pytest.raises(SystemExit, match="new --out"):
        run(mvp, tmp_path)
    assert len(calls) == 4 and snapshot(out) == before


@pytest.mark.parametrize("damage", ["drop a variant's rows", "blank a verdict"])
def test_finalize_refuses_a_review_that_misses_a_verdict(mvp, calls, tmp_path, damage):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out, first_verdict="ok")
    rows = mvp.read_review(out)
    if damage == "drop a variant's rows":
        rows = [row for row in rows if row["variant"] != rows[0]["variant"]]
    else:
        rows[-1]["verdict"] = ""
    write_rows(mvp, out / "review.csv", rows)
    with pytest.raises(SystemExit, match="review.csv"):
        mvp.finalize(out)
    assert all(json.loads(p.read_text())["qa"]["review_verdict"] == "pending"
               for p in (out / "variants").glob("*/creative.json"))


def test_check_catches_a_verdict_changed_after_finalize(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out, first_verdict="ok")
    mvp.finalize(out)
    rows = mvp.read_review(out)
    rows[0]["verdict"] = "reject"
    write_rows(mvp, out / "review.csv", rows)
    found = mvp.check(out)
    assert [line for line in found if line.startswith(rows[0]["variant"])]
    assert [line for line in found if line.startswith("review.csv is not the merge of the reviewers' sheets")]


def test_merge_keeps_the_strictest_verdict_and_every_note(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    pairs = [("ok", "ok", "ok"), ("ok", "edit", "edit"), ("edit", "reject", "reject"), ("reject", "ok", "reject"),
             ("edit", "edit", "edit")]
    sheets = {}
    for i, name in enumerate(("fable", "astra")):
        rows = [dict(row, verdict="ok") for row in mvp.read_review(out)]
        for row, pair in zip(rows, pairs):
            row["verdict"], row["edit"] = pair[i], "" if pair[i] == "ok" else f"{name} note"
        sheets[name] = write_rows(mvp, tmp_path / f"{name}.csv", rows)
    mvp.merge(out, sheets)
    merged = mvp.read_review(out)
    assert [row["verdict"] for row in merged[:5]] == [pair[2] for pair in pairs]
    assert [row["edit"] for row in merged[:5]] == ["", "astra: astra note", "fable: fable note; astra: astra note",
                                                   "fable: fable note", "fable: fable note; astra: astra note"]
    assert all(row["verdict"] == "ok" for row in merged[5:])
    assert json.loads((out / "reviewers.json").read_text()) == {"fable": "review-fable.csv",
                                                                 "astra": "review-astra.csv"}
    assert mvp.check(out) == []


def test_merge_refuses_a_sheet_that_misses_a_row(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    rows = [dict(row, verdict="ok") for row in mvp.read_review(out)]
    sheets = {"fable": write_rows(mvp, tmp_path / "fable.csv", rows),
              "astra": write_rows(mvp, tmp_path / "astra.csv", rows[1:])}
    with pytest.raises(SystemExit, match="astra.*missing 1"):
        mvp.merge(out, sheets)
    assert not (out / "reviewers.json").exists() and not any(row["verdict"] for row in mvp.read_review(out))


def test_finalize_refuses_before_merge(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    write_rows(mvp, out / "review.csv", [dict(row, verdict="ok") for row in mvp.read_review(out)])
    with pytest.raises(SystemExit, match="run merge first"):
        mvp.finalize(out)


def test_check_catches_a_changed_starvation_decision(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    changed = [{**DECISIONS[0], "u": [0, 0, 0, 1]}, *DECISIONS[1:]]
    (out / "starvation" / "starvation_decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in changed))
    assert [line for line in mvp.check(out) if line.startswith("wrapper.json")]


def test_check_says_the_wrapper_was_not_rechecked_without_the_decisions_file(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    (out / "starvation" / "starvation_decisions.jsonl").unlink()
    assert mvp.check(out) == ["wrapper.json and starvation.json not rechecked: starvation/starvation_decisions.jsonl "
                              "is git-ignored and absent here"]


def test_finalize_refuses_a_review_csv_that_is_not_the_merge(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out, first_verdict="reject")
    rows = mvp.read_review(out)
    rows[0]["verdict"] = "ok"
    write_rows(mvp, out / "review.csv", rows)
    with pytest.raises(SystemExit, match="not the merge.*first differing row"):
        mvp.finalize(out)
    assert all(json.loads(p.read_text())["qa"]["review_verdict"] == "pending"
               for p in (out / "variants").glob("*/creative.json"))
    assert [line for line in mvp.check(out) if line.startswith("review.csv is not the merge")]


def test_the_email_names_the_reviewers_only_once_they_have_read(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    md = (out / "report.md").read_text()
    assert "a read of every string by model reviewers (still pending), at $pending" in md and "Fable" not in md
    rows = [dict(row, verdict="ok") for row in mvp.read_review(out)]
    noted = [dict(rows[0], edit="fine as is"), *rows[1:]]
    mvp.merge(out, {"Claude Fable 5.1": write_rows(mvp, tmp_path / "fable.csv", rows),
                    "GPT-6 Astra": write_rows(mvp, tmp_path / "astra.csv", noted)})
    mvp.finalize(out)
    md = (out / "report.md").read_text()
    assert "a read of every string by the model reviewers Claude Fable 5.1 and GPT-6 Astra, at $" in md
    assert mvp.check(out) == []


def test_a_rerun_refuses_while_reviewer_sheets_remain(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out)
    (out / "review.csv").rename(tmp_path / "moved.csv")
    with pytest.raises(SystemExit, match=r"move reviewers.json and review-\*.csv away"):
        run(mvp, tmp_path)
    assert (out / "reviewers.json").exists() and (out / "review-fable.csv").exists()


def copy_run(tmp_path):
    """The parts of the committed run that facts, assembly and QA read, copied so a test can change them."""
    copy = tmp_path / "run"
    for name in ("model", "qa/approved/assets"):
        shutil.copytree(RUN / name, copy / name)
    for name in ("manifest.json", "qa/qa_report.json", "qa/approved/index.html"):
        (copy / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(RUN / name, copy / name)
    return copy


def test_a_second_run_refuses_while_the_first_holds_the_lock(mvp, calls, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def held(model, *args, **kwargs):
        entered.set()
        release.wait(timeout=60)
        return llm.Reply(text=json.dumps(CLEAN), model=model, tokens_in=100, tokens_out=10)

    monkeypatch.setitem(llm.PROVIDERS, "anthropic", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(run, mvp, tmp_path)
        assert entered.wait(timeout=60)
        try:
            with pytest.raises(SystemExit, match=r"another run is writing .*remove .*\.run\.lock"):
                run(mvp, tmp_path)
        finally:
            release.set()
        assert first.result(timeout=300)["variants_total"] == 4
    assert not (tmp_path / "out" / ".run.lock").exists()


def test_a_rerun_after_a_changed_proof_screenshot_touches_nothing(mvp, calls, tmp_path):
    source, out = copy_run(tmp_path), tmp_path / "out"
    mvp.run(source, out, seed=7, cache_dir=tmp_path / "cache", qa=ONE_RUN, requests=3)
    before = snapshot(out)
    shot = source / "model" / "states" / "s15.png"
    with Image.open(shot) as image:
        size = image.size
    Image.new("RGB", size, "black").save(shot)
    with pytest.raises(SystemExit, match="new --out"):
        mvp.run(source, out, seed=7, cache_dir=tmp_path / "cache", qa=ONE_RUN, requests=3)
    assert len(calls) == 4 and snapshot(out) == before


def test_a_rerun_under_a_changed_content_config_touches_nothing(mvp, calls, tmp_path, monkeypatch):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    before = snapshot(out)
    profiles = mvp.config.profiles()
    profiles["content"]["adult_keywords"].append("learning")
    monkeypatch.setattr(mvp.config, "profiles", lambda: profiles)
    with pytest.raises(SystemExit, match="new --out"):
        run(mvp, tmp_path)
    assert len(calls) == 4 and snapshot(out) == before


def test_a_refused_interrupted_rerun_leaves_every_file_as_it_was(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    (out / "drafts" / "teacher-help_host" / "round0" / "draft.json").unlink()
    before = snapshot(out)
    with pytest.raises(FileExistsError):
        run(mvp, tmp_path)
    assert len(calls) == 4 and snapshot(out) == before


def test_merge_refuses_one_sheet_given_as_two_reviewers(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    sheet = write_rows(mvp, tmp_path / "fable.csv", [dict(row, verdict="ok") for row in mvp.read_review(out)])
    with pytest.raises(SystemExit, match="same sheet"):
        mvp.merge(out, {"Claude Fable 5.1": sheet, "GPT-6 Astra": tmp_path / "." / "fable.csv"})
    assert not (out / "reviewers.json").exists()


def test_two_reviewers_who_both_approve_everything_merge(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    rows = [dict(row, verdict="ok") for row in mvp.read_review(out)]
    mvp.merge(out, {"Claude Fable 5.1": write_rows(mvp, tmp_path / "fable.csv", rows),
                    "GPT-6 Astra": write_rows(mvp, tmp_path / "astra.csv", rows)})
    assert mvp.finalize(out)["final_accept"] == 4 and mvp.check(out) == []


@pytest.mark.parametrize("edit", ["a reviewer list beside the sheets", "a name that is not its sheet's"])
def test_finalize_and_check_refuse_reviewer_metadata_of_another_shape(mvp, calls, tmp_path, edit):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    fill_review(mvp, out)
    mapping = json.loads((out / "reviewers.json").read_text())
    if edit == "a reviewer list beside the sheets":
        mapping = {"reviewers": ["Uninvoked Model"], "files": mapping}
    else:
        mapping["Uninvoked Model"] = mapping.pop("astra")
    (out / "reviewers.json").write_text(json.dumps(mapping))
    with pytest.raises(SystemExit, match="reviewers.json"):
        mvp.finalize(out)
    assert [line for line in mvp.check(out) if "reviewers.json" in line]


def test_an_html_edited_after_run_refuses_finalize_and_fails_check(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    path = out / "variants" / "teacher-challenge" / "creative.html"
    path.write_text(path.read_text().replace(CLEAN["intro"], "The app is free and guarantees better grades."))
    fill_review(mvp, out)
    with pytest.raises(SystemExit, match="teacher-challenge.*creative.html"):
        mvp.finalize(out)
    assert [line for line in mvp.check(out) if line.startswith("teacher-challenge: creative.html")]


def test_check_recomputes_the_starvation_numbers_from_the_decisions(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    changed = [DECISIONS[0], {**DECISIONS[1], "p": [0.0, 1.0], "e": 8, "gap": 0.8}, DECISIONS[2]]
    (out / "starvation" / "starvation_decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in changed))
    assert [line for line in mvp.check(out) if line.startswith("starvation.json")]


def test_variant_numbers_come_from_the_saved_drafts(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    path = out / "variants" / "teacher-challenge" / "result.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "first_pass_accept": False, "seconds": 10000.0,
                                "usd": 999.0}))
    fill_review(mvp, out)
    final = mvp.finalize(out)
    assert final["first_pass_accept"] == 4 and final["variants"][0]["usd"] < 1 and final["seconds_per_variant"] < 100
    assert [line for line in mvp.check(out) if line.startswith("teacher-challenge: result.json")]


def test_check_catches_drafts_that_claim_more_spend_than_the_trace(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    path = out / "drafts" / "toki-challenge" / "round0" / "draft.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "usd": 999.0}))
    assert [line for line in mvp.check(out) if line.startswith("the drafts spent")]


def test_check_passes_on_an_out_folder_moved_after_run(mvp, calls, tmp_path):
    run(mvp, tmp_path)
    shutil.copytree(tmp_path / "out", tmp_path / "moved")
    assert mvp.check(tmp_path / "moved") == []


def test_check_passes_a_clean_run_whose_spend_rounds_down(mvp, calls, tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", lambda model, *args, **kwargs: llm.Reply(
        text=json.dumps(CLEAN), model=model, tokens_in=1001, tokens_out=210))
    assert run(mvp, tmp_path)["usd_total"] == 0.0164
    assert mvp.check(tmp_path / "out") == []


def test_check_allows_the_trace_rounding_each_call_to_six_places(mvp, calls, tmp_path, monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", lambda model, *args, **kwargs: llm.Reply(
        text=json.dumps(CLEAN), model=model, tokens_in=1001, tokens_out=210, tokens_cached=1))
    run(mvp, tmp_path)
    assert mvp.results(tmp_path / "out")["teacher-challenge"].usd == pytest.approx(0.0041002)
    assert mvp.check(tmp_path / "out") == []


def test_merge_refuses_two_hardlinks_to_one_sheet(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    sheet = write_rows(mvp, tmp_path / "fable.csv", [dict(row, verdict="ok") for row in mvp.read_review(out)])
    os.link(sheet, tmp_path / "astra.csv")
    with pytest.raises(SystemExit, match="same sheet"):
        mvp.merge(out, {"Claude Fable 5.1": sheet, "GPT-6 Astra": tmp_path / "astra.csv"})


def test_check_catches_a_starvation_json_from_another_seed(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    path = out / "starvation" / "starvation.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "sample_seed": 7}))
    assert [line for line in mvp.check(out) if line.startswith("starvation.json samples")]


def test_check_catches_a_reused_starvation_json_for_another_request_count(mvp, calls, tmp_path):
    out = tmp_path / "out"
    run(mvp, tmp_path)
    mvp.run(RUN, out, seed=7, cache_dir=tmp_path / "cache", qa=ONE_RUN, requests=5)
    assert [line for line in mvp.check(out) if line.startswith("starvation.json samples")]
