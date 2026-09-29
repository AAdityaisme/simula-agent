# How the live judge rubric was chosen (read this first)

**The pre-registered bar was not met by either rubric (known-good 1/4 each). VF′ dominates V0 on every item of the bar (combined verdicts). Per judge, judge_2's C7 falls from 1/2 to 0/2. VF′ was adopted on that dominance (D9); three of its clauses were written after seeing the failures; judge_2 fails c2 on 3 of 4 known-goods under both rubrics; 0 human labels.**

**PR 6's merge gate (D10).** PR 6's harness gate (each judge ≥ 70% known-good; no check at 0/2) is **not met by judge_2 under either rubric**. The PR merges with that disclosed, because tonight's end-to-end run needs the judge stage and the only fix, reworking judge_2's prompt, would be tuning on the validation set. Splits now become labelled CONDITIONAL ideas (D10), so judge_2's strictness shows as a flag instead of emptying the deck.

What D10 changes, measured on the saved VF′ runs of the 4 known-good ideas (the stage calls each judge once, so each run is one stage outcome):
- **Before D10**, a judge_2-only fail went to a person and out of the deck: 1, 2 and 0 of the 4 reached flows in runs 1–3.
- **With D10**, 3 of 4 reach flows in every run. A split idea is CONDITIONAL, ranked below every accept, and carries both judges' reasons.
- AOL c01, which both judges fail on c2, stays out.
- A gate any judge fails still rejects, and a person is asked only when a judge's call failed.

The three clauses written after seeing the failures are the absence clause (C2), the plain-meaning clause (C2), and the C1 clarification of where the player uses the reward. The rest of VF′ came from earlier rounds, which were shaped by earlier failures too (below).

The live rubric (`prompts/judge/rubric.md`) is **VF′**, adopted 2026-09-29 by decision D9, made for Aadi. It is the
earlier rubric V0 with these changes:
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
- The combined verdict still catches both C7 cases through judge_1.

**How the sections below were computed.**
- `validate.report` ran on VF′'s majority-of-3 verdicts per judge.
- The planted set is 22 cases. pd-c4-subtle counts from here on because VF′'s C4 fails a grant that never expires.
- 4 known-good ideas; c07 and c09 are held out; the C8 cases are scored by code.
- In the `--no-cache` rerun section, each gate case is compared with the first of its 3 runs that disagrees with the
  majority, so a flip there means the 3 runs disagreed.
- Across every (case, check) pair, the 3 runs disagreed on 1 of 330 for judge_1 and 10 of 330 for judge_2 (8 of them
  on c2).
- Every saved verdict is committed: `validation/verdicts/VF2/` for VF′ and `validation/verdicts/V0/` for V0, as
  `<case>_<judge>_r<n>.json`.
- `uv run python -m simula.validate summarize` rebuilds this file from `validation/verdicts/VF2/` and this preface
  (`validation/preface.md`) with no calls.
- `simula validate-judge` writes a fresh single-run report to `validation/latest/`, never over this file.

**The harness gate below is not the adoption rule.** It judges VF′ alone, against fixed thresholds. Run on V0's saved runs, the same gate trades lines rather than improving: V0 fails judge_1's known-good line (50%), which VF′ passes (75%), and VF′ fails judge_2's broken-check line (C7 0/2), which V0 passes (1/2).
- judge_1 passes every item.
- The gate fails on judge_2 alone: C7 is broken (0/2), and known-good is 25%, under 70%.

The adoption rule is VF′'s dominance over V0 above. V0 fails the same judge_2 known-good item, at 25% too.
