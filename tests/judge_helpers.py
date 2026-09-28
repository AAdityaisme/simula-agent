"""Fake verdicts and priced candidates for judge tests. App-neutral: every builder takes a golden model."""

from simula.contracts import (GATES, JUDGMENT, Candidate, CandidateDraft, CandidatesFile, Check, Economics, LensOutput,
                              Verdict)
from simula.stages import Ctx, propose
from tests.propose_fixtures import candidate, golden

CHECKS = GATES + JUDGMENT


def verdict(fail=(), fixable: bool = False, cid: str = "x") -> Verdict:
    return Verdict(candidate_id=cid, other_concern="", fixable=fixable,
                   **{k: Check(passed=k not in fail, reason=f"{k} {'fails' if k in fail else 'passes'} here")
                      for k in CHECKS})


def priced(c: Candidate, econ: str = "PASS", rank: float = 1.0) -> Candidate:
    economics = Economics(cost_2k=0.0, cost_8k=0.0, breakeven_ecpm_2k=0.0, breakeven_ecpm_8k=0.0,
                          benchmark_ecpm=10.0, verdict=econ,
                          assumption_line="Serving cost not counted: no price observed. A rewarded view earns more.")
    return c.model_copy(update={"economics": economics, "rank_score": rank, "reach_score": rank})


def idea(model, cid: str = "c01", econ: str = "PASS", rank: float = 1.0, **changes) -> Candidate:
    return priced(candidate(model, id=cid, **changes), econ, rank)


# ---------- a run folder and a fake model for whole-stage tests ----------

def ctx_for(app, run_dir) -> Ctx:
    return Ctx(app={"name": app, "package": "x"}, run_dir=run_dir, profile="dev", no_cache=False, replay=False,
               usd_cap=None, allow_fixtures=True)


def seed(tmp_path, app, candidates):
    run_dir = tmp_path / app
    (run_dir / "model").mkdir(parents=True)
    (run_dir / "propose").mkdir()
    (run_dir / "model" / "product_model.json").write_text(golden(app).model_dump_json())
    (run_dir / "propose" / "candidates.json").write_text(CandidatesFile(candidates=candidates).model_dump_json())
    return run_dir


def fake_llm(rules, revision=None):
    """A stand-in for llm.call: a judge fails the checks `rules` maps a marker in the proposal text to; the
    reviser returns `revision`."""
    calls = []

    def call(**kw):
        text = kw["messages"][0]["content"][0]["text"]
        calls.append(kw)
        if kw["schema"] is Verdict:
            proposal = text.split("## Proposal", 1)[1]
            fails = [k for marker, checks in rules.items() if marker in proposal for k in checks]
            return verdict(fails, fixable=True), None
        drafts = [CandidateDraft(**revision.model_dump(include=set(CandidateDraft.model_fields)))] if revision else []
        return LensOutput(candidates=drafts), None
    return call, calls


def live(app, *changes):
    m = golden(app)
    drafts = [idea(m, f"x{n}", **{"title": f"Idea {n}", **ch}) for n, ch in enumerate(changes, 1)]
    return propose.finish(drafts, m, "annotate")[0]
