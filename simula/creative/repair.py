"""One variant through generation, code checks and at most one repair round: the host lines are written and checked,
then the creative is assembled, grounded, tiered and played; a draft that fails anything goes back to the model once
with every concrete failure. Every draft is kept under drafts/<variant>/round<n>/."""

import time
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from simula import llm
from simula.contracts import Strict
from simula.creative.assemble import write_variant
from simula.creative.facts import Facts, Host
from simula.creative.lines import HostLines, build_content, problems, write_lines
from simula.creative.puzzles import puzzles
from simula.creative.qa import MODES, PATHS, WIDTHS, grounding, playthrough, tier
from simula.creative.schema import Hook, Tier


class Draft(Strict):
    round: int
    lines: HostLines | None
    assembled: bool
    content_tier: Tier | None
    lines_failures: list[str]
    grounding_failures: list[str]
    tier_failures: list[str]
    playthrough: dict[str, list[str]]

    def failures(self) -> list[str]:
        """Every failure of this draft, as the repair call receives them."""
        return [*self.lines_failures, *self.grounding_failures, *self.tier_failures,
                *(f"playthrough {run}: {problem}" for run, found in self.playthrough.items() for problem in found)]


class VariantResult(Strict):
    variant_id: str
    host_id: str
    hook: Hook
    drafts: list[Draft]
    first_pass_accept: bool
    repair_round: Literal[0, 1]
    passed: bool
    playthrough_pass: bool
    grounding_pass: bool
    tier_pass: bool
    content_tier: Tier | None
    final_dir: str | None
    seconds: float
    usd: float


def _check(round_: int, lines: HostLines, facts: Facts, host: Host, hook: Hook, seed: int, run_dir: Path, out: Path,
           qa: dict) -> Draft:
    (out / "lines.json").write_text(lines.model_dump_json(indent=1))
    found = problems(lines, facts, host, puzzles(seed))
    if found:
        return Draft(round=round_, lines=lines, assembled=False, content_tier=None, lines_failures=found,
                     grounding_failures=[], tier_failures=[], playthrough={})
    try:
        content = build_content(facts, host, hook, seed, lines)
    except ValidationError as e:
        return Draft(round=round_, lines=lines, assembled=False, content_tier=None,
                     lines_failures=[f"{e.title}.{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()],
                     grounding_failures=[], tier_failures=[], playthrough={})
    html = write_variant(content, run_dir, out)
    content_tier, tier_failures = tier(content, facts)
    return Draft(round=round_, lines=lines, assembled=True, content_tier=content_tier, lines_failures=[],
                 grounding_failures=grounding(content, facts), tier_failures=tier_failures,
                 playthrough=playthrough(html, content, **qa))


def generate_variant(*, facts: Facts, host: Host, hook: Hook, seed: int, run_dir: Path, drafts_dir: Path,
                     budget: llm.Budget, trace_path: Path, cache_dir: Path = llm.CACHE,
                     paths: tuple[str, ...] = tuple(PATHS), widths: tuple[int, ...] = WIDTHS,
                     modes: tuple[str, ...] = MODES) -> VariantResult:
    """Generates one variant: a first draft, and a repair round only when the first draft fails a check. A failed
    host-line call, or a host the content can't hold, is a failure like any other. A rerun resumes from the saved
    drafts: it never rewrites a saved round and calls the model only for a round still owed. paths, widths and modes
    narrow the playthrough (tests use one run). llm.CapReached propagates: the $ cap stops the whole MVP run."""
    variant_id, started, spent = f"{host.id}-{hook}", time.monotonic(), budget.spent
    qa = {"paths": paths, "widths": widths, "modes": modes}
    drafts: list[Draft] = []
    for round_ in (0, 1):
        if drafts and not drafts[-1].failures():
            break
        out = drafts_dir / variant_id / f"round{round_}"
        if (out / "draft.json").exists():
            drafts.append(Draft.model_validate_json((out / "draft.json").read_text()))
            continue
        previous = drafts[-1] if drafts else None
        try:
            lines = write_lines(facts=facts, host=host, hook=hook, puzzles=puzzles(seed), budget=budget,
                                trace_path=trace_path, cache_dir=cache_dir,
                                previous=previous.lines if previous else None,
                                failures=previous.failures() if previous else None)
        except llm.LLMFailure as e:
            lines, failed = None, f"the host-line call failed: {e.outcome}"
        out.mkdir(parents=True)
        draft = _check(round_, lines, facts, host, hook, seed, run_dir, out, qa) if lines else Draft(
            round=round_, lines=None, assembled=False, content_tier=None, lines_failures=[failed],
            grounding_failures=[], tier_failures=[], playthrough={})
        (out / "draft.json").write_text(draft.model_dump_json(indent=1))
        drafts.append(draft)
    last = drafts[-1]
    return VariantResult(
        variant_id=variant_id, host_id=host.id, hook=hook, drafts=drafts, first_pass_accept=not drafts[0].failures(),
        repair_round=len(drafts) - 1, passed=not last.failures(),
        playthrough_pass=last.assembled and not any(last.playthrough.values()),
        grounding_pass=last.assembled and not last.grounding_failures,
        tier_pass=last.assembled and not last.tier_failures, content_tier=last.content_tier,
        final_dir=str(drafts_dir / variant_id / f"round{last.round}") if last.assembled else None,
        seconds=time.monotonic() - started, usd=budget.spent - spent)
