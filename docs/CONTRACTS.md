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
- Edge ids: `<element id>><to_state>` when a listed or vision element was tapped, else `<from_state>.<action>><to_state>` (e.g. `s03.back>s01`). The first recorded move between the same element and target wins. Edges with `element_id: null` have no `data-el` to hang on: the mock's runtime performs each swipe, back and type edge from its gesture (§7), and a `back` edge also goes on the screen's back control when one is drawn.

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
- `CandidateDraft.after_reward` (added in PR 5, Aadi-approved): one plain sentence on what the user sees when the reward runs out and why that moves them toward paying, returning, or watching again. The proposer's schema (`CandidateDraft`) requires it; the stored `Candidate` defaults it to `""` so older files parse, and propose drops a candidate that leaves it empty.
- `CandidateDraft.daily_cap` (added 2026-09-29, Aadi-approved): how many times one user can take the offer in a day, as an integer: the per-user limit only, never a per-character, per-item, or per-screen one (those stay in the `frequency_cap` prose). `rank()` reads reach as trigger-depth weight × `daily_cap`, never the prose; it replaces a regex over `frequency_cap` that read "1 per user per day; each character at most 10 times per day" as 10. An offer one user can take less than once a day (one-time, weekly, monthly, every few days) is `0`: it stays live, gets reach 0, and so ranks after every daily offer and loses a duplicate pair to a daily twin (red team PR4 @eddcdef #1; typing it 1 gave a weekly offer a daily offer's reach). The proposer's schema requires it; the stored `Candidate` defaults it to `0` so older files parse, and propose drops only a negative `daily_cap`.
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
| Term | `observed`: true only when a `defined_by` element's own text or label carries the term and that element is not the evidence of a `used_in` line (a bullet can't define itself); otherwise `meaning` becomes "meaning not observed", `defined_by` is emptied, and **no idea may rest on the term**: an idea that uses it stays live with a flag for a person reviewing the output (Stage 5), and the judge never sees the flag. Known limit: the check only proves the word was on a cited screen; the meaning stays the model's reading. Rejects a term that no kept mechanic or ledger line in `used_in` uses. An `everyday` term (its plain-English meaning is what it means in the app) keeps its `observed` value, but no idea is flagged for it and the proposer isn't told to avoid it; `everyday` defaults to false, so a product model without it flags as before | term, meaning, defined_by, used_in, everyday |
| OpenQuestion | rejects a `start_state` that isn't a recorded state; keeps at most 5 (the model orders them, most monetization-relevant first); `answered` starts false and is set by the targeted explore pass (a later PR) | id, question, start_state, look_for |
| Candidate | id (`c01`, `c02`, … in draft order), the bucket prefix in `title` (`Existing opportunity: ` or `Product change: `), economics, reach_score, rank_score, dropped_reason, flags (concerns code raises that don't drop the idea, one readable sentence each, e.g. `uses "Pro", whose meaning was never observed`, checked against every field the slides print; for a person reviewing the output, never the judge). The economics cost term comes from `reward.kind`; `app_category` only breaks ties | everything else in CandidateDraft |
| Verdict | nothing | every check, `other_concern`, `fixable` |
| Decision | everything. `checks_total` = the 11 LLM-judged checks (5 gates + 6 judgment checks); `checks_passed` counts a check only when every judge that ran passed it | nothing |

## 7. Mock contract (data attributes)

- One static `mock/index.html`, inline CSS/JS, no build step. Images only from `assets/<element_id>.png`. Fonts only from `assets/fonts/`, which code copies from Google Fonts when it builds the mock; code also writes their `@font-face` rules and links them, so a builder or fixer names a family only in `font-family` and never writes `@font-face` or a font file path. Every request outside the mock's own folder, Google Fonts included, is blocked. Code also writes `assets/fonts/LICENSE.txt`, each copied family's license text (SIL OFL, Apache 2.0 or the Ubuntu Font Licence, from Google's font repository); a family whose license can't be found isn't copied.
- Art: `assets/<element_id>.art.png` is the real picture painted inside that element, cropped from its screenshot by code. `mock/art.json` = `{"schema_version": 1, "art": {"assets/<element_id>.art.png": <content-dp rect it was cropped from>}}` lists every one, so QA can mask it. Art search looks inside every element with no asset and no text of its own, drawn or not, and a crop avoids every other element's words and images, so it may be any size.
- `<section data-screen="s03">` per in-scope state. Modals: `data-screen="s07" data-parent="s03"`, drawn as an overlay.
- `data-el="s03.e07"` on each `in_mock` element; only the `data-el` tag is limited to the first 2 items of a repeated list. `in_mock` means "the mock draws this element", and every element that starts an edge is `in_mock`.
- `data-edge="s03.e07>s05"` on each tappable element, plus **`data-transition="push|modal|tab|back|replace|unknown"`** copied from the edge. A click shows the edge's `to_state` screen with that transition: push slides, modal fades in an overlay, tab switches instantly; `prefers-reduced-motion` turns animation off. `unknown` renders as `push`, and QA skips the transition check for it.
- **Edges with no element.** Code writes `<script id="simula-actions" type="application/json">` into the page: `{screen: {swipe|back|type: [to_state, transition, text field element id or null]}}`, the in-scope edges that start on no element, the first per action and screen. The runtime performs them: a drag of 48 px or more on the screen is its swipe, or its back when the drag starts within 24 px of the left edge and runs right; Escape is its back; the screen's first tagged `EditText` takes typing, and Enter in it performs the type edge. A `back` edge also goes on a drawn back control as `data-edge`.
- `data-chrome="header|tabbar"` on shared chrome (one shared DOM fragment). `data-value="<cross_screen_value id>"` on shared values.
- `window.simula.go(id)`, `window.simula.state()`, `window.simula.reset()`.
- No screenshot wallpaper: no single `<img>` may cover more than 40% of a screen's content area, unless it is a code-made crop that holds no listed interface (an art crop, or an element asset with no other element's words or image inside it) drawn within 4 dp of the rect it was cut from, on that rect's own screen. The model crops an element asset only under 40% of the content area; a bigger picture is left to art search.
- Flow screens added in stage 7: `data-screen="new:<slug>"`, `data-flow="<candidate id>"`.
- **Batches.** Stage 3 draws the scope in batches of consecutive screens in `mock_order` (a modal or sheet stays in its parent's batch), one model call per batch, and code joins them into the one page. Code writes `<style id="simula-shared">` once: a `:root` block of the in-scope elements' most used fills, text colors and fonts (`--bg-N`, `--fg-N`, `--font-N`) and the body's background and font; an edit keeps it intact. Each batch's own CSS sits in `<style data-batch="<n>">`, and every selector in it that is not nested in another rule starts with one of its own screens' `section[data-screen="<id>"]` or an `:is(…)`/`:where(…)` list of them.
- **Contract errors code adds** besides the validator's: `undrawn_screen` (the screen's batch failed after its retry or met the $ cap; its section is a placeholder reading `screen not drawn: <reason>`), `unscoped_css` (a batch selector that breaks the scoping rule above), and `foreign_screen` (a batch returned a section for a screen outside its batch). The last two carry the batch's first screen as `screen`.
- Scope is chosen by code (stage 2): the root, every state showing a paywall, limit, currency, or ad, every state showing any other mechanic (a modal or sheet brings its parent), and every state on the core flows; no fixed cap; a tab or depth-1 screen is in only when it is on a flow or holds a mechanic; never `blocked` or `external`. An `unsafe` state stays out unless it is on a core flow, where a gap would break the flow (decided 2026-09-28, replacing "unsafe never removes a state"). `ProductModel.mock_order` lists the scope in that priority order (added 2026-09-28).

## 8. Trace, cache, money

- **Trace line:** `{ts, stage, step, decider: code|jev|model|human, model, effort, confidence, tokens_in, tokens_out, tokens_cached, usd, cache_hit, outcome, note}`. `confidence` is null unless the decider returns one (Jev does). Build spend and hand fixes go to `build/trace.jsonl` through `simula note`.
- **Cache** (`cache/<sha256>.json`): the key covers provider, model, system prompt, messages (image bytes replaced by their sha256), effort, max_tokens, the JSON schema, and the attempt number. Only answers that pass validation are stored as answers, and a retry bumps the attempt, so a bad answer is never replayed as an answer. An attempt the model answered but that failed (`max_tokens`, `refusal`, `schema_fail`) is stored as a **failed-attempt entry** under its own key: a hit raises the same `LLMFailure` without a call, so `--replay` follows the recorded path to the next attempt or the caller's own retry, and a rerun doesn't pay for a known failure again. Timeouts and connection errors are never stored. `--no-cache` skips reads; `--replay` allows cache hits only and fails on a miss before any network call. The mock's font fetches are recorded too, in the run's own `mock/font-records/<sha256 of the URL>.json` (the bytes, or the error a live build got): a live build fetches and records, and `--replay` builds the fonts from those records only, so it rebuilds the same `mock/assets/fonts/` offline; a missing record is a replay miss. The records sit in the run, not in `cache/`, because a URL is not a request's full content: the same URL can answer a later build differently (a failed fetch, a new font version), and that must not change what an earlier run replays.
- **$ cap:** before each call, code adds the worst case (estimated input + `max_tokens` output) to the stage's spend so far and stops cleanly if it would cross the cap (`needs-human.md`, exit 4). Stage caps sum to $66 per full app run (mock $25); validation has its own $20. The mock plans its batches before the first call: in `mock_order` priority, it keeps batches while their worst cases plus one spare worst case (for a `max_tokens` retry) fit what is left of the stage cap (after `--usd-cap`) once the mock spend already in the run is taken off, and stops at the first that doesn't, so the $ check never turns a planned first call away on a rerun either. The rest become `undrawn_screen` placeholders decided up front. A live run records the plan in `mock/plan.json` (`keep`, `cap`, `spent`), and `--replay` reads that record instead of planning again (its trace also holds the live run's spend), so it draws the same batches whatever `--usd-cap` it is given; with no record it is a replay miss. The plan is the trace's `mock/plan` line, and the mock exhibit shows it with each batch's worst case. Only that cap is a `CapReached`. A provider refusing the account (a usage, spend, quota or billing limit) is an outage, not our cap: `ProviderUnavailable`, never cached; the stage fails with no `done.json`, `needs-human.md` gives the command to resume from that stage, and the CLI exits 5. A failed attempt is charged too: an answered failure for its usage, an aborted stream for its worst case, the input it reported plus `max_tokens` of output (or what it streamed, if more), because the API bills thinking a stream may not carry, or only its estimated input if no message had started (nothing was generated, so a dropped connection can't trip the cap). Other API errors (5xx, connection) become `LLMFailure("error")`, and so does any error raised while a stream is read that the SDK leaves untyped (its event accumulator raises a plain `RuntimeError`, `TypeError` or `IndexError`), since it comes from provider data; any other non-SDK exception is a bug in our code and is raised as itself, not retried; streamed calls use `max_retries=0`, so every retry goes through the attempts loop and is charged.
- **Capability table** (`config/models.toml`): prices, `supports_effort`, streaming threshold. Haiku 4.5 never gets `effort` (it returns a 400).

## 9. Field names

These names are final: `Edge.from_state` / `Edge.to_state` (not `from` / `to`), `Check.passed` (not `pass`), and `Verdict` has no `judge` or `round` field: the judge stage stores each verdict under `judge/verdicts/<cand>_<judge>_r<round>.json`.
