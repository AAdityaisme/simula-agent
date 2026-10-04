"""What a creative may use from one explored app, by code ($0): the safe scope, whether the app has unsafe screens, the
hosts (a hand-written table per app in hosts/, checked against the run), the allowed proof strings, and the feature
terms host lines may not use."""

import json
import re
from pathlib import Path
from typing import Literal

from simula.contracts import ContentRating, ProductModel, Strict

HOSTS = Path(__file__).with_name("hosts")


class ProofPick(Strict):
    evidence_id: str
    text: str


class Host(Strict):
    id: str
    name: str
    kind: Literal["app_mascot", "app_persona"]
    art: str
    evidence_id: str
    greetings: list[str]
    proof_screen: str
    proof: list[ProofPick]


class HostTable(Strict):
    app: str
    hosts: list[Host]


class Allowed(Strict):
    evidence_id: str
    screen: str
    text: str
    cited_by: list[str]


class Facts(Strict):
    run_dir: str
    app: str
    app_name: str
    app_package: str
    app_version: str
    run_id: str
    safe_scope: list[str]
    excluded: dict[str, str]
    ratings: dict[str, ContentRating]
    has_unsafe_screens: bool
    hosts: list[Host]
    allowed: list[Allowed]
    terms: list[str]


def is_excerpt(text: str, whole: str) -> bool:
    """True when text is whole or an exact contiguous piece of it that cuts no word in two."""
    start = r"\b" if re.match(r"\w", text) else ""
    end = r"\b" if re.search(r"\w$", text) else ""
    return bool(text.strip()) and re.search(start + re.escape(text) + end, whole) is not None


def build_facts(run_dir: Path, hosts_path: Path | None = None) -> Facts:
    """Facts for one run. A state is in the safe scope when it is rated safe, sits in mock scope and the approved mock
    draws it: a placeholder section QA lists as undrawn does not count. An allowed proof string is the text of an
    element on a safe-scope screen that an observed or inferred mechanic or a value-ledger item cites, or that the host
    table lists; a mechanic of unknown status counts for nothing. Raises SystemExit when the run has no approved mock or
    QA report, a host-table id is not on a safe-scope screen, or a host proof pick is not a word-boundary excerpt of its
    element's text."""
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    mock = run_dir / "qa" / "approved" / "index.html"
    qa_report = run_dir / "qa" / "qa_report.json"
    if not mock.exists():
        raise SystemExit(f"{run_dir}: no approved mock at qa/approved/index.html")
    if not qa_report.exists():
        raise SystemExit(f"{run_dir}: no QA report at qa/qa_report.json")
    drawn = set(re.findall(r"""<section\b[^>]*\bdata-screen=["']?([^"'\s>]+)""", mock.read_text()))
    drawn -= {u["screen"] for u in json.loads(qa_report.read_text()).get("undrawn_screens", [])}
    excluded = {}
    for state in model.states:
        if state.content_rating != "safe":
            excluded[state.id] = state.content_rating
        elif not state.in_mock_scope:
            excluded[state.id] = "out_of_mock_scope"
        elif state.id not in drawn:
            excluded[state.id] = "not_drawn"
    safe = [s.id for s in model.states if s.id not in excluded]
    elements = {e.id: (s.id, e) for s in model.states for e in s.elements}
    table = HostTable.model_validate_json((hosts_path or HOSTS / f"{model.app}.json").read_text())
    for host in table.hosts:
        art_element = host.art.removesuffix(".art.png")
        ids = [host.evidence_id, *host.greetings, *(p.evidence_id for p in host.proof), art_element]
        for eid in ids:
            if eid not in elements or elements[eid][0] not in safe:
                raise SystemExit(f"hosts table, {host.id}: {eid} is not an element on a safe-scope screen")
        for pick in host.proof:
            if not is_excerpt(pick.text, elements[pick.evidence_id][1].text):
                raise SystemExit(f"hosts table, {host.id}: proof {pick.text!r} is not an excerpt of {pick.evidence_id}")
        if host.proof_screen not in safe:
            raise SystemExit(f"hosts table, {host.id}: proof screen {host.proof_screen} is not in the safe scope")
        if not (run_dir / "qa" / "approved" / "assets" / host.art).exists():
            raise SystemExit(f"hosts table, {host.id}: no art file qa/approved/assets/{host.art}")
    cited: dict[str, list[str]] = {}
    for mechanic in model.mechanics:
        if mechanic.status in ("observed", "inferred"):
            for eid in mechanic.evidence_ids:
                cited.setdefault(eid, []).append(mechanic.id)
    for item in model.value_ledger:
        for eid in item.evidence_ids:
            cited.setdefault(eid, []).append(item.id)
    for host in table.hosts:
        for eid in dict.fromkeys([host.evidence_id, *host.greetings, *(p.evidence_id for p in host.proof)]):
            cited.setdefault(eid, []).append(f"host:{host.id}")
    allowed = [Allowed(evidence_id=eid, screen=elements[eid][0], text=elements[eid][1].text, cited_by=sorted(set(by)))
               for eid, by in sorted(cited.items())
               if eid in elements and elements[eid][0] in safe and elements[eid][1].text.strip()]
    return Facts(run_dir=str(run_dir), app=model.app, app_name=model.app_name, app_package=manifest["app_package"],
                 app_version=model.app_version, run_id=model.run_id, safe_scope=safe, excluded=excluded,
                 ratings={s.id: s.content_rating for s in model.states},
                 has_unsafe_screens=any(s.content_rating == "unsafe" for s in model.states), hosts=table.hosts,
                 allowed=allowed, terms=[t.term for t in model.terms])
