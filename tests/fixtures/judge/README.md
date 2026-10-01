# Judge validation fixtures

Test data for `uv run python -m simula.validate validate-judge` (ARCHITECTURE §5). Never a proposer input.

- `known_good/<id>.json`: a good idea for one app, unmutated. `source` is `base` (a base the planted defects change) or `run` (a real candidate from a propose run that Aadi approved as good; `from_run` says which).
- `known_good/models/<app>.json`: a product-model sketch for an app outside the test set.
- `planted/<id>.json`: a base with the fields its defect touches changed, aimed at one check; or a real candidate from a run that fails one check.
- `labels/<id>.json`: blind human labels written by `python -m simula.validate label`.
- `regression/<id>.json`: a real idea from a run that the judges must fail, on `target` when it names a check. `model` points to `regression/models/<run>.json`, the product model its judge saw, kept to the fields the judge reads. Held out of the gate; validate-judge's report shows its verdict.
- `flagged/<id>.json`: a pick that doesn't clearly break its target as written, held out of the gate until Aadi decides; `git mv` it into `planted/` to count it.

Ids: lowercase with dashes, no underscores (`kg-fitness-01`, `pd-g-policy-flagrant`).

## known_good

```json
{
  "id": "kg-fitness-01",
  "source": "base",
  "app": "a fitness tracker",
  "app_type": "fitness",
  "in_test_set": false,
  "model": "judge/known_good/models/fitness.json",
  "author": "aadi",
  "filled_by": {},
  "note": "",
  "candidate": { "every CandidateDraft field except id (the case id) and lens (optional)": "..." }
}
```

`model` is a path under `tests/fixtures/`: `golden/<app>/product_model.json` for a test app, or a sketch. `in_test_set` must match it (true exactly when the model is a golden one); the loader refuses a mismatch, so the outside-the-test-set requirement can't be met by a label. `filled_by` marks each candidate field the source didn't state, `"aadi"` or `"agent"`.

A sketch holds only what a judge reads; code fills the rest with empty defaults:

```json
{
  "app": "fitness",
  "app_category": "utility",
  "states": [{"id": "s01", "name": "Today", "purpose": "Daily workout plan.", "content_rating": "safe",
              "elements": [{"id": "s01.e01", "text": "2 of 3 free workouts left this week", "role": "meter"}]}],
  "mechanics": [{"id": "m01", "kind": "limit", "evidence_ids": ["s01.e01"], "summary": "3 free workouts a week.",
                 "observed_numbers": ["3"], "status": "observed"}],
  "value_ledger": [{"id": "l01", "kind": "limit", "verbatim": "2 of 3 free workouts left this week", "evidence_ids": ["s01.e01"]}],
  "open_questions": []
}
```

## planted

```json
{
  "id": "pd-g-policy-flagrant",
  "target": "g_policy",
  "tier": "flagrant",
  "one_liner": "Aadi's line, verbatim from the worksheet",
  "author": "aadi",
  "expanded_by": "claude-opus-5-5",
  "base": "kg-fitness-01",
  "change": {"decline_path": "Declining uses up one of the week's free workouts."}
}
```

A real candidate that fails one check (Aadi approved it from a run) replaces `base` and `change` with the full Candidate and where it came from:

```json
{"id": "pd-c1-subtle-aol-c07", "target": "c1_revealed_value", "tier": "subtle", "one_liner": "...", "author": "aadi",
 "from_run": "PR 5 round 6: runs/aol/<run id>/propose/candidates.json c07", "app": "aol", "app_type": "news",
 "in_test_set": true, "model": "golden/aol/product_model.json", "candidate": {"...": "the full Candidate"}}
```

`target` is one of `g_policy g_no_cash g_no_chat_content g_no_free_removal g_brand_safety c1_revealed_value c2_evidence c3_spares_payers c4_protects_subscription c5_moment c6_fits_simula c7_specific c8_economics`. `change` names the candidate fields that carry the defect: the one the defect sits in, plus every other field a judge reads that would otherwise still tell the base's story, so the idea never contradicts itself (a `reward` or `cost_inputs` change may give only the keys it changes). A `c8_economics` case also sets `"expect_economics": "PASS" | "CONDITIONAL" | "FAIL" | "dropped"`: what `economics.py` must say about it (`dropped` = its cost inputs can't price the reward kind).

The gate needs 1 flagrant + 1 subtle per LLM-judged check (24), plus 2 for C8, across at least 3 app types including an app outside the test set. The worksheet's "Copy all" lines (`<check> / <tier> / app: <app> : <one-liner>`) map to `target`, `tier`, the base's app, and `one_liner`.
