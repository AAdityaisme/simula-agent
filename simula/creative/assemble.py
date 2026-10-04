"""One creative as one self-contained HTML file: the fixed, code-owned template (quick_puzzles.html beside this module)
with the content JSON, the host art, the proof screenshot cropped to its cited element and the app's font inlined as
data URIs. The page makes no request. Every string reaches it as JSON and is set with textContent, never parsed as HTML."""

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image

from simula.contracts import ProductModel, Rect, State
from simula.creative.schema import Character, Content
from simula.render import content_dp

TEMPLATE = Path(__file__).with_name("quick_puzzles.html")
SPONSORED, SKIP, CLOSE = "Sponsored", "Skip puzzles", "×"
MARGIN_DP, HEADER_FITS_DP = 8, 280
SDK_EVENTS = {"DISPLAYED": "DISPLAYED", "CTA_CLICKED": "CLICKED", "CLOSED": "CLOSED"}  # CHALLENGE_*: needs_mapping


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def concept(app_name: str) -> str:
    return f"Concept ad, not affiliated with {app_name}, not served"


def proof_label(content: Content) -> str:
    return f"From {content.host.name}'s screen in {content.app_name}"


def _meets(rect: Rect, box: tuple[float, float, float, float]) -> bool:
    return rect.x < box[2] and box[0] < rect.x + rect.w and rect.y < box[3] and box[1] < rect.y + rect.h


def _holds(box: tuple[float, float, float, float], rect: Rect) -> bool:
    return box[0] <= rect.x and box[1] <= rect.y and rect.x + rect.w <= box[2] and rect.y + rect.h <= box[3]


def proof_box(state: State, cited: set[str], size: tuple[int, int]) -> tuple[float, float, float, float]:
    """The proof crop, (x0, y0, x1, y1) in content dp on a screen of size: the cited elements plus MARGIN_DP, taken
    from the top of the screen at full width when that stays within HEADER_FITS_DP tall, so the screen's header shows.
    It then stops short of any other text element it would cut into, pass after pass until nothing changes, so no
    uncited line is half shown."""
    rects = [e.rect_dp for e in state.elements if e.id in cited]
    held = (min(r.x for r in rects), min(r.y for r in rects),
            max(r.x + r.w for r in rects), max(r.y + r.h for r in rects))
    x0, y0 = max(0, held[0] - MARGIN_DP), max(0, held[1] - MARGIN_DP)
    x1, y1 = min(size[0], held[2] + MARGIN_DP), min(size[1], held[3] + MARGIN_DP)
    if y1 <= HEADER_FITS_DP:
        x0, y0, x1 = 0, 0, size[0]
    box = None
    while box != (x0, y0, x1, y1):  # each pass only shrinks the box, so this ends
        box = (x0, y0, x1, y1)
        for e in state.elements:
            r = e.rect_dp
            if e.id in cited or not e.text.strip() or _meets(r, held) or not _meets(r, (x0, y0, x1, y1)) \
                    or _holds((x0, y0, x1, y1), r):
                continue
            if r.y >= held[3]:
                y1 = min(y1, r.y)
            elif r.y + r.h <= held[1]:
                y0 = max(y0, r.y + r.h)
            elif r.x >= held[2]:
                x1 = min(x1, r.x)
            else:
                x0 = max(x0, r.x + r.w)
    return box


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def proof_png(run_dir: Path, screen_id: str, cited: set[str], crop: list[float] | None = None) -> bytes:
    """The state's real screenshot in content dp (render.content_dp), cropped to crop (the host table's proof_crop)
    or else to proof_box around the cited elements, as PNG bytes. Crop only: no pixel is drawn."""
    model = ProductModel.model_validate_json((run_dir / "model" / "product_model.json").read_text())
    state = next(s for s in model.states if s.id == screen_id)
    with Image.open(run_dir / "model" / state.canonical_png) as image:
        frame = content_dp(image, model.device)
    return png(frame.crop(tuple(round(v) for v in crop or proof_box(state, cited, frame.size))))


def art_png(run_dir: Path, host: Character) -> bytes:
    """The host's art file, cut to its art_crop box when it has one."""
    path = run_dir / "qa" / "approved" / "assets" / host.art_ref
    if not host.art_crop:
        return path.read_bytes()
    with Image.open(path) as image:
        return png(image.crop(tuple(host.art_crop)))


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
    cited = {claim.evidence_id for claim in content.proof.claims}
    data = {**content.model_dump(mode="json"), "art": data_uri(art_png(run_dir, content.host), "image/png"),
            "shot": data_uri(proof_png(run_dir, content.proof.screen_id, cited, content.proof.crop), "image/png"),
            "concept": concept(content.app_name), "proof_label": proof_label(content)}
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
    observed:<element id>, generated (the model's lines), template (fixed wording, at most filled in with the host's
    or app's name) or code."""
    copy = content.copy_
    host_source = f"observed:{content.host.evidence_id}" if content.host.evidence_id else "code"
    rows = [("all", SPONSORED, "template"), ("all", concept(content.app_name), "template"), ("all", SKIP, "template"),
            ("intro", content.host.name, host_source), ("intro", copy.intro, "generated")]
    for caption, puzzle in zip(copy.captions, content.puzzles):
        rows += [("puzzle", caption, "generated"), ("puzzle", puzzle.prompt, "code"),
                 *[("puzzle", option, "code") for option in puzzle.options]]
    rows += [("puzzle", copy.right_line, "generated"), ("puzzle", copy.wrong_hint, "generated")]
    rows += [("proof", proof_label(content), "template")]
    rows += [("proof", claim.text, f"observed:{claim.evidence_id}") for claim in content.proof.claims]
    rows += [("end", copy.end_headline, "generated"), ("end", content.app_name, "code"), ("end", content.cta, "code"),
             ("end", CLOSE, "template")]
    return list(dict.fromkeys(rows))
