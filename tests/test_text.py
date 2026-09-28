from simula import text


def test_a_quote_matches_across_any_whitespace_and_returns_the_app_text():
    assert text.find("$ 1.99 a week", "Only $\xa01.99  a week!") == "$\xa01.99  a week"
    assert text.find("$1.99", "Only $\xa01.99", spaced=False) == "$\xa01.99"
    assert text.find("$1.99", "Only $\xa01.99") is None


def test_every_other_character_must_match():
    assert text.find("$1.98", "$\xa01.99", spaced=False) is None
    assert text.find("Plus", "plus members") is None
    assert text.find("Plus", "plus members", ignore_case=True) == "plus"
    assert text.find("   ", "anything") is None


def test_same_ignores_only_whitespace():
    assert text.same("Try\xa0Plus ", "Try Plus")
    assert not text.same("Try Plus", "TryPlus")
