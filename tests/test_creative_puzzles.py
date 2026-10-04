import re

from simula.creative.puzzles import WORDS, puzzles

FAMILIES = ["solve_for_x", "next_in_sequence", "unscramble_word"]


def solve(p) -> str:
    """An independent re-solve from the prompt text alone."""
    if p.family == "solve_for_x":
        a, b, c = map(int, re.fullmatch(r"(\d+)x \+ (\d+) = (\d+)", p.prompt).groups())
        assert (c - b) % a == 0
        return str((c - b) // a)
    if p.family == "next_in_sequence":
        terms = [int(t) for t in p.prompt.removesuffix(", ?").split(", ")]
        steps = {b - a for a, b in zip(terms, terms[1:])}
        if len(steps) == 1:
            return str(terms[-1] + steps.pop())
        assert all(b == 2 * a for a, b in zip(terms, terms[1:]))
        return str(terms[-1] * 2)
    found = [w.upper() for w in WORDS if sorted(w.upper()) == sorted(p.prompt)]
    assert len(found) == 1
    return found[0]


def test_over_1000_seeds_a_re_solve_matches_every_answer_and_exactly_one_option_is_right():
    for seed in range(1000):
        ps = puzzles(seed)
        assert [p.family for p in ps] == FAMILIES
        for p in ps:
            want = solve(p)
            assert p.options[p.answer] == want, (seed, p)
            assert [o == want for o in p.options].count(True) == 1, (seed, p)
            assert len(set(p.options)) == 3, (seed, p)
        assert 1 <= int(ps[0].options[ps[0].answer]) <= 12
        scramble = ps[2].prompt.lower()
        assert scramble not in WORDS and scramble != ps[2].options[ps[2].answer].lower()


def test_the_same_seed_gives_the_same_puzzles():
    assert puzzles(42) == puzzles(42)
    assert puzzles(42) != puzzles(43)


def test_the_word_list_is_40_school_words_with_no_two_anagrams():
    assert len(WORDS) == 40 == len({"".join(sorted(w)) for w in WORDS})
    assert all(w.isalpha() and w.islower() and len(set(w)) >= 2 for w in WORDS)
