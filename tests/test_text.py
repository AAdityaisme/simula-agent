import unicodedata

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


def test_a_mark_written_on_a_letter_is_part_of_its_word():
    """A vowel sign (Devanagari, Bengali) or an accent typed as its own character is a combining mark, which regex \\w
    leaves out; the whole-word edge still counts it as part of the word."""
    assert text.phrase("टोकन").search("टोकन से मैसेज भेजें") and text.phrase("টোকেন").search("টোকেন দিয়ে মেসেজ পাঠান")
    assert not text.phrase("कन").search("टोकन खरीदें") and not text.phrase("मा").search("सीमा")
    assert not text.phrase("Poke").search(unicodedata.normalize("NFD", "Pokémon cards"))
    assert text.phrase("ได้").search("ก็ได้"), "a mark in a script written without spaces still matches as a substring"


def test_a_mark_on_an_emoji_does_not_join_it_to_the_next_word():
    """The presentation selector U+FE0F and the keycap U+20E3 only draw a symbol: "#️⃣" is "#", U+FE0F, U+20E3."""
    assert text.phrase("Energy").search("⚡️Energy refills every 4 hours") and text.phrase("Gems").search("💎️Gems: 120")
    assert text.phrase("Premium").search("⭐️Premium members skip the line")
    assert text.phrase("Tags").search("#️⃣Tags you follow") and text.phrase("Energy").search("1️⃣Energy refills")
    assert text.phrase("Energy").search("1\u20e3Energy refills"), "a keycap written without its selector, on a digit"
    assert text.phrase("Info").search("ℹ️Info: how credits work"), "U+2139 is a letter, but its selector only draws it"
    assert not text.phrase("nergy").search("⚡️Energy"), "the word itself still has its edges"


def test_a_letter_mark_used_as_a_decoration_does_not_join_the_next_word():
    """Character bios decorate with letter marks on spaces and symbols ("✴︎ ̊。⋆", " ׁ ׅ You"); one written on a space
    before a name belongs to no letter."""
    assert text.phrase("Yuki").search("⋆ ̊Yuki's garden") and text.phrase("MY").search("✴︎ ׁMY rules")
