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

## 4. Provenance: fixtures are test data only

- `tests/fixtures/` and the golden models build and unit-test stages. They never produce a deliverable.
- Every `done.json` and the manifest carry `provenance {source: explorer_run | fixture}`. Any fixture upstream makes the output a fixture.
- Every stage refuses fixture input without `--allow-fixtures`. With it, the run id ends in `-fixture`, exhibits carry a banner, and slides a watermark.
- `--fixture STAGE=PATH` is **the only way** a fixture enters a run, and only into a run the same call creates: `simula run APP --new --allow-fixtures --fixture STAGE=PATH`. Seeding an existing run is refused (exit 2) and leaves it untouched.

## 5. Schemas a model fills

`MODEL_FACING` in `contracts.py`: `ModelMeaning`, `IconPass`, `HardScreenAction`, `Critique`, `Edits`, `LensOutput`, `Verdict`, `PairwisePick`.

- No `dict` fields and no recursion. The Anthropic SDK turns a dict field into an object that can only be `{}`, and pydantic still accepts it, so a dict-shaped verdict would pass everything. `tests/test_schema_gate.py` checks both SDKs.
- At most 4 levels of nesting; `extra="forbid"`.
- Fixed named fields wherever the keys are known: the judge's 5 gates and 6 judgment checks are properties, not a map.
- No `minLength`, `maximum`, or `pattern` (the API drops them). Code checks those after parsing.

## 6. Who writes which field

Code writes every number and id; a model writes meaning keyed by ids code gave it; code validates the merge.

| Object | Code writes | A model writes |
|---|---|---|
| ProductModel | app, app_version, run_id, device, coverage, provenance, edges | app_category, flows, mechanics, cross_screen_values, value_ledger, open_questions |
| State | id, kind, parent_id, fingerprint, canonical_png, elements, in_mock_scope, dynamic_regions (**device px**, like `rect_px`; QA converts them to content dp), blocked_reason | name, purpose, content_rating |
| Element | id, mcp_ref, type, text, label, source, rects, asset_png, colors, font_px, in_mock, repeat_group | role, font_guess |
| Edge | everything, including `transition` (closing a modal → back, opening a modal or sheet → modal, a tab tap → tab, BACK → back, root replaced → replace, else push) | nothing |
| Flow | validates that every edge id exists and the hops connect | id, name, purpose, edge_ids, evidence_ids |
| LedgerItem | checks `verbatim` string-matches its evidence element's text | everything else |
| Candidate | economics, reach_score, rank_score, dropped_reason. The economics cost term comes from `reward.kind`; `app_category` only breaks ties | everything in CandidateDraft |
| Verdict | nothing | every check, `other_concern`, `fixable` |
| Decision | everything. `checks_total` = the 11 LLM-judged checks (5 gates + 6 judgment checks); `checks_passed` counts a check only when every judge that ran passed it | nothing |

## 7. Mock contract (data attributes)

- One static `mock/index.html`, inline CSS/JS, no build step. Images only from `assets/<element_id>.png`. Google Fonts allowed; every other request is blocked.
- `<section data-screen="s03">` per in-scope state. Modals: `data-screen="s07" data-parent="s03"`, drawn as an overlay.
- `data-el="s03.e07"` on each `in_mock` element (first 2 items of a repeated list only).
- `data-edge="s03.e07>s05"` on each tappable element, plus **`data-transition="push|modal|tab|back|replace|unknown"`** copied from the edge. A click shows the edge's `to_state` screen with that transition: push slides, modal fades in an overlay, tab switches instantly; `prefers-reduced-motion` turns animation off. `unknown` renders as `push`, and QA skips the transition check for it.
- `data-chrome="header|tabbar"` on shared chrome (one shared DOM fragment). `data-value="<cross_screen_value id>"` on shared values.
- `window.simula.go(id)`, `window.simula.state()`, `window.simula.reset()`.
- No screenshot wallpaper: no single `<img>` may cover more than 40% of a screen's content area.
- Flow screens added in stage 7: `data-screen="new:<slug>"`, `data-flow="<candidate id>"`.
- Scope is chosen by code: root, tabs, depth-1 states, every state with a mechanic; at most 8; never `unsafe` or `blocked`.

## 8. Trace, cache, money

- **Trace line:** `{ts, stage, step, decider: code|jev|model|human, model, effort, confidence, tokens_in, tokens_out, tokens_cached, usd, cache_hit, outcome, note}`. `confidence` is null unless the decider returns one (Jev does). Build spend and hand fixes go to `build/trace.jsonl` through `simula note`.
- **Cache** (`cache/<sha256>.json`): the key covers provider, model, system prompt, messages (image bytes replaced by their sha256), effort, max_tokens, the JSON schema, and the attempt number. Only answers that pass validation are stored, and a retry bumps the attempt, so a bad answer is never replayed. `--no-cache` skips reads; `--replay` allows cache hits only and fails on a miss before any network call.
- **$ cap:** before each call, code adds the worst case (estimated input + `max_tokens` output) to the stage's spend so far and stops cleanly if it would cross the cap (`needs-human.md`, exit 4). Stage caps sum to $49 per full app run; validation has its own $20.
- **Capability table** (`config/models.toml`): prices, `supports_effort`, streaming threshold. Haiku 4.5 never gets `effort` (it returns a 400).

## 9. Field names

These names are final: `Edge.from_state` / `Edge.to_state` (not `from` / `to`), `Check.passed` (not `pass`), and `Verdict` has no `judge` or `round` field: the judge stage stores each verdict under `judge/verdicts/<cand>_<judge>_r<round>.json`.
