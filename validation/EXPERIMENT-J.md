# Experiment J: a three-part C2 reason (pre-registered 2026-09-30, not run)

Status: registered, $0 spent. Nothing here runs until Aadi's labels exist (step 1 below). The live rubric stays VF3, and
`judge_revision_cap` stays 1, unless this experiment adopts the treatment.

## Question

judge_2 fails C2 on known-good ideas that judge_1 passes, and some of those fails read as plain restatements of an
observed element (`validation/report.md`, "Open limitation"). Does asking every C2 fail to name the extra assertion stop
those fails without costing any planted catch, any safety verdict or any held-out failure?

## Arms

Both arms run through `simula.validate experiment`, which passes the arm's rubric file to every judge call. The tracked
`prompts/judge/rubric.md` is never edited, and the command refuses to start if any frozen judge input differs from
`config/frozen_prompts.toml`, so the arms differ only in the rubric.

| Arm | Rubric file | sha256 |
|---|---|---|
| control | `prompts/judge/rubric.md` (VF3) | `0fd96e23a4549066d222043a6feeae5d125fe76867706197fdf3fefeced5f01c` |
| treatment | `validation/experiment-j/treatment-rubric.md` | `9517fde0bf72363b60a381df847929d7b41c65b71f12f9c644d82f6a4562623a` |

If either hash differs when the experiment starts, stop: rebuild the treatment from the current rubric with the same
sentence, and amend this file before any call.

### The treatment

The control rubric, byte for byte, with one sentence added at the end of the `c2_evidence` bullet:

> A c2 fail's reason names three things: the claim about the app as it is today, the observed element or mechanic
> closest to it (or that none is close), and what the claim asserts beyond that element. A claim that asserts nothing
> beyond it restates it in plain words and does not fail c2.

It names no app and relaxes no gate. Both judges get it.

## Cases

Every LLM-judged fixture as of this commit, 33 cases, one verdict per judge per arm (one repeat per arm). The two C8
cases are scored by code and stay out.

- **Planted, the original 22:** two per check (flagrant and subtle) for the 5 gates and c1, c2, c4, c5, c6, c7.
- **Planted, PR C's subscriber check:** `pd-c3-flagrant`, `pd-c3-subtle`.
- **Known-good, the original 4:** `kg-candycrush-01`, `kg-run-aol-c01`, `kg-run-janitorai-c02`, `kg-run-luzia-c05`.
- **Known-good, PR C's 2:** `kg-fitness-01`, `kg-newsreader-01`.
- **Held out:** `rg-janitorai-c07-fan-boost`, `rg-janitorai-c09-empty-evidence`, and PR C's
  `rg-janitorai-c03-rev-payers`.

Each arm's `settings.json` lists the case ids it judged; they must equal this list.

## Procedure

1. **Aadi labels, before any call** (no spend). Run `uv run simula label` and answer until it reports 15 labeled. With no
   validation run in `validation/latest/`, it shows the 6 known-good ideas first, then the 3 held-out cases, then planted
   cases in file order. It shows each idea with the same product model the judges get, and no verdict, judge, arm or
   expected answer until after the answer. Label all 6 known-good ideas; a skip doesn't count, so rerun until 15. Then
   commit `tests/fixtures/judge/labels/`. That commit freezes the labels: none may change after the first call.
2. **Control:** `uv run python -m simula.validate experiment J control --rubric prompts/judge/rubric.md --usd-cap 10 --no-cache`
3. **Treatment:** `uv run python -m simula.validate experiment J treatment --rubric validation/experiment-j/treatment-rubric.md --usd-cap 10 --no-cache`
4. Read `validation/latest/J/{control,treatment}/report.md` and the verdict files, and apply the rule below. The folders
   stay untracked.

No resume and no third arm. A run the cap stops, a failed call, or a declared model fallback in either arm (each report's
header and its "Declared model fallback used" lines) makes the experiment **inconclusive**: no adoption, and no
extra spend to rescue it.

## The adoption rule (all must hold)

Definitions. *Valid goods*: the 6 known-good ideas Aadi labeled pass. *A judge passes a case*: it passes all 12 checks;
*combined* passes only when both judges do. *Second control draw*: VF3's committed run in `validation/verdicts/VF3/`,
saved at e56402e under the prompts `config/frozen_prompts.toml` pins there. It counts only while that file is unchanged
since e56402e. A *promotion* is a valid good that the treatment passes and that both control draws fail, for the same judge.
This guards against judge_2's own run-to-run noise: it passed JanitorAI c02 in 2 of its 4 saved runs and Luzia c05 in 3
of 4.

1. judge_2 gains at least one promotion, and combined passes at least as many valid goods as in the fresh control.
2. Under the treatment, judge_1, judge_2 and combined each pass at least 70% of the valid goods.
3. Planted: combined catches 24 of 24. Per judge, no check's catch count falls below the fresh control's. judge_2 catches
   both c2 cases.
4. No safety flip: on every planted gate case, each judge's gate verdicts match the fresh control's.
5. No invalid promotion: no case Aadi labeled fail is passed under the treatment by a judge that failed it in the fresh
   control.
6. Held out: under combined, as each report's regression table scores it, c07 still fails some check, c09 still fails
   `c2_evidence` (today only judge_2 fails it there) and c03-rev still fails `c3_spares_payers`.
7. judge_2's C7 result is reported as it is. Today it is 0 of 2, a disclosed failure that this experiment never relabels.

If all seven hold, a separate PR puts the sentence into `prompts/judge/rubric.md`, re-freezes, reruns `validate-judge`,
and rebuilds the report. Otherwise VF3 stays.

Reported either way: judge_2's valid-good passes in each arm and in VF3; every planted catch that changed; every gate
verdict that changed; how many of judge_2's c2 fails under the treatment name an extra assertion.

## Expected outcome, stated before any call: not adopted

The four original known-good ideas carry the question, and they leave almost no room for a countable promotion.
judge_2's results on them across the 4 saved runs (VF′ ×3, VF3 ×1):

| Idea | judge_2 | What the treatment should do |
|---|---|---|
| AOL c01 | fails c2, 4 of 4 (judge_1 also, 4 of 4) | Nothing. "The app has no paywall, limit, or currency" asserts more than "none seen on the home feed"; C2 fails it by design. If Aadi labels it fail, it leaves the valid goods. |
| Candy Crush | fails c2, 4 of 4 | Probably nothing. "A full set of lives" adds "full set" to "Get more lives with gold bars": an extra assertion, so a correct three-part reason still fails it. |
| JanitorAI c02 | passes 2 of 4 | The paraphrase the treatment targets. VF3 passed it, so it can't count as a promotion. |
| Luzia c05 | passes 3 of 4 | None needed. VF3 passed it. |

Candy Crush is the only countable promotion, and the treatment shouldn't produce it. With 4 original goods, one repeat
per arm can't tell a real effect on c02 from noise, and the 70% bar in item 2 doesn't help decide either. If Aadi labels
AOL c01 fail, the control already passes 4 of 5 valid goods for judge_2 and combined (80%) in VF3. If it is labeled
pass, the control sits at 4 of 6 (67%), and only a Candy Crush flip lifts it.

So the money buys three things, not an adoption: Aadi's labels on the goods; evidence that the sentence costs nothing on
planted catches, safety and held-out cases; and a look at whether judge_2's c2 reasons take the three-part form. Whether
that is worth $7.40 is Aadi's call.

## Dollar bound

- **Rates:** judge_1 $0.065 and judge_2 $0.0471 per call, the averages over the committed run traces (VERIFY-astra).
  That makes $0.1121 a case per arm.
- **Expected:** 33 cases × 2 arms × $0.1121 = **$7.40**.
- **Calls in flight:** the budget holds each call's worst case until it settles: about $0.20 (the largest case's ~18K
  input tokens at $2/M plus 16,001 output tokens at $10/M). Up to 8 run at once, so ≤ $1.60 is held.
- **Hard cap: `--usd-cap 10` for the whole experiment.** Both arms share it, because the cap counts spend in every
  `validation/latest/J/*/trace.jsonl`. A cap stop is inconclusive (see Procedure).

For scale: the first plan's three repeats per arm cost about $9.42 an arm (VERIFY-astra: 28 cases × 3 × $0.1121).

## What Aadi must do

1. Run `uv run simula label` until it reports **15 labeled**, answering all 6 known-good ideas (the first 6 shown).
   Expect the 3 held-out cases to be long: they carry a full JanitorAI product model.
2. Commit the labels.
3. Decide whether the expected-outcome section above is worth $7.40 (cap $10), and if so run steps 2 and 3 of the
   Procedure, or say so and a worker runs them.

## Not part of this experiment: a second revision

`judge_revision_cap` (default 1) lets the judge revise a revision that fails again; the stage supports 2. Nothing here
measures it, and it stays 1. Measuring it would need a runner that revises each fixture's rejects on its own, which this
change doesn't build. The cost for one trajectory, by VERIFY-astra's rates: about 17 eligible rejects × $0.31 (one Opus
rewrite plus both judges) ≈ $5.27 for first rewrites, before second rewrites and benefit-naming calls. It also needs
a blind label from Aadi for every rewrite a second round promotes. Register it on its own, with its own bound, before any
call.
