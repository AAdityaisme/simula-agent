import ast
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from simula.creative import th1
from tests.conftest import ROOT

TH1_MODULES = {"simula.data", "simula.rank", "simula.train"}


def starvation_tool():
    spec = importlib.util.spec_from_file_location("th1_starvation", ROOT / "tools" / "th1_starvation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", ["th1_bridge.py", "th1_starvation.py"])
def test_the_th1_scripts_import_nothing_from_exp_connect(script):
    tree = ast.parse((ROOT / "tools" / script).read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert {m for m in modules if m.split(".")[0] == "simula"} <= TH1_MODULES
    assert {m.split(".")[0] for m in modules} <= {"simula", *sys.stdlib_module_names}


def test_th1_runs_under_its_own_project_without_writing_in_its_tree(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    assert th1.command("th1_bridge.py", "--model", "m") == [
        "uv", "run", "--no-sync", "--project", str(th1.TH1), "python", str(ROOT / "tools" / "th1_bridge.py"),
        "--model", "m"]
    env = th1.env()
    assert env["PYTHONPATH"] == str(th1.TH1) and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "VIRTUAL_ENV" not in env


def test_the_th1_paths_come_from_the_environment_resolved_from_the_callers_directory(tmp_path):
    probe = ("from simula.creative import th1; import json; "
             "print(json.dumps([th1.command('x.py')[4], th1.env()['PYTHONPATH'], str(th1.DATA), str(th1.BUNDLE)]))")
    env = {**os.environ, "SIMULA_TH1": os.path.relpath(tmp_path / "th1", ROOT),
           "SIMULA_TH1_DATA": os.path.relpath(tmp_path / "data", ROOT)}
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    th1_dir, data = (tmp_path / "th1").resolve(), (tmp_path / "data").resolve()
    assert json.loads(done.stdout) == [str(th1_dir), str(th1_dir), str(data), str(th1_dir / "bundle")]


def test_wilson_matches_known_intervals():
    tool = starvation_tool()
    assert tool.wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    assert tool.wilson(0, 10) == pytest.approx((0.0, 0.2775), abs=1e-4)


def test_a_tuple_key_round_trips_to_a_rank_candidate_and_surfaces_follow_the_placeholder():
    tool = starvation_tool()
    assert tool.candidate("0|15702|320|50|1722|0|35|-1|79") == {
        "candidate_id": "0|15702|320|50|1722|0|35|-1|79", "content_tier": "sfw", "banner_pos": 0, "C14": 15702,
        "C15": 320, "C16": 50, "C17": 1722, "C18": 0, "C19": 35, "C20": "-1", "C21": 79}
    assert tool.surface("85f751fd", "app1") == "app1"
    assert tool.surface("site1", "ecad2386") == "site1"


def test_the_summary_counts_only_requests_with_a_seen_and_an_unseen_tuple():
    tool = starvation_tool()
    records = [
        {"row": 1, "n": 2, "e": 1, "p": [1.0, 0.0], "u": [0, 1], "c14": [0, 1], "gap": 0.1},
        {"row": 2, "n": 2, "e": 2, "p": [0.975, 0.025], "u": [0, 1], "c14": [0, 1], "gap": 0.004},
        {"row": 3, "n": 2, "e": 1, "p": [0.95, 0.05], "u": [1, 1], "c14": [1, 1], "gap": 0.0},
        {"row": 4, "n": 2, "e": 1, "p": [1.0, 0.0], "u": [0, 0], "c14": [0, 0], "gap": None},
    ]
    summary = tool.summarize(records, seed=7, feature_set="B", rows_all=10, windows={"refit": (1, 2), "test": (3, None)},
                             policy={"max_candidates": 50})
    assert (summary["starvation_requests"], summary["starved"], summary["starvation_share"]) == (2, 1, 0.5)
    assert summary["starvation_ci"] == [round(v, 4) for v in tool.wilson(1, 2)]
    assert summary["exploration_set_size"] == {"mean": 1.25, "histogram": {"1": 3, "2": 1}}
    assert summary["unseen_tuples_with_unseen_C14_share"] == 1.0 and summary["seen_tuples_with_unseen_C14_share"] == 0.0
    gap = summary["pctr_gap_top_unseen_to_leader"]
    assert 0.004 <= gap["p10"] <= gap["median"] <= gap["p90"] <= 0.1, gap


@pytest.mark.live
def test_the_bridge_reproduces_th1s_committed_rank_sample():
    payloads = json.loads((th1.TH1 / "fixtures" / "rank_requests.json").read_text())
    want = json.loads((th1.TH1 / "reports" / "rank_sample.json").read_text())
    got = th1.bridge(payloads)
    assert [d["id"] for d in got] == [d["id"] for d in want]
    for mine, theirs in zip(got, want):
        assert mine["selected_candidate_id"] == theirs["selected_candidate_id"], mine["id"]
        assert {c["candidate_id"]: c["selection_probability"] for c in mine["candidates"]} == pytest.approx(
            {c["candidate_id"]: c["selection_probability"] for c in theirs["candidates"]}, abs=1e-12)


@pytest.mark.live
def test_starvation_on_a_small_sample_is_consistent_with_its_records(tmp_path):
    summary = th1.starvation(tmp_path, requests=300)
    records = list(th1.decisions(tmp_path))
    assert summary["requests_sampled"] == len(records) == 300
    assert summary["bundle"] == {"feature_set": "B", "rows_all": 1_000_000}
    both = [r for r in records if any(r["u"]) and not all(r["u"])]
    starved = [r for r in both if all(p == 0 for p, u in zip(r["p"], r["u"]) if u)]
    assert (summary["starvation_requests"], summary["starved"]) == (len(both), len(starved))
    low, high = summary["starvation_ci"]
    assert low <= summary["starvation_share"] <= high
    assert all(abs(sum(r["p"]) - 1) < 1e-9 and 1 <= r["n"] <= 50 for r in records)
