"""The host lines of one variant: one model call writes them (and at most one repair call rewrites them), code checks
them, and build_content puts them beside the code-made puzzles, proof and call to action."""

import json
import re
from pathlib import Path

from simula import config, llm
from simula.config import ROOT
from simula.contracts import Strict
from simula.creative.facts import Facts, Host
from simula.creative.puzzles import puzzles as make_puzzles
from simula.creative.schema import Character, Claim, Content, Copy, Hook, Proof, Puzzle

PROMPT = ROOT / "prompts" / "creative" / "host_lines.md"
ROLE = {"model": "claude-sonnet-5-5", "effort": "low", "max_tokens": 2000}
STAGE = "creative"
CTA = "Install Now"
LIMITS = {"intro": 90, "caption": 60, "right_line": 40, "wrong_hint": 40, "end_headline": 60}
BANNED = ("free", "$", "price", "premium", "unlimited", "discount")
HOOKS = {"challenge": "Dare the player to get all three right.",
         "help_host": "The host asks the player for help with three quick ones."}


class HostLines(Strict):
    """What the model writes, and nothing else. Lengths are checked by problems(), not by the schema, so an over-long
    line comes back as a failure the repair call can fix."""
    intro: str
    captions: list[str]
    right_line: str
    wrong_hint: str
    end_headline: str


def forbidden_terms(facts: Facts, host: Host) -> list[str]:
    """The app's feature terms, except the host's own name and the app's name."""
    own = {host.name.lower(), facts.app_name.lower()}
    return [t for t in facts.terms if t.lower() not in own]


def observed_lines(facts: Facts, host: Host) -> list[str]:
    """The app's own text for this host (its greetings, then its evidence element), as a voice reference."""
    text = {a.evidence_id: a.text for a in facts.allowed}
    return [text[eid] for eid in [*host.greetings, host.evidence_id] if eid in text]


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_NUMBER = re.compile(rf"(?<![A-Za-z0-9])(?:({'|'.join(_TENS)})(?: ({'|'.join(_ONES[1:10])}))?|({'|'.join(_ONES)}))"
                     r"(?![A-Za-z0-9])", re.IGNORECASE)
# A fixed cue list and at most two words after it, not a parser: widen it if a model gets an answer past it.
_CUE = re.compile(r"(?:\b(?:is|was|be|try|equals?|it[’']?s)\s+|[=:]\s*)(?:[A-Za-z]+\s+){0,2}$", re.IGNORECASE)


def _flat(text: str) -> str:
    return " ".join(text.replace("-", " ").split())


def _names(text: str, phrase: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(_flat(phrase))}(?![A-Za-z0-9])"
    return re.search(pattern, _flat(text), re.IGNORECASE) is not None


def _spells(text: str, value: int, cued: bool) -> bool:
    """Whether text spells out value as a whole number ("twenty-five" is 25, never 20 or 5), after a cue if cued."""
    flat = _flat(text)
    for m in _NUMBER.finditer(flat):
        tens, ones, alone = (w and w.lower() for w in m.groups())
        n = (_TENS.index(tens) + 2) * 10 + (_ONES.index(ones) if ones else 0) if tens else _ONES.index(alone)
        if n == value and (not cued or _CUE.search(flat[:m.start()])):
            return True
    return False


def problems(lines: HostLines, facts: Facts, host: Host, puzzles: list[Puzzle]) -> list[str]:
    """Every code check the lines fail, one plain sentence each: count and length per field, banned price and claim
    words, the app's feature terms (whitespace and hyphens normalized), and a line that gives away an answer. [] when
    they pass.

    A line is checked against the answers of the puzzles still open while it shows: the intro and the wrong hint
    against all three, caption i against puzzles i to 3, the right line against puzzles 2 and 3. The end card comes
    after the last puzzle (or replaces the rest), so nothing is left to give away. Captions and the hint may not
    name an open answer at all, as a word, digits or a number word. The intro and right line may not name a word
    answer or its digits, but a number word counts there only after a cue (is, try, equals, =) and at most two other
    words, so "Three quick ones" passes when an answer is 3 and "x is exactly three" fails. Number words are read as
    whole numbers: "twenty-five" never gives away 20, nor "twenty three" 3."""
    found = [] if len(lines.captions) == 3 else [f"captions has {len(lines.captions)} lines; it needs exactly 3"]
    fields = [("intro", lines.intro, LIMITS["intro"]),
              *[(f"caption {i + 1}", c, LIMITS["caption"]) for i, c in enumerate(lines.captions)],
              ("right_line", lines.right_line, LIMITS["right_line"]),
              ("wrong_hint", lines.wrong_hint, LIMITS["wrong_hint"]),
              ("end_headline", lines.end_headline, LIMITS["end_headline"])]
    for name, text, limit in fields:
        if not text.strip():
            found.append(f"{name} is empty")
        if len(text) > limit:
            found.append(f"{name} is {len(text)} characters; the limit is {limit}")
        found += [f'{name} uses the banned word "{word}"' for word in BANNED
                  if (word in text if word == "$" else _names(text, word))]
        found += [f'{name} names the app feature "{term}"' for term in forbidden_terms(facts, host)
                  if _names(text, term)]
    answers = [p.options[p.answer] for p in puzzles]
    shown = [("intro", lines.intro, 0, True),
             *[(f"caption {i + 1}", c, i, False) for i, c in enumerate(lines.captions)],
             ("right_line", lines.right_line, 1, True), ("wrong_hint", lines.wrong_hint, 0, False)]
    found += [f"{name} gives away the answer {answer}"
              for name, text, first, cued in shown for answer in answers[first:]
              if _names(text, answer) or (answer.isdigit() and _spells(text, int(answer), cued))]
    return found


def write_lines(*, facts: Facts, host: Host, hook: Hook, puzzles: list[Puzzle], budget: llm.Budget,
                trace_path: Path, cache_dir: Path = llm.CACHE, previous: HostLines | None = None,
                failures: list[str] | None = None) -> HostLines:
    """One host-line call through llm.call (stage creative, charged to budget). With failures it is the repair call:
    the previous answer and every concrete failure go back in one message. Raises llm.LLMFailure when the model fails
    twice and llm.CapReached when the budget can't hold the call."""
    task = {"app_name": facts.app_name, "host": {"name": host.name, "kind": host.kind,
                                                 "observed_lines": observed_lines(facts, host)},
            "hook": hook, "hook_means": HOOKS[hook],
            "puzzles": [{"family": p.family, "prompt": p.prompt} for p in puzzles],
            "forbidden_terms": forbidden_terms(facts, host), "limits": LIMITS}
    text = "Write the host lines for this creative.\n" + json.dumps(task, indent=1, ensure_ascii=False)
    if failures:
        text += ("\n\nYour previous answer:\n" + (previous.model_dump_json(indent=1) if previous else "(none)")
                 + "\n\nIt failed these checks. Fix every one and return the whole JSON again:\n"
                 + "\n".join(f"- {f}" for f in failures))
    step = f"host_lines {host.id}-{hook} round {1 if failures else 0}"
    lines, _ = llm.call(trace_path=trace_path, stage=STAGE, step=step, model=ROLE["model"], effort=ROLE["effort"],
                        system=PROMPT.read_text(), messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
                        max_tokens=config.max_tokens(ROLE), budget=budget, schema=HostLines, cache_dir=cache_dir)
    return lines


def build_content(facts: Facts, host: Host, hook: Hook, seed: int, lines: HostLines) -> Content:
    """The variant's content: the seed's puzzles, the host table's proof picks and the fixed CTA beside the lines.
    Call it only on lines problems() passed; over-long lines fail Copy's validation."""
    return Content(variant_id=f"{host.id}-{hook}", app_name=facts.app_name,
                   host=Character(kind=host.kind, name=host.name, art_ref=host.art, art_crop=host.art_crop,
                                  evidence_id=host.evidence_id),
                   hook=hook, seed=seed, puzzles=make_puzzles(seed), copy=Copy(**lines.model_dump()),
                   proof=Proof(screen_id=host.proof_screen,
                               claims=[Claim(text=p.text, evidence_id=p.evidence_id) for p in host.proof]),
                   cta=CTA)
