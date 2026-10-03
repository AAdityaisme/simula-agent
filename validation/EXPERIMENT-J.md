# Experiment j: a three-part C2 reason (pre-registered 2026-09-30, not run)

Status: registered, $0 spent. The live rubric stays VF3, and `judge_revision_cap` stays 1, unless this experiment adopts
the treatment.

## Label first, before reading on

**Aadi:** run `uv run simula label` and label every case it shows until it reports 15 labeled, then commit
`tests/fixtures/judge/labels/`. Do this before reading the rest of this file, which names the cases and what the judges
are expected to do with them.

- Each case appears as the judges see it, product model included. No verdict, judge, arm or expected answer appears
  until you have answered that case.
- The order is a fixed shuffle, so it says nothing about any case. A skipped case comes back in the same place on the
  next run.
- The commit freezes the labels: none may change after the experiment's first call. The experiment refuses to start
  until 15 committed labels exist and include the ones it needs.

What can't be blind: these fixtures include ideas Aadi chose, and `validation/report.md` already discusses three of them
by name. The labels are blind to the judges' verdicts and to each case's expected answer, not to memory.

## Question

judge_2 fails C2 on known-good ideas that judge_1 passes, and some of those fails read as plain restatements of an
observed element (`validation/report.md`, "Open limitation"). Does asking every C2 fail to name the extra assertion stop
those fails without costing any planted catch, any safety verdict or any held-out failure?

## Arms

Both arms run through `python -m simula.validate experiment j <arm>`, which takes everything else from the committed
`validation/experiment-j/registration.toml`: each arm's rubric file and its sha256, the judges, the profile, `--no-cache`,
and the cap both arms share. It passes the arm's rubric file to every judge call; the tracked `prompts/judge/rubric.md`
is never edited.

Each arm takes two runs into the tracked `validation/experiment-j/<arm>/`:

1. **The start.** The first run writes the arm's start, `settings.json` and `rubric.md`, and stops. Commit and push it.
2. **The draw.** The second run makes the calls only when all of these hold:
   - exactly that start is committed and on a remote branch, since the push is checked, not just asked for;
   - no commit on any ref or in the reflog ever held more under the folder;
   - no draw of the arm began before.

   Before its first call, it creates the ref `refs/experiments/j/<arm>`, with a reflog, to mark the draw. The ref is
   created only if it doesn't exist, so of two draws started at once, only one makes calls. It writes
   the verdicts, trace and `report.md`. Commit and push them.

Any other state is refused: results on disk or in history, a start that is gone, or a draw ref that exists. So a
draw that was deleted, cleaned away (`git clean`), crashed or reset away is never drawn again. The refusal names the
two visible ways out: commit the folder as it stands, which leaves the experiment inconclusive, or register a new
experiment folder in a commit.

Both runs also refuse, before any call or write, when any of these holds:

- another arm git knows of (in its history or by its draw ref) isn't in this checkout, or git holds more of it than
  the checkout does (its draw): merge it first, so the inputs check and the shared cap see it;
- `validation/experiment-j/` (the registration and every arm) or the labels aren't committed;
- there are fewer than 15 labels, or one of the known-good ideas has none;
- the arm's rubric doesn't hash to its registered sha256;
- an arm started on other inputs. Each start's `settings.json` records them by sha256:
  - the registration, both rubrics, the frozen judge prompts, the judge models' `config/models.toml` entries and the
    labels;
  - every case, both as the judges see it and as the reports score it (id, source, target, tier, known-good, app,
    app type, in the test set).

  A commit that touches none of these, such as a `next` merge, may land between the runs;
- a frozen judge input differs from `config/frozen_prompts.toml`.

So the arms differ only in the rubric.

| Arm | Rubric file | sha256 |
|---|---|---|
| control | `prompts/judge/rubric.md` (VF3) | `0fd96e23a4549066d222043a6feeae5d125fe76867706197fdf3fefeced5f01c` |
| treatment | `validation/experiment-j/treatment-rubric.md` | `9517fde0bf72363b60a381df847929d7b41c65b71f12f9c644d82f6a4562623a` |

If the live rubric changes before the experiment runs, the control's hash no longer matches and the command refuses.
Rebuild the treatment from the new rubric with the same sentence, and amend this file and the registration before any
call.

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

1. **Labels**, as the first section says: no spend.
2. **Control:** run `uv run python -m simula.validate experiment j control`, then commit and push the start. Run it
   again to draw, then commit and push `validation/experiment-j/control/` at once. Nothing else runs until it is
   committed.
3. **Treatment:** the same two runs, each followed by a commit and push, with `experiment j treatment`.
4. Read both arms' `report.md` and verdict files, and apply the rule below.

No resume and no third arm: each arm draws once.

- Until a draw is committed, it shows in `git status`.
- Once it has begun, its ref refuses any other draw of the arm, whatever happens to the folder.
- Redrawing would take rewriting git's own records on purpose. One way is deleting the draw ref
  (`git update-ref -d`, which drops its reflog) and cleaning the folder. The other is a fresh clone of a start whose
  draw was never pushed, since the ref stays local. So commit and push each draw before reading its report.

The experiment is **inconclusive** if either arm hits any of these:

- a cap stop;
- a failed call;
- a declared model fallback (each report's header and its "Declared model fallback used" lines).

Inconclusive means no adoption and no extra spend to rescue it.

## The adoption rule (all must hold)

Definitions. *Valid goods*: the known-good ideas that each arm's `report.md` counts in its known-good rate and that
Aadi labeled pass. Every known-good idea needs a label, but the denominator is whatever `report.md` counts: all 6 today,
or only the human-written ones if a later change narrows the rate to those. *A judge passes a case*: it passes all 12 checks;
*combined* passes only when both judges do. *Second control draw*: VF3's committed run in `validation/verdicts/VF3/`,
saved at e56402e under the prompts `config/frozen_prompts.toml` pins there. It counts only while that file is unchanged
since e56402e. A *promotion* is a valid good that the treatment passes and that both control draws fail, for the same judge.
This guards against judge_2's own run-to-run noise: it passed JanitorAI c02 in 3 of its 6 saved runs and Luzia c05 in 5
of 6.

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
judge_2's results on them across the 6 saved runs (VF′ ×3, VF3 ×3):

| Idea | judge_2 | What the treatment should do |
|---|---|---|
| AOL c01 | fails c2, 6 of 6 (judge_1 also, 6 of 6) | Nothing. "The app has no paywall, limit, or currency" asserts more than "none seen on the home feed"; C2 fails it by design. If Aadi labels it fail, it leaves the valid goods. |
| Candy Crush | fails c2, 6 of 6 | Probably nothing. "A full set of lives" adds "full set" to "Get more lives with gold bars": an extra assertion, so a correct three-part reason still fails it. |
| JanitorAI c02 | passes 3 of 6 | The paraphrase the treatment targets. VF3 passed it, so it can't count as a promotion. |
| Luzia c05 | passes 5 of 6 | None needed. VF3 passed it. |

*(Updated 2026-10-03: this was registered over 4 saved runs, VF′ ×3 and VF3's kept run, and said 4 of 4, 4 of 4, 2 of 4
and 3 of 4. #42 then saved VF3's two earlier paid runs, so the counts above are over all 6: the run at 42f9428 failed
c02 on c2 and passed c05, and the run at 9092006 passed both. "VF3 passed it" means VF3's kept run, the second control
draw.)*

Candy Crush is the only countable promotion, and the treatment shouldn't produce it. With 4 original goods, one repeat
per arm can't tell a real effect on c02 from noise. The 70% bar in item 2 doesn't decide it either; where VF3 sits on
it depends on how AOL c01 is labeled and which goods `report.md` counts:

| AOL c01 labeled | All 6 counted (today) | Only the 4 human-written counted |
|---|---|---|
| fail | 4 of 5 (80%), bar met | 2 of 3 (67%); only a Candy Crush flip lifts it, to 3 of 3 |
| pass | 4 of 6 (67%); only a Candy Crush flip lifts it, to 5 of 6 | 2 of 4 (50%); a Candy Crush flip lifts it to 3 of 4 |

Each cell is VF3's control for judge_2 and for combined.

So the money buys three things, not an adoption: Aadi's labels on the goods; evidence that the sentence costs nothing on
planted catches, safety and held-out cases; and a look at whether judge_2's c2 reasons take the three-part form. Whether
that is worth $7.40 is Aadi's call.

## Dollar bound

- **Rates:** judge_1 $0.065 and judge_2 $0.0471 per call, the averages over the committed run traces (VERIFY-astra).
  That makes $0.1121 a case per arm.
- **Expected:** 33 cases × 2 arms × $0.1121 = **$7.40**.
- **Calls in flight:** the budget holds each call's worst case until it settles: about $0.20 (the largest case's ~18K
  input tokens at $2/M plus 16,001 output tokens at $10/M). Up to 8 run at once, so ≤ $1.60 is held.
- **Hard cap: `usd_cap = 10` in the registration, for the whole experiment.** Both arms share it: the cap counts spend
  in every committed `validation/experiment-j/*/trace.jsonl`. A cap stop is inconclusive (see Procedure).

For scale: the first plan's three repeats per arm cost about $9.42 an arm (VERIFY-astra: 28 cases × 3 × $0.1121).

## What Aadi must do

1. Label and commit, as the first section says (15 labels). Some cases are long: they carry a full JanitorAI product
   model.
2. Decide whether the expected-outcome section above is worth $7.40 (cap $10). If so, run steps 2 and 3 of the
   Procedure, or say so and a worker runs them.

## Not part of this experiment: a second revision

`judge_revision_cap` (default 1) lets the judge revise a revision that fails again; the stage supports 2. Nothing here
measures it, and it stays 1. Measuring it would need a runner that revises each fixture's rejects on its own, which this
change doesn't build. The cost for one trajectory, by VERIFY-astra's rates: about 17 eligible rejects × $0.31 (one Opus
rewrite plus both judges) ≈ $5.27 for first rewrites, before second rewrites and benefit-naming calls. It also needs
a blind label from Aadi for every rewrite a second round promotes. Register it on its own, with its own bound, before any
call.
