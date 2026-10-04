"""Three code-made puzzles per seed, one per family in a fixed order. Code computes every answer and wrong option, so
no model ever decides what is right, and no puzzle says anything about the advertiser's app."""

import random

from simula.creative.schema import Puzzle

WORDS = (
    "pencil", "eraser", "ruler", "notebook", "teacher", "lesson", "science", "history", "algebra", "grammar",
    "library", "chapter", "diagram", "fraction", "decimal", "equation", "planet", "volcano", "answer", "homework",
    "marker", "crayon", "globe", "story", "number", "triangle", "circle", "square", "angle", "graph",
    "laptop", "backpack", "chalk", "poetry", "spelling", "reading", "writing", "biology", "physics", "student",
)


def _shuffled(rng: random.Random, family: str, prompt: str, answer: str, wrong: list[str]) -> Puzzle:
    options = [answer, *wrong]
    rng.shuffle(options)
    return Puzzle(family=family, prompt=prompt, options=options, answer=options.index(answer))


def solve_for_x(rng: random.Random) -> Puzzle:
    """a·x + b = c with an integer answer from 1 to 12; the wrong options are its neighbours."""
    a, x, b = rng.randint(2, 9), rng.randint(1, 12), rng.randint(1, 20)
    wrong = [v for v in (x + 1, x - 1, x + 2) if v > 0][:2]
    return _shuffled(rng, "solve_for_x", f"{a}x + {b} = {a * x + b}", str(x), [str(v) for v in wrong])


def next_in_sequence(rng: random.Random) -> Puzzle:
    """Four terms of an arithmetic or a doubling sequence; one wrong option is the term after next."""
    if rng.random() < 0.5:
        start, step = rng.randint(1, 20), rng.randint(2, 9)
        terms = [start + step * i for i in range(6)]
        wrong = [terms[5], terms[4] + 1]
    else:
        start = rng.randint(1, 6)
        terms = [start * 2 ** i for i in range(6)]
        wrong = [terms[5], terms[3] + (terms[3] - terms[2])]
    prompt = ", ".join(str(t) for t in terms[:4]) + ", ?"
    return _shuffled(rng, "next_in_sequence", prompt, str(terms[4]), [str(v) for v in wrong])


def unscramble_word(rng: random.Random) -> Puzzle:
    """A word from WORDS with its letters shuffled; the scramble differs from the word and is not itself on the list."""
    word = rng.choice(WORDS)
    letters = list(word)
    while True:
        rng.shuffle(letters)
        scramble = "".join(letters)
        if scramble != word and scramble not in WORDS:
            break
    same_length = [w for w in WORDS if w != word and len(w) == len(word)]
    wrong = rng.sample(same_length if len(same_length) >= 2 else [w for w in WORDS if w != word], 2)
    return _shuffled(rng, "unscramble_word", scramble.upper(), word.upper(), [w.upper() for w in wrong])


def puzzles(seed: int) -> list[Puzzle]:
    """The run's three puzzles: solve_for_x, next_in_sequence, unscramble_word. The same seed gives the same puzzles,
    so every variant of a run shares them and differs only in host and hook."""
    rng = random.Random(seed)
    return [solve_for_x(rng), next_in_sequence(rng), unscramble_word(rng)]
