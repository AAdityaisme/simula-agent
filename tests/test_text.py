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


def test_a_number_matches_only_whole():
    for screen in ("$199", "1990", "$0.99", "1,099", "99.5%"):
        assert text.find("99", screen, spaced=False) is None, screen
    assert text.find("99", "$99", spaced=False) == "99"
    assert text.find("99", "99 credits", spaced=False) == "99"
    assert text.find("99", "only 99.", spaced=False) == "99"
    assert text.find("$1.99", "US$1.99/week", spaced=False) == "$1.99"


def test_same_ignores_only_whitespace():
    assert text.same("Try\xa0Plus ", "Try Plus")
    assert not text.same("Try Plus", "TryPlus")


def test_a_phrase_is_a_whole_word_where_words_are_spaced_and_a_substring_where_they_are_not():
    assert text.phrase("Luzia+").search("Unlock Luzia+.") and text.phrase("zap credits").search("3 zap\xa0 Credits")
    assert not text.phrase("Pro").search("Protect") and not text.phrase("Pro").search("Go Pro2")
    assert text.phrase("Pro").search("Proを購入"), "a particle written onto a Latin word doesn't hide it"
    assert text.phrase("トークン").search("トークンを購入") and text.phrase("代币").search("购买代币")
    assert text.phrase("토큰").search("토큰을 구매")
    assert text.phrase(" ").search("anything") is None


def test_placeholder_characters_are_stripped_and_nothing_else():
    assert text.strip_placeholders("￼Hidden Gems show�") == "Hidden Gems show"
    assert text.strip_placeholders("🧑‍🎨 OC ‏$\xa01.99\n") == "🧑‍🎨 OC ‏$\xa01.99\n"
