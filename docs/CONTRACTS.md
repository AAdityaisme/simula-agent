# Contracts (frozen in PR 0)

Every later PR builds on these. Changing one needs Aadi's approval and its own small PR. The schemas themselves live in `simula/contracts.py`; this page explains the parts code can't.

## 1. Generality rule

Nothing in code or config is tuned to one app. The only per-app input is the package name:

```toml
# config/apps/janitorai.toml
package = "com.janitor.ai"
```

Everything else is generic behavior or a run flag:

| Was per-app | Now |
|---|---|
| exploration size | `--budget deep` (80 actions / 25 min) or `--budget transfer` (40 / 12, default) |
| account creation | `--allow-account-create`, off by default |
| content filter | the explorer looks for a content or safety filter on the root screen, picks the most restrictive option, checks it by screenshot, re-applies it after every relaunch (PR 1) |
| content rating | per state, from the screenshot pass, plus the generic `adult_keywords` list in `config/profiles.toml` |
| dynamic regions (ads, clocks) | pixels that differ between two captures of the same state are masked in QA and recorded in `State.dynamic_regions` |
| app category | written by the model stage (`ProductModel.app_category`) |
| blocked apps | the explorer records a blocked root generically |

Tests use fixtures from all three test apps, and `tests/test_config.py` fails if an app config grows a second key.

## 2. Geometry

- Device: 1080×2400 px, density 420, **2.625 px per dp**.
- Status bar: y 0–136 px (51.8 dp). Gesture bar: y 2337–2400 px (24 dp). **Content area: y ∈ [136, 2337).**
- mobile-mcp `coordinates` are raw device pixels.
- `rect_dp` is in content coordinates: `x / 2.625`, `(y − 136) / 2.625`, `w / 2.625`, `h / 2.625`.
- The mock renders in Playwright at viewport **411×914, device_scale_factor 2.625**. Chromium outputs **1079×2399**, not 1080×2400.
- The mock body reserves the bars with CSS insets: 51.8px top, 24px bottom.
- **Pixel comparisons run in content dp**: resize the render to 1080×2400 (Lanczos), crop both images to y 136–2337, resize both to **411×838** (Lanczos). Identity gate: SSIM ≥ 0.985 and identity pixelmatch < ¼ of a 10 px shift's.
- Insets are stored per run from the status-bar element, not assumed for other devices.

## 3. Run folder

```
runs/<app>/<run_id>/                run_id = YYYYMMDD-HHMMSS-<git sha>[-fixture][-N]
  manifest.json                     git sha, profile, budget, roles, prompt hashes, app package + version,
                                    mobile-mcp + Playwright versions, caps, provenance, stages done, $ total
  trace.jsonl                       one line per decision (TraceLine)
  needs-human.md                    only when a person is needed: what, why, evidence, exact command to continue
  exhibits/NN-<stage>.md            one readable summary per stage, written by code
  explore/  explore.json · states/<sid>.png · states/<sid>.elements.json (raw MCP reply) · states/<sid>.json · actions.jsonl
  model/    product_model.json · product_model.md · states/<sid>.png · assets/<element_id>.png
  mock/     index.html · assets/ · renders/<sid>.png · contract_report.json
  qa/       round<N>/… · approved/index.html · approved/assets/ · qa_report.json
  propose/  lenses.json · candidates.json
  judge/    verdicts/ · revisions.json · decisions.json · human-queue.md · pairwise.json · order_swap.json
  flows/    approvals.json? · <cand>/index.html · <cand>/screens/ · slides.html · slides.pdf
  <stage>/done.json | <stage>/failure.json
runs/<app>/latest -> <run_id>
```

- Spend outside a run has its own trace: `build/trace.jsonl` (build spend, hand fixes via `simula note`, and one line per paid `simula doctor --keys` probe) and `validation/trace.jsonl` (`simula validate-judge`, under its own $20 cap).
- Run ids are `YYYYMMDD-HHMMSS-<git sha>`, plus `-fixture` for a fixture run, plus `-2`, `-3` … when two runs start in the same second.
- A stage writes only its own folder. It never edits another stage's.
- Every JSON file a stage writes carries `schema_version`. Files that hold a list use a wrapper from `contracts.py` (all code-written, none model-facing):

| File | Wrapper |
|---|---|
| `explore/explore.json` | `ExploreFile` |
| `explore/states/<sid>.json` | `StateFile` |
| `model/product_model.json` | `ProductModel` |
| `mock/contract_report.json` | `ContractReport` |
| `qa/round<N>/metrics.json` | `QAMetrics` |
| `propose/lenses.json` | `LensesFile` |
| `propose/candidates.json` | `CandidatesFile` |
| `judge/decisions.json` | `DecisionsFile` |
| `<stage>/done.json` | `DoneMarker` |
| `manifest.json` | `Manifest` |
- `done.json` holds the hashes of every input, prompt, params, and output file. `simula run` skips a stage only when all four still match. Rerunning an upstream stage changes its outputs, so everything below it reruns. It is written to a temp file and renamed, so a crash never leaves a half marker.
- `failure.json` replaces `done.json` when a stage fails.
- Stage 3 (mock) reads `model/` only: the model folder carries its own canonical screenshots (`model/states/`), so another agent could mock the app without the explore folder.

### explore/ → model/ hand-off (PR 1 writes, PR 2 reads)

- `explore/states/<sid>.json` (`StateFile`): `screenshot` and `elements_reply` are paths relative to `explore/` (`states/<sid>.png`, `states/<sid>.elements.json`). `elements_reply` is the raw `mobile_list_elements_on_screen` result saved as JSON (`{"content": [{"type": "text", "text": "Found these elements on screen: [...]"}], "isError": false}`), and `""` only when no tree was captured. These two files are the state's **canonical capture**, taken the first time the state was seen. Every `mcp_ref` anywhere in `explore/` refers to that state's canonical capture.
- State ids are `s01`, `s02`, … in discovery order. The root is the lowest-numbered state whose kind is `screen`.
- `icon_labels` names listed elements that have no text or label (the Sonnet icon pass), by `mcp_ref`; stage 2 writes the name into `Element.label`. `vision_elements` are controls the tree doesn't list: a name plus a `rect_px` box (a 48 dp square around the model's point, clipped to the content area); stage 2 turns each into an `Element` with `source: vision` and `mcp_ref: null`. `blocked_reason` is set whenever `kind` is `blocked`.
- `explore/explore.json` (`ExploreFile`) carries `device`: screen size, density, and the content insets measured from the status-bar and gesture-bar elements. Stage 2 copies it into `ProductModel.device` and uses it for every `rect_dp`, crop, and color.
- `explore/actions.jsonl`: one `ActionLine` per action the explorer executed or denied, in order.
  - `action` is `tap | swipe | back | type | relaunch`. Only the first four can become edges; `relaunch` lines are bookkeeping.
  - `mcp_ref` is the tapped element's ref in `from_state`'s canonical capture (the explorer maps a tap made on a revisit back to it by label and bucketed rect), or `null` for back, swipe, and vision taps. `tap_px` is where the tap landed, in device px (`null` if the action wasn't a tap). Stage 2 uses the `mcp_ref` element only if its rect contains `tap_px`; otherwise the smallest canonical element that contains `tap_px`; otherwise none.
  - `to_state` is the state the move reached, or `null` when the action was denied or never ran. A move that leaves the app has `outcome: ok` and `to_state` = the recorded `external` state.
  - `outcome` is `ok | denied | timeout | error`. Only `ok` lines with a `to_state` become edges. A move that stays on the same state becomes an edge only when `change_summary` is non-empty (a counter "3 → 2", a toggle).
  - `transition` follows the Edge rule in §6 and is set by the explorer when the move is recorded.
  - **Core-loop passes** (the explorer repeating the app's core action): `loop_pass` is 1, 2, … on each pass and null on every tour move. `change_summary` carries the pass's measurements as comma-separated `<what> <number> <unit>` parts, e.g. `reply started 2.1 s, finished 9.4 s, 612 chars` or `no reply within 45 s`; a part without a number is ignored. `loop_stop` is set on the pass where a limit, paywall, or ad appeared, in a few words (`limit banner`, `paywall`, `interstitial ad`), and the loop stops there. Stage 2 turns the passes into `experience` ledger items (§6).
- Edge ids: `<element id>><to_state>` when a listed or vision element was tapped, else `<from_state>.<action>><to_state>` (e.g. `s03.back>s01`). The first recorded move between the same element and target wins. Edges with `element_id: null` have no `data-el` to hang on; the mock wires `back` edges to its back control and may skip the rest.

## 4. Provenance: fixtures are test data only

- `tests/fixtures/` and the golden models build and unit-test stages. They never produce a deliverable.
- Every `done.json` and the manifest carry `provenance {source: explorer_run | fixture}`. Any fixture upstream makes the output a fixture.
- Every stage refuses fixture input without `--allow-fixtures`. With it, the run id ends in `-fixture`, exhibits carry a banner, and slides a watermark.
- `--fixture STAGE=PATH` is **the only way** a fixture enters a run, and only into a run the same call creates: `simula run APP --new --allow-fixtures --fixture STAGE=PATH`. Seeding an existing run is refused (exit 2) and leaves it untouched.

## 5. Schemas a model fills

`MODEL_FACING` in `contracts.py`: `ModelMeaning`, `IconPass`, `HardScreenAction`, `Critique`, `Edits`, `LensOutput`, `BenefitNames`, `Verdict`, `PairwisePick`.

- No `dict` fields and no recursion. The Anthropic SDK turns a dict field into an object that can only be `{}`, and pydantic still accepts it, so a dict-shaped verdict would pass everything. `tests/test_schema_gate.py` checks both SDKs.
- At most 4 levels of nesting; `extra="forbid"`.
- Fixed named fields wherever the keys are known: the judge's 5 gates and 6 judgment checks are properties, not a map.
- No `minLength`, `maximum`, or `pattern` (the API drops them). Code checks those after parsing.
- `CostInputs.currency_amount`: the USD price the app charges for exactly what the reward grants; 0 when no price was observed.
- `CandidateDraft.after_reward` (added in PR 5, Aadi-approved): one plain sentence on what the user sees when the reward runs out and why that moves them toward paying, returning, or watching again. It defaults to `""` so older files parse; propose drops a candidate that leaves it empty.
- `CandidateDraft.for_users` and `CandidateDraft.grants_id` (added in PR 5, Aadi-approved): who is offered the reward (`free`, `paying`, or `everyone`), and the `value_ledger` id of the paid benefit the reward is a piece of, or more of (null for a new resource). Both are required. Propose drops an idea whose `grants_id` isn't a ledger id, and more of a bullet for payers when the bullet has no number and no observed limit cites it; it dedupes on (`grants_id`, `for_users`).
- `ModelMeaning.terms` and `ModelMeaning.open_questions` are required in the model's answer (it may return `[]`); `ModelMeaning` is never stored, so no file changes. The stored `ProductModel.terms` and `ProductModel.questions` default to `[]`.
- **Every check of a model's words against app text uses `simula/text.py`**: `text.find(quote, app_text, spaced=True, ignore_case=False)` returns the app's own span or None (any whitespace run matches any other; `spaced=False` ignores whitespace, for numbers), and `text.same(a, b)` compares with whitespace normalized. App trees carry non-breaking spaces (`$\xa01.99`) that a model types back as plain spaces.

## 6. Who writes which field

Code writes every number and id; a model writes meaning keyed by ids code gave it; code validates the merge.

| Object | Code writes | A model writes |
|---|---|---|
| ProductModel | app, app_version, run_id, device, coverage, provenance, edges, the `experience` ledger items, `open_questions` (the text of `questions`) | app_category, flows, mechanics, cross_screen_values, value_ledger (except `experience`), terms, questions |
| State | id, kind, parent_id, fingerprint, canonical_png, elements, in_mock_scope, dynamic_regions (**device px**, like `rect_px`; QA converts them to content dp), blocked_reason | name, purpose, content_rating |
| Element | id, mcp_ref, type, text, label, source, rects, asset_png, colors, font_px, in_mock, repeat_group | role, font_guess |
| Edge | everything, including `transition` (closing a modal → back, opening a modal or sheet → modal, a tab tap → tab, BACK → back, root replaced → replace, else push) | nothing |
| Flow | validates that every edge id exists and the hops connect | id, name, purpose, edge_ids, evidence_ids |
| Evidence ids (mechanics, ledger, values, flows) | each must be a recorded state, element, or edge id; a paywall, limit, or currency must cite an element | the ids |
| LedgerItem | checks `verbatim` matches its evidence element's text or label (via `simula/text.py`) and stores the element's exact span. Writes every `experience` item: `verbatim` is then the measured fact (median, min, max, n over the core-loop passes, or what stopped the loop, or "After N passes … nothing limited it"), **not tree text**, and `evidence_ids` are the edges of those passes. A model may not write `experience` items | everything else |
| Term | `observed`: true only when a `defined_by` element's own text or label carries the term and that element is not the evidence of a `used_in` line (a bullet can't define itself); otherwise `meaning` becomes "meaning not observed", `defined_by` is emptied, and **nothing downstream may build on the term**. Known limit: the check only proves the word was on a cited screen; the meaning stays the model's reading. Rejects a term that no kept mechanic or ledger line in `used_in` uses | term, meaning, defined_by, used_in |
| OpenQuestion | rejects a `start_state` that isn't a recorded state; keeps at most 5 (the model orders them, most monetization-relevant first); `answered` starts false and is set by the targeted explore pass (a later PR) | id, question, start_state, look_for |
| Candidate | id (`c01`, `c02`, … in draft order), the bucket prefix in `title` (`Existing opportunity: ` or `Product change: `), economics, reach_score, rank_score, dropped_reason, flags (concerns code raises that don't drop the idea, one readable sentence each, e.g. `uses "Pro", whose meaning was never observed`; the judge sees them). The economics cost term comes from `reward.kind`; `app_category` only breaks ties | everything else in CandidateDraft |
| Verdict | nothing | every check, `other_concern`, `fixable` |
| Decision | everything. `checks_total` = the 11 LLM-judged checks (5 gates + 6 judgment checks); `checks_passed` counts a check only when every judge that ran passed it | nothing |

## 7. Mock contract (data attributes)

- One static `mock/index.html`, inline CSS/JS, no build step. Images only from `assets/<element_id>.png`. Google Fonts allowed; every other request is blocked.
- `<section data-screen="s03">` per in-scope state. Modals: `data-screen="s07" data-parent="s03"`, drawn as an overlay.
- `data-el="s03.e07"` on each `in_mock` element; only the `data-el` tag is limited to the first 2 items of a repeated list. `in_mock` means "the mock draws this element", and every element that starts an edge is `in_mock`.
- `data-edge="s03.e07>s05"` on each tappable element, plus **`data-transition="push|modal|tab|back|replace|unknown"`** copied from the edge. A click shows the edge's `to_state` screen with that transition: push slides, modal fades in an overlay, tab switches instantly; `prefers-reduced-motion` turns animation off. `unknown` renders as `push`, and QA skips the transition check for it.
- `data-chrome="header|tabbar"` on shared chrome (one shared DOM fragment). `data-value="<cross_screen_value id>"` on shared values.
- `window.simula.go(id)`, `window.simula.state()`, `window.simula.reset()`.
- No screenshot wallpaper: no single `<img>` may cover more than 40% of a screen's content area.
- Flow screens added in stage 7: `data-screen="new:<slug>"`, `data-flow="<candidate id>"`.
- Scope is chosen by code (stage 2): the root, every state showing a paywall, limit, currency, or ad, every state showing any other mechanic (a modal or sheet brings its parent), and every state on the core flows; no fixed cap; a tab or depth-1 screen is in only when it is on a flow or holds a mechanic; never `blocked` or `external`. An `unsafe` state stays out unless it is on a core flow, where a gap would break the flow (decided 2026-09-28, replacing "unsafe never removes a state"). `ProductModel.mock_order` lists the scope in that priority order (added 2026-09-28).

## 8. Trace, cache, money

- **Trace line:** `{ts, stage, step, decider: code|jev|model|human, model, effort, confidence, tokens_in, tokens_out, tokens_cached, usd, cache_hit, outcome, note}`. `confidence` is null unless the decider returns one (Jev does). Build spend and hand fixes go to `build/trace.jsonl` through `simula note`.
- **Cache** (`cache/<sha256>.json`): the key covers provider, model, system prompt, messages (image bytes replaced by their sha256), effort, max_tokens, the JSON schema, and the attempt number. Only answers that pass validation are stored as answers, and a retry bumps the attempt, so a bad answer is never replayed as an answer. Every failed attempt is stored as a **failed-attempt entry** under its own key, whether the model answered badly (`max_tokens`, `refusal`, `schema_fail`) or the call was lost (`timeout`, `error`, with what streamed); a hit raises the same `LLMFailure` without a call. Under `--replay` every recorded attempt replays as recorded: a lost call ends in the same failure, and a primary that failed before its declared fallback answered replays to the fallback's answer. On a normal run a failed-attempt entry replays only when a later attempt of the same call has a recorded answer, so the run follows its recorded path there for free; a call whose recorded attempts all failed is tried fresh from the first attempt, and that new try is paid (updated 2026-09-28). `--no-cache` skips reads; `--replay` allows cache hits only and fails on a miss before any network call.
- **$ cap:** before each call, code adds the worst case (estimated input + `max_tokens` output) to the stage's spend so far and stops cleanly if it would cross the cap (`needs-human.md`, exit 4). Stage caps sum to $49 per full app run; validation has its own $20. A failed attempt is charged too: an answered failure for its usage, an aborted stream for its snapshot's usage (output at least what it streamed), or only its estimated input if no message had started (nothing was generated, so a dropped connection can't trip the cap). Other API errors (5xx, connection) become `LLMFailure("error")`; streamed calls use `max_retries=0`, so every retry goes through the attempts loop and is charged.
- **Capability table** (`config/models.toml`): prices, `supports_effort`, streaming threshold. Haiku 4.5 never gets `effort` (it returns a 400).

## 9. Field names

These names are final: `Edge.from_state` / `Edge.to_state` (not `from` / `to`), `Check.passed` (not `pass`), and `Verdict` has no `judge` or `round` field: the judge stage stores each verdict under `judge/verdicts/<cand>_<judge>_r<round>.json`.
