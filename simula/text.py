"""Text matching for every check of a model's words against app text. App trees carry non-breaking and
other odd spaces ('$\\xa01.99') that a model types back as plain ones, so whitespace never decides a match."""

import re


def find(quote: str, text: str, spaced: bool = True, ignore_case: bool = False) -> str | None:
    """The span of `text` that `quote` quotes, or None. Spaced (a quote): any whitespace run matches any
    other. Not spaced (a number like '$ 1.99'): whitespace is ignored. Every other character must match."""
    parts = quote.split() if spaced else [c for c in quote if not c.isspace()]
    if not parts:
        return None
    pattern = (r"\s+" if spaced else r"\s*").join(re.escape(p) for p in parts)
    match = re.search(pattern, text, re.IGNORECASE if ignore_case else 0)
    return match.group(0) if match else None


def same(a: str, b: str) -> bool:
    """Equal once whitespace runs are normalized."""
    return a.split() == b.split()
