"""The editor call: the one model call per idea that adds the idea's screens as find/replace edits."""

import re

from simula import config, llm
from simula.config import ROOT
from simula.contracts import Candidate, Edit, Edits, ProductModel
from simula.runlog import run_trace
from simula.stages import Ctx
from simula.stages.flows.wording import caption, reward_line
from simula.stages.mock import contract_text
from simula.stages.propose import BUCKETS

PROMPTS = ROOT / "prompts" / "flows"
MAX_TOKENS = 16000


def system_prompt() -> str:
    parts = [p.read_text() for p in sorted(PROMPTS.glob("*.md"))]
    return "\n\n".join(parts + [contract_text()])


def editor_brief(c: Candidate, model: ProductModel, page: str) -> str:
    names = {s.id: s.name for s in model.states}
    steps = [f"{n}. {s.state_id} ({names.get(s.state_id, 'new screen')}): {s.caption}"
             for n, s in enumerate(c.flow_steps, 1)]
    drawn = [f"- {sid} {names.get(sid, '')}" for sid in re.findall(r'<section[^>]*data-screen="([^"]+)"', page)]
    new = list(dict.fromkeys(s.state_id for s in c.flow_steps if s.state_id.startswith("new:")))
    return "\n".join([
        f"Idea {c.id} ({BUCKETS.get(c.kind, c.kind)}): {caption(c)}", "",
        f"Trigger: {c.trigger_event}", f"Starts on: {c.trigger_state_id}", f"Placement of the offer: {c.placement}",
        f"Offer copy (verbatim): {c.offer_copy}", f"Reward: {reward_line(c)}",
        f"If they say no: {c.decline_path}", f"If the ad fails: {c.ad_fail_path}",
        f"When the reward runs out: {c.after_reward}", f"Subscribers: {c.subscriber_treatment}", "",
        "Steps, in order:", *steps, "", f"New screens to add, one section each (one of them is the ad): {', '.join(new)}",
        "", "Screens already in the page:", *drawn, "",
        "The page (code removed its navigation runtime and adds it back after your edits):",
        "```html", page, "```"])


def ask_editor(ctx: Ctx, c: Candidate, model: ProductModel, page: str, budget: llm.Budget) -> Edits | None:
    role = config.roles(ctx.profile)["flows_editor"]
    messages = [{"role": "user", "content": [{"type": "text", "text": editor_brief(c, model, page)}]}]
    try:
        edits, _ = llm.call(trace_path=ctx.run_dir / "trace.jsonl", stage="flows", step=f"edit:{c.id}",
                            model=role["model"], effort=role.get("effort"), system=system_prompt(), messages=messages,
                            max_tokens=min(MAX_TOKENS, config.models()[role["model"]]["max_out"]), budget=budget,
                            schema=Edits, no_cache=ctx.no_cache, replay=ctx.replay)
    except llm.LLMFailure as e:
        run_trace(ctx.run_dir, stage="flows", step=f"edit:{c.id}", decider="code", outcome=e.outcome,
                  note="edit call failed; the flow is walked on the unedited mock")
        return None
    return edits


def apply_edits(html: str, edits: list[Edit]) -> tuple[str, list[str]]:
    """Applies each edit whose find matches the page exactly once. Returns the page and why each other edit failed."""
    rejected = []
    for edit in edits:
        matches = html.count(edit.find) if edit.find else 0
        if matches == 1:
            html = html.replace(edit.find, edit.replace, 1)
        else:
            rejected.append(f"find matches the page {matches} times ({edit.reason[:80]})")
    return html, rejected
