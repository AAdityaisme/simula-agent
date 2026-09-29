import json

import pytest

from simula import runfolder
from simula.config import ROOT, STAGES
from simula.contracts import Provenance

REAL = Provenance(source="explorer_run", explorer_run_id="r1")


@pytest.fixture
def run(tmp_path):
    (tmp_path / "config.toml").write_text("package = 'x'\n")
    (tmp_path / "prompt.md").write_text("v1")
    stage = tmp_path / "model"
    stage.mkdir()
    (stage / "product_model.json").write_text("{}")
    return tmp_path


def mark(run, params=None, stage="model", inputs=None):
    stage_dir = run / stage
    inputs = inputs or [run / "config.toml"]
    runfolder.write_done(stage_dir, run, inputs, [run / "prompt.md"], params or {"effort": "high"}, [stage_dir], REAL)
    return stage_dir, inputs


def done(run, params=None, stage="model", inputs=None):
    return runfolder.is_done(runfolder.read_done(run / stage), run, inputs or [run / "config.toml"],
                             [run / "prompt.md"], params or {"effort": "high"})


def test_no_marker_means_not_done(run):
    assert not done(run)


def test_matching_hashes_skip(run):
    mark(run)
    assert done(run)


def test_changed_input_reruns(run):
    mark(run)
    (run / "config.toml").write_text("package = 'y'\n")
    assert not done(run)


def test_changed_prompt_reruns(run):
    mark(run)
    (run / "prompt.md").write_text("v2")
    assert not done(run)


def test_changed_params_rerun(run):
    mark(run)
    assert not done(run, params={"effort": "xhigh"})


def test_edited_or_deleted_output_reruns(run):
    mark(run)
    (run / "model" / "product_model.json").write_text('{"edited": true}')
    assert not done(run)
    mark(run)
    (run / "model" / "product_model.json").unlink()
    assert not done(run)


def test_rerunning_upstream_makes_downstream_stale(run):
    mark(run)
    (run / "mock").mkdir()
    (run / "mock" / "index.html").write_text("<html>")
    mark(run, stage="mock", inputs=[run / "model"])
    assert done(run, stage="mock", inputs=[run / "model"])
    (run / "model" / "product_model.json").write_text('{"new": 1}')
    assert not done(run, stage="mock", inputs=[run / "model"])


def test_half_written_marker_does_not_count(run):
    (run / "model" / "done.json.tmp").write_text("{}")
    assert not done(run)


def test_a_failure_record_leaves_the_marker_to_the_runner(run):
    mark(run)
    runfolder.write_failure(run / "model", "boom")
    assert (run / "model" / "done.json").exists() and (run / "model" / "failure.json").exists()


def test_same_second_runs_get_a_numbered_suffix(runs, monkeypatch):
    from datetime import datetime

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 28, 1, 2, 3)
    monkeypatch.setattr(runfolder, "datetime", Frozen)
    monkeypatch.setattr(runfolder, "git_sha", lambda: "abc1234")
    names = [runfolder.new_run("aol").name for _ in range(3)]
    assert names == ["20260928-010203-abc1234", "20260928-010203-abc1234-2", "20260928-010203-abc1234-3"]
    assert (runs / "aol" / "latest").resolve().name == names[-1]


def test_editing_a_file_outside_the_run_folder_reruns_the_stage(runs, tmp_path, monkeypatch):
    from simula import cli, stages
    knowledge = tmp_path / "bible.md"
    knowledge.write_text("v1")
    monkeypatch.setitem(stages.EXTRA_INPUTS, "propose", [str(knowledge)])
    cli.main(["run", "janitorai", "--new"])
    ctx = cli.open_run(cli.parser().parse_args(["propose", "janitorai"]))
    inputs = cli.stage_inputs("propose", ctx)
    assert knowledge in inputs
    (ctx.run_dir / "propose").mkdir()
    runfolder.write_done(ctx.run_dir / "propose", ctx.run_dir, inputs, [], {}, [ctx.run_dir / "propose"], REAL)
    marker = runfolder.read_done(ctx.run_dir / "propose")
    assert runfolder.is_done(marker, ctx.run_dir, cli.stage_inputs("propose", ctx), [], {})
    knowledge.write_text("v2")
    assert not runfolder.is_done(marker, ctx.run_dir, cli.stage_inputs("propose", ctx), [], {})


def test_real_stage_inputs_include_the_bible_and_the_contract():
    from simula.stages import EXTRA_INPUTS
    assert EXTRA_INPUTS["propose"] == EXTRA_INPUTS["judge"] == ["bible"]
    assert EXTRA_INPUTS["mock"] == EXTRA_INPUTS["qa"] == ["docs/CONTRACTS.md"]


def test_flows_reruns_when_the_mock_the_model_its_templates_or_the_contract_change(runs):
    from simula import cli
    cli.main(["run", "janitorai", "--new"])
    ctx = cli.open_run(cli.parser().parse_args(["flows", "janitorai"]))
    inputs = cli.stage_inputs("flows", ctx)
    for path in (ctx.run_dir / "mock", ctx.run_dir / "model", ROOT / "templates", ROOT / "docs" / "CONTRACTS.md"):
        assert path in inputs


def test_bytecode_caches_are_not_stage_inputs(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "lens.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / "stray.pyc").write_bytes(b"\x00")
    (tmp_path / "lens.py").write_text("x = 1")
    assert runfolder.expand([tmp_path]) == [tmp_path / "lens.py"]


# ---------- the code a stage runs ----------

PACKAGE = {  # a package shaped like the stages': judge and flows build on propose, qa and flows on mock
    "simula/__init__.py": "",
    "simula/llm.py": "",
    "simula/render.py": "",
    "simula/stages/__init__.py": "class Ctx: ...\n",
    "simula/stages/explore.py": "from simula.stages import Ctx\n",
    "simula/stages/model.py": "from simula import llm\nfrom simula.stages import Ctx\n",
    "simula/stages/mock.py": "import simula.render\nfrom simula import llm\n",
    "simula/stages/qa.py": "from simula.stages import mock\n",
    "simula/stages/propose.py": "from simula.llm import call\n",
    "simula/stages/judge.py": "def run(ctx):\n    from simula.stages.propose import rank\n",
    "simula/stages/flows.py": "from simula.stages import mock, propose\n",
    "README.md": "# simula\n",
}
STAGE_NAMES = ["explore", "model", "mock", "qa", "propose", "judge", "flows"]


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "checkout"
    for rel, text in PACKAGE.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def rerun_after_editing(root, rel):
    before = {s: runfolder.hashes(runfolder.code_files(s, root), root) for s in STAGE_NAMES}
    (root / rel).write_text((root / rel).read_text() + "# edited\n")
    return [s for s in STAGE_NAMES if runfolder.hashes(runfolder.code_files(s, root), root) != before[s]]


@pytest.mark.parametrize("rel, reruns", [
    ("simula/stages/propose.py", ["propose", "judge", "flows"]),
    ("simula/stages/mock.py", ["mock", "qa", "flows"]),
    ("simula/stages/flows.py", ["flows"]),
    ("simula/llm.py", ["model", "mock", "qa", "propose", "judge", "flows"]),
    ("simula/render.py", ["mock", "qa", "flows"]),
    ("simula/stages/__init__.py", STAGE_NAMES),
    ("README.md", []),
])
def test_editing_code_reruns_exactly_the_stages_that_run_it(package, rel, reruns):
    assert rerun_after_editing(package, rel) == reruns


def test_each_real_stage_hashes_its_own_module_and_its_package():
    """A stage split into a package of its own (flows) hashes every module in it, so editing any of them reruns it."""
    for stage in STAGE_NAMES:
        files = {str(f.relative_to(ROOT)) for f in runfolder.code_files(stage)}
        own = ROOT / "simula" / "stages" / stage
        modules = {f"{own.relative_to(ROOT)}.py"} if not own.is_dir() else \
            {str(m.relative_to(ROOT)) for m in own.glob("*.py")}
        assert modules | {"simula/stages/__init__.py", "simula/__init__.py"} <= files, stage


def test_a_code_change_reruns_a_finished_stage(run):
    code = run / "stage_code.py"
    code.write_text("RANK = 1\n")
    runfolder.write_done(run / "model", run, [run / "config.toml"], [run / "prompt.md"], {"effort": "high"},
                         [run / "model"], REAL, code=[code])
    marker = runfolder.read_done(run / "model")
    assert runfolder.is_done(marker, run, [run / "config.toml"], [run / "prompt.md"], {"effort": "high"}, code=[code])
    code.write_text("RANK = 2\n")
    assert not runfolder.is_done(marker, run, [run / "config.toml"], [run / "prompt.md"], {"effort": "high"},
                                 code=[code])


def test_a_marker_from_before_the_code_check_reruns(run):
    mark(run)
    marker = json.loads((run / "model" / "done.json").read_text())
    del marker["code_hashes"], marker["outcome"]
    (run / "model" / "done.json").write_text(json.dumps(marker))
    assert not runfolder.is_done(runfolder.read_done(run / "model"), run, [run / "config.toml"], [run / "prompt.md"],
                                 {"effort": "high"}, code=[run / "prompt.md"])


@pytest.mark.parametrize("stage", STAGES)
def test_no_stage_hashes_the_cli_or_the_checkout_tools(stage):
    """The CLI, doctor and checkout modules are bookkeeping: a stage that imports one would go stale on every edit
    to it, and a stale explore can't be replayed."""
    files = {str(p.relative_to(ROOT)) for p in runfolder.code_files(stage)}
    assert not files & {"simula/cli.py", "simula/doctor.py", "simula/checkout.py"}
