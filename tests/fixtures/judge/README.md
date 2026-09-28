# Judge validation fixtures

Test data for `uv run python -m simula.validate validate-judge` (ARCHITECTURE §5). Never a proposer input.

- `known_good/<id>.json`: a good idea for one app, unmutated. `source` is `base` (a base the planted defects change) or `deck` (one of Simula's Luzia deck ideas).
- `known_good/models/<app>.json`: a product-model sketch for an app outside the test set.
- `planted/<id>.json`: a base with exactly one field changed, aimed at one check.
- `labels/<id>.json`: blind human labels written by `python -m simula.validate label`.

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

`model` is a path under `tests/fixtures/`: `golden/<app>/product_model.json` for a test app, or a sketch. `filled_by` marks each candidate field the source didn't state, `"aadi"` or `"agent"` (deck ideas).

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

`target` is one of `g_policy g_no_cash g_no_chat_content g_no_free_removal g_brand_safety c1_revealed_value c2_evidence c4_protects_subscription c5_moment c6_fits_simula c7_specific c8_economics`. `change` names exactly one candidate field (a `reward` or `cost_inputs` change may give only the keys it changes). A `c8_economics` case also sets `"expect_economics": "PASS" | "CONDITIONAL" | "FAIL" | "dropped"`: what `economics.py` must say about it (`dropped` = its cost inputs can't price the reward kind).

The gate needs 1 flagrant + 1 subtle per LLM-judged check (22), plus 2 for C8, across at least 3 app types including an app outside the test set. The worksheet's "Copy all" lines (`<check> / <tier> / app: <app> : <one-liner>`) map to `target`, `tier`, the base's app, and `one_liner`.
