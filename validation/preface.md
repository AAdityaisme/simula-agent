# 2026-09-30: the live rubric is VF3 (VF′ plus `c3_spares_payers`)

VF3 is VF′ with one judgment check added. `c3_spares_payers` fails an offer that sits inside what paying users pay for; one paying users see that is pitched as more of what their plan sells, or whose reward their plan already includes; or one that members of an ad-free plan see. An offer only non-paying users see passes, even when its reward is a slice of the plan. An offer for something outside the plan and outside what payers pay for passes, even when only paying users see it. The new fixtures test it:
- pd-c3-flagrant: Luzia+ members offered a benefit their plan includes.
- pd-c3-subtle: an ad-free plan's members shown the offer, in a news-reader sketch.
- kg-fitness-01: a known-good offer only paying users see, for a streak freeze their plan doesn't sell. Agent-written; not yet approved by Aadi.
- kg-newsreader-01: a known-good offer only free readers see, in the ad-free-plan app. Agent-written; not yet approved by Aadi.
- rg-janitorai-c03-rev-payers: the committed JanitorAI c03-rev, as a regression case.

**VF3 was paid for three times, and every run is reported.** Three full `validate-judge` runs were committed in turn, each over the last: 42f9428, 9092006 and e56402e. All three are saved: `validation/verdicts/VF3-42f9428/`, `validation/verdicts/VF3-9092006/` and the kept `validation/verdicts/VF3/`, each with a `run.toml` naming its commit and the c3 wording it judged with. Between them only c3's wording and the c3 fixtures changed, so for the five gates they are replicates. The generated sections under "Judge validation" score the kept run, run 3. "Every paid run" scores all three, and the rerun section and the gate count flips over all three: 4 of 60 rerun pairs flipped a safety verdict, and run 3 is the only run with none. One of the four is a planted cash reward, pd-g-no-cash-subtle, that judge_2 caught in run 1's first round and missed on its rerun. A 1–1 split between a round and its rerun is not a catch, so judge_2 is scored as missing it in run 1. Everything from "How the live judge rubric was chosen" to "Judge validation" is about VF′ and its three saved runs in `validation/verdicts/VF2/`, kept as the record. *(Corrected 2026-10-01: this paragraph said the generated sections were "one" run because the API cap paid for one, and that the gate reruns flipped no safety verdict. Both described run 3 alone.)*

**Edits made after seeing a failure.**
- kg-fitness-01 was edited after it failed. In run 2, judge_2 failed it on c2: "The what-makes-it-work and subscribers fields claim free users see the Plus trial button instead of this offer, but s02.e04 does not establish who sees that button. The trigger field also says opening the app opens s02, which the model does not establish." a396f94 removed exactly those two claims, and run 3 passed it.
- c3 was reworded in the same commit after a planted case failed it. In run 2, judge_2 failed c3 on pd-g-brand-safety-flagrant, an offer only free users see whose reward is part of Luzia+. The new wording passes such an offer. The same commit rewrote the pd-c3-flagrant fields that still told its base's free-user story.
- Both are tuning on the validation set, which the section below declines to do for judge_2. Both new known-goods are also agent-written, so the generated sections keep them out of the known-good rate and show them on their own row.

**The 4 original known-goods.** Combined, VF′ passes 1 of 4 (majority of 3) and VF3's three runs pass 1, 2 and 2. The whole difference is one verdict: judge_2's c2 on JanitorAI c02, a check c3 doesn't touch. It failed in 2 of VF′'s 3 runs and in 1 of VF3's 3 (run 1), so it is noise in judge_2's c2, not a gain from c3.

For comparison, VF′'s three runs, scored by `uv run python -m simula.validate summarize --runs validation/verdicts/VF2 --only-judged --out <file>` on the fixtures VF2 judged, catch the 22 original planted defects 22/22 combined (judge_1 22, judge_2 20) and pass the 4 original known-goods 1/4 combined (judge_1 3/4, judge_2 1/4).

# How the live judge rubric was chosen (read this first)

**The adoption bar, set after seeing earlier rounds' failures, was not met by either rubric (known-good 1/4 each). VF′ dominates V0 on every item of the bar (combined verdicts). Per judge, judge_2's C7 falls from 1/2 to 0/2. VF′ was adopted on that dominance (D9); three of its clauses were written after seeing the failures; judge_2 fails c2 on 3 of 4 known-goods under both rubrics; 0 human labels.**

**PR 6's merge gate (D10).** PR 6's harness gate (each judge ≥ 70% known-good; no check at 0/2) is **not met by judge_2 under either rubric**. The PR merges with that disclosed, because tonight's end-to-end run needs the judge stage and the only fix, reworking judge_2's prompt, would be tuning on the validation set. Splits now become labelled CONDITIONAL ideas (D10), so judge_2's strictness shows as a flag instead of emptying the deck. *Superseded 2026-09-29 by D11: a split isn't drawn; it waits on the deck's Needs your call page, and only when nothing is accepted is the top split drawn, labelled as the closest idea (see below).*

*(D11, 2026-09-29, Aadi: a split idea no longer gets full slides. It is listed on one "Needs your call" page at the end of the deck, each split check as its open question with both judges' reasons, and it is drawn only if a person names it in `flows/approvals.json`. Full slides go to accepted ideas, and to the judge's fallback pick when nothing survives.)*

What D10 and D11 do, measured on the saved VF′ runs through the stage's own `decide()` (annotate mode, the live one; the stage calls each judge once, so each run is one stage outcome):

| Run | Known-good ideas on full slides | Known-good on Needs your call | Known-good rejected | Planted defects on full slides | Planted defects on Needs your call |
|---|---|---|---|---|---|
| 1 | 1 (Luzia c05) | 2 (Candy Crush, JanitorAI c02) | 1 (AOL c01) | 0 of 22 | 2 (pd-c7-flagrant, pd-c7-subtle) |
| 2 | 2 (JanitorAI c02, Luzia c05) | 1 (Candy Crush) | 1 (AOL c01) | 0 of 22 | 2 (same) |
| 3 | 1 (JanitorAI c02, the top-ranked split, drawn as the closest idea: nothing was accepted) | 2 (Candy Crush, Luzia c05) | 1 (AOL c01) | 0 of 22 | 2 (same) |

- **Before D10**, a judge_2-only fail went to a person and out of the deck: 1, 2 and 0 of the 4 known-good ideas reached flows in runs 1–3.
- **Under D10 alone** (81c5945), 3 of 4 reached full slides in every run, and so did **both planted C7 defects, in 3 of 3 runs**: judge_2 is blind on C7, so every C7 defect judge_1 catches was a split. That is the cost D10 alone carried.
- **Under D11**, no planted defect reaches a full slide on its own merit; both C7 defects wait on Needs your call with judge_1's reason against them. The table scores each idea beside an accepted one. In a deck where nothing is accepted, the top-ranked split, planted defect or not, is drawn as the closest idea (below), and its slide names the doubted check with both reasons. Known-good ideas on full slides are 1, 2 and 1; the others judge_1 accepts (2, 1 and 2) wait on Needs your call instead of being lost.
- **Run 3 as a deck:** no accept, and the three split ideas count as survivors, so the judge's fallback doesn't fire. flows then draws the top-ranked split as the closest idea ("Closest idea: The reviewers split on …; confirm it before building", with both reasons), so the deck has exactly one full slide and the other two wait on Needs your call. *(Decided for Aadi, 2026-09-29: a deck with no full slide fails both the finished-product bar and the assignment's flows. With at least one accept nothing changes. The judge stage is unchanged.)*
- AOL c01, which both judges fail on c2, stays out. A gate any judge fails still rejects, and a person is asked about a judge's call only when that call failed.

The three clauses written after seeing the failures are the absence clause (C2), the plain-meaning clause (C2), and the C1 clarification of where the player uses the reward. The rest of VF′ came from earlier rounds, which were shaped by earlier failures too (below).

**VF′** was the live rubric (`prompts/judge/rubric.md`) from 2026-09-29, adopted by decision D9, made for Aadi, until
VF3 replaced it on 2026-09-30 (top). It is the earlier rubric V0 with these changes:
- **Product facts.** An open question never supports a claim. A mechanic marked `inferred` supports one when the model
  cites the observed element it was inferred from.
- **C1.** A product change passes only if its new resource is a slice of something charged for, or something the player
  gets for their own use in an observed core flow. It fails if most of the reward goes to someone else. How a reward is
  earned (always by the game) doesn't count; where the player uses it does.
- **C2.** C2 is scoped by the kind of claim, in any field, never by the field.
  - Not claims about the app, so they never fail C2: the proposal's own additions, its predictions, labelled
    assumptions, and ordinary navigation.
  - Restating an observed element in plain words is supported; adding a fact no element states is not.
  - An absence claim is supported when the model records no such mechanic and the idea words it as "not seen".
- **C4.** C4 also fails a grant that never expires, even a count-limited one. It also fails taking from other users or
  creators what they get today (list place, visibility), unless the app sells that. The app's own ad revenue is left to
  the cost mark.

**The bar was set post hoc.** Every clause above, and the adoption bar below, was written after seeing the earlier
rounds' failures, on the same 4 known-good ideas. The out-of-sample checks are:
- the held-out real cases c07 and c09 (below);
- the next fresh end-to-end run.

**Neither rubric meets bar item (c).** V0 and VF′ each pass 1 of 4 known-good ideas combined. VF′ is adopted because
it dominates V0 on every item of the bar:

| Bar item (combined verdicts, majority of 3 `--no-cache` runs per judge) | V0 | VF′ |
|---|---|---|
| (a) every gate's flagrant planted defect caught | 5/5 | 5/5 |
| (b) planted catches per check, at least V0's | 21/22 (c4 1/2) | 22/22 (c4 2/2), equal or better on every check |
| (c) known-good passed, at least 3/4 | 1/4 ✗ | 1/4 ✗ |
| (d) c07 and c09 fail under both judges | no: judge_1 passes both on all 11 checks | yes: both judges fail c1, c2 and c4 on both, 3/3 runs |

**Pooled recall and small n.**
- Planted defects caught, combined: V0 21/22 [78%–99%], VF′ 22/22 [85%–100%] (Wilson 95%).
- Known-good passed: 1/4 [5%–70%] under both.
- Exact McNemar vs V0: p = 1.0 on both sets.
- With 2 cases per check, the per-check table below is a smoke test.

**Open limitation (a candidate for after submission).** judge_2 fails C2 on three of Aadi's known-good ideas under VF′,
while judge_1 passes two of them:
- **Candy Crush (3/3 runs):** "a gold-bar sale for a full set of lives" isn't in the model, which shows only "Get more
  lives with gold bars".
- **JanitorAI c02 (2/3 runs):** "skip a normal queue… earlier placement in line" goes beyond "Priority routing for
  faster replies". judge_1 accepts this as the plain meaning of priority routing.
- **AOL c01 (3/3 runs, both judges):** "The app has no paywall, limit, or currency" is stated as fact. The model says
  only that none was seen on the home feed. The absence clause fails this wording by design, and a revision can fix it.

Whether judge_2's reading of small restatements is too strict is open.

**A second limitation: judge_2 is blind on C7 under VF′ (0 of 2).**
- It was blind under V2, V4 and VF in phase 2 too.
- Under V0 it catches 1 of 2 (pd-c7-flagrant).
- The combined verdict still catches both C7 cases through judge_1. "Caught" in this report means failed by any judge: rejected, or held for a person (a split waits on Needs your call, D11). A split still reaches a full slide when a person approves that exact disagreement, or, in a deck with nothing accepted, as the closest idea, labelled with the doubted check. Under D10 alone both C7 defects were drawn as labelled CONDITIONAL ideas (above).

**How the sections below were computed.**
- `validate.report` ran on VF′'s majority-of-3 verdicts per judge. *(2026-09-30: the sections below now score VF3's kept run, the last of three paid runs; see the top.)*
- The planted set is 22 cases. pd-c4-subtle counts from here on because VF′'s C4 fails a grant that never expires.
- 4 known-good ideas; c07 and c09 are held out; the C8 cases are scored by code.
- In the `--no-cache` rerun section, each gate case is compared with the first of its 3 runs that disagrees with the
  majority, so a flip there means the 3 runs disagreed.
- Across every (case, check) pair, the 3 runs disagreed on 1 of 330 for judge_1 and 10 of 330 for judge_2 (8 of them
  on c2).
- Every saved verdict is committed: `validation/verdicts/VF2/` for VF′ and `validation/verdicts/V0/` for V0, as
  `<case>_<judge>_r<n>.json`.
- `uv run python -m simula.validate summarize` rebuilds this file from `validation/verdicts/VF3/` (VF2 until 2026-09-30), the runs its `run.toml` says it
  replaced, and this preface
  (`validation/preface.md`) with no calls.
- `simula validate-judge` writes a fresh single-run report to `validation/latest/`, never over this file.

**The harness gate below is not the adoption rule.** It judges VF′ alone *(VF3 since 2026-09-30)*, against fixed thresholds. Run on V0's saved runs, the same gate trades lines rather than improving: V0 fails judge_1's known-good line (50%), which VF′ passes (75%), and VF′ fails judge_2's broken-check line (C7 0/2), which V0 passes (1/2).
- judge_1 passes every item.
- The gate fails on judge_2 alone: C7 is broken (0/2), and known-good is 25%, under 70%.
- *(VF3, 2026-10-01: counted over its three paid runs, the rerun item fails for both judges.)*

The adoption rule is VF′'s dominance over V0 above. V0 fails the same judge_2 known-good item, at 25% too.
