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


# Scripts written without spaces between words (Thai, Lao, Tibetan, Myanmar, Khmer, Chinese, Japanese) or with
# particles written onto the word (Korean), where a word has no boundary to match.
UNSPACED = ("\u0e00-\u0fff\u1000-\u109f\u1100-\u11ff\u1780-\u17ff\u3000-\u318f\u31f0-\u31ff\u3400-\u4dbf"
            "\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff\uff66-\uffdc\U00020000-\U0003134f")
SPACED_LETTER = f"[^\\W{UNSPACED}]"


def phrase(words: str) -> re.Pattern[str]:
    """`words` as a whole word or phrase, ignoring case; any whitespace run matches any other. An edge in a script
    written with spaces must not touch another letter or digit of such a script, so a term ending in "+" still
    matches, "Pro" is not in "Protect", and "Pro" is in "Proを購入". An edge in an unspaced script matches as a
    substring, so "トークン" is in "トークンを購入". Empty `words` match nothing."""
    parts = words.split()
    if not parts:
        return re.compile(r"(?!)")
    head = "" if re.match(f"[{UNSPACED}]", parts[0][0]) else f"(?<!{SPACED_LETTER})"
    tail = "" if re.match(f"[{UNSPACED}]", parts[-1][-1]) else f"(?!{SPACED_LETTER})"
    return re.compile(head + r"\s+".join(re.escape(p) for p in parts) + tail, re.IGNORECASE)


def same(a: str, b: str) -> bool:
    """Equal once whitespace runs are normalized."""
    return a.split() == b.split()
