"""Text matching for every check of a model's words against app text. App trees carry non-breaking and
other odd spaces ('$\\xa01.99') that a model types back as plain ones, so whitespace never decides a match."""

import re


def find(quote: str, text: str, spaced: bool = True, ignore_case: bool = False) -> str | None:
    """The span of `text` that `quote` quotes, or None. Spaced (a quote): any whitespace run matches any
    other. Not spaced (a number like '$ 1.99'): whitespace is ignored, and a number matches only whole, so
    '99' is not in '$199', '1990', or '0.99'. Every other character must match."""
    parts = quote.split() if spaced else [c for c in quote if not c.isspace()]
    if not parts:
        return None
    pattern = (r"\s+" if spaced else r"\s*").join(re.escape(p) for p in parts)
    if not spaced and parts[0].isdigit():
        pattern = r"(?<!\d)(?<!\d[.,])" + pattern
    if not spaced and parts[-1].isdigit():
        pattern += r"(?!\d)(?![.,]\d)"
    match = re.search(pattern, text, re.IGNORECASE if ignore_case else 0)
    return match.group(0) if match else None


def phrase(words: str) -> re.Pattern[str]:
    """`words` as a whole word or phrase, ignoring case; any whitespace run matches any other. Boundaries are "no
    word character next to it", so a term ending in "+" still matches and "Pro" is not in "Protect"."""
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(p) for p in words.split()) + r"(?!\w)", re.IGNORECASE)


def same(a: str, b: str) -> bool:
    """Equal once whitespace runs are normalized."""
    return a.split() == b.split()
