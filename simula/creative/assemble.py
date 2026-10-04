"""One creative as one self-contained HTML file: the fixed, code-owned template (quick_puzzles.html beside this module)
with the content JSON, the host art, the proof screenshot cropped to app content and the app's font inlined as data
URIs. The page makes no request. Every string reaches it as JSON and is set with textContent, never parsed as HTML."""

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image

from simula.contracts import ProductModel
from simula.creative.schema import Content
from simula.render import content_dp

TEMPLATE = Path(__file__).with_name("quick_puzzles.html")
SPONSORED, SKIP, CLOSE, CONCEPT = "Sponsored", "Skip", "×", "Concept, not affiliated, not served"
SDK_EVENTS = {"DISPLAYED": "DISPLAYED", "CTA_CLICKED": "CLICKED", "CLOSED": "CLOSED"}  # CHALLENGE_*: needs_mapping


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def proof_png(run_dir: Path, screen_id: str) -> bytes:
    """The state's real screenshot, cropped to app content in content dp (render.content_dp), as PNG bytes."""
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    state = next(s for s in model.states if s.id == screen_id)
    with Image.open(run_dir / "model" / state.canonical_png) as image:
        crop = content_dp(image, model.device)
    out = io.BytesIO()
    crop.save(out, "PNG", optimize=True)
    return out.getvalue()


def font_css(run_dir: Path) -> str:
    """An @font-face for the approved mock's vendored font, inlined; "" when the mock vendored none."""
    fonts = run_dir / "qa" / "approved" / "assets" / "fonts"
    files = sorted(fonts.glob("*.woff2"))
    if not files or not (fonts / "fonts.css").exists():
        return ""
    family = re.search(r"font-family:\s*'([^']+)'", (fonts / "fonts.css").read_text()).group(1)
    src = data_uri(files[0].read_bytes(), "font/woff2")
    return (f"@font-face{{font-family:'{family}';font-weight:400 800;src:url({src}) format('woff2')}}\n"
            f":root{{--font:'{family}',system-ui,-apple-system,Roboto,sans-serif}}")


def assemble(content: Content, run_dir: Path) -> str:
    """The creative's HTML. "<" in the JSON is written as \\u003c, so no string can close the script tag."""
    art = (run_dir / "qa" / "approved" / "assets" / content.host.art_ref).read_bytes()
    data = {**content.model_dump(mode="json"), "art": data_uri(art, "image/png"),
            "shot": data_uri(proof_png(run_dir, content.proof.screen_id), "image/png")}
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    template = TEMPLATE.read_text()
    if template.count("/*FONT*/") != 1 or template.count("__DATA__") != 1:
        raise ValueError(f"{TEMPLATE.name} must hold /*FONT*/ and __DATA__ once each")
    return template.replace("/*FONT*/", font_css(run_dir)).replace("__DATA__", blob)


def write_variant(content: Content, run_dir: Path, out_dir: Path) -> Path:
    """Writes out_dir/content.json and out_dir/creative.html; returns the HTML's path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "content.json").write_text(content.model_dump_json(indent=1))
    path = out_dir / "creative.html"
    path.write_text(assemble(content, run_dir))
    return path


def visible_strings(content: Content) -> list[tuple[str, str, str]]:
    """Every string the creative can show, as (screen, string, source), once each, in screen order. source is
    observed:<element id>, generated (the model's lines), template (fixed in quick_puzzles.html) or code."""
    copy = content.copy_
    host_source = f"observed:{content.host.evidence_id}" if content.host.evidence_id else "code"
    rows = [("all", SPONSORED, "template"), ("all", CONCEPT, "template"), ("all", SKIP, "template"),
            ("intro", content.host.name, host_source), ("intro", copy.intro, "generated")]
    for caption, puzzle in zip(copy.captions, content.puzzles):
        rows += [("puzzle", caption, "generated"), ("puzzle", puzzle.prompt, "code"),
                 *[("puzzle", option, "code") for option in puzzle.options]]
    rows += [("puzzle", copy.right_line, "generated"), ("puzzle", copy.wrong_hint, "generated")]
    rows += [("proof", claim.text, f"observed:{claim.evidence_id}") for claim in content.proof.claims]
    rows += [("end", copy.end_headline, "generated"), ("end", content.cta, "code"), ("end", CLOSE, "template")]
    return list(dict.fromkeys(rows))
