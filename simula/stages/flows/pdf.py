"""Printing the deck to PDF and checking its layout for overflow."""

from pathlib import Path

from playwright.sync_api import sync_playwright

from simula.stages.flows.deck import SLIDE_H, SLIDE_W

OVERFLOW_JS = """() => [...document.querySelectorAll('.slide')].flatMap((slide, i) => {
  const box = slide.getBoundingClientRect(), footer = slide.querySelector('footer');
  const floor = footer ? footer.getBoundingClientRect().top : box.bottom;
  const name = `slide ${i + 1}` + (slide.dataset.idea ? ` (${slide.dataset.idea} ${slide.dataset.part})` : '');
  const bad = [], lines = [];
  for (const el of slide.querySelectorAll('*')) {
    if (el === footer || footer?.contains(el) || el.contains(footer) || el.closest('.phone, svg') ||
        bad.some(b => b.contains(el)) || !el.checkVisibility()) continue;
    const b = el.getBoundingClientRect(), style = getComputedStyle(el);
    if (!b.width || !b.height) continue;
    const past = Math.max(b.right - box.right, b.bottom - box.bottom, box.left - b.left, box.top - b.top);
    const into = b.bottom - floor;
    const cut = style.overflow !== 'visible' && style.textOverflow !== 'ellipsis' &&
                (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
    if (past <= 1 && into <= 1 && !cut) continue;
    bad.push(el);
    const what = (el.className ? '.' + String(el.className).split(' ')[0] : el.tagName.toLowerCase()) +
                 ` "${el.textContent.trim().slice(0, 40)}"`;
    lines.push(`${name}: ${what} ` + (past > 1 ? `runs ${Math.round(past)}px past the slide's edge`
               : into > 1 ? `runs ${Math.round(into)}px into the footer` : 'is cut off'));
  }
  return lines;
})"""


def overflows(page) -> list[str]:
    """Where the rendered deck's text or boxes run past a slide's edge, into its footer, or get cut off: one line
    per outermost element, naming its slide, measured once the deck's font has loaded and its why slides have fitted
    their text. Screenshots and deliberate ellipses don't count."""
    page.wait_for_function("'fitted' in document.documentElement.dataset")
    return page.evaluate(OVERFLOW_JS)


def write_pdf(slides: Path) -> list[str]:
    """Prints the deck to PDF beside it and returns where its layout overflows."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})
        page.goto(slides.as_uri())
        page.wait_for_load_state("networkidle")
        problems = overflows(page)
        page.pdf(path=slides.with_suffix(".pdf"), width=f"{SLIDE_W}px", height=f"{SLIDE_H}px", print_background=True)
        browser.close()
    return problems
