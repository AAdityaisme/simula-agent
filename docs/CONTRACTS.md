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
| `mock/art.json` | `ArtFile` |
| `qa/round<N>/metrics.json` | `QAMetrics` |
| `qa/round<N>/critique.json` | `CritiqueFile` |
| `qa/round<N>/edits.json` | `EditsFile` |
| `qa/qa_report.json` | `QAReport` |
| `propose/lenses.json` | `LensesFile` |
| `propose/candidates.json` | `CandidatesFile` |
| `judge/decisions.json` | `DecisionsFile` |
| `<stage>/done.json` | `DoneMarker` |
| `manifest.json` | `Manifest` |
- `done.json` holds the hashes of every input, prompt, params, code, and output file. The code is the stage's module and every `simula` module it imports, transitively, read from the source (`runfolder.code_files`): a fix to propose's ranking reruns propose and the stages that import it (judge, flows), an `llm.py` fix reruns every stage that calls a model, and a README edit reruns nothing. `simula run` skips a stage only when all five still match; a marker written before the code was hashed never matches. Rerunning an upstream stage changes its outputs, so everything below it reruns. A dot-file in a folder a stage reads (Finder's `.DS_Store`) is OS metadata no loader reads, so it is neither hashed nor checked by the preflight. It is written to a temp file and renamed, so a crash never leaves a half marker. A live rerun removes a stage's old marker when the stage starts, and the new one is written after its `needs-human.md` entry, so a failure there leaves no marker and the next run redoes the stage. `--replay` keeps the old marker while the stage runs, so a stage that can't be replayed keeps the run's committed record; a stage that ran drops it before its records, as a live run does (added 2026-09-29). The manifest's `stages_done` and `usd_total` are read from the complete markers and the trace (at every command, and when a stage starts and finishes), so the manifest never lists a stage without a complete marker. A marker that can't be read, or that has a newer `failure.json` beside it (the stage failed after it last finished, the rule upstream stages are held to), counts as not done, in `simula run`'s check and in the manifest alike; an unreadable one gets a trace line naming it, so it never blocks the run that rewrites it.
- `done.json` also says what the stage delivered: `outcome {status: complete | partial, reasons, resume}`, written by `run_stage` from what the stage reports (a stage returns a `StageOutcome`; returning nothing means complete). The **$ cap** makes a stage partial on its own: when the cap turned work away (a refused call, or work the stage planned away for its cap; each writes a trace line with outcome `cap`, and a refused model call's line names its step and cache key), those notes become the reasons and the resume command raises `--usd-cap`. Every printed resume command (the runner's for a partial stage, a cap stop or an outage, and a stage's own request, built with `stages.rerun_command`) carries the run's options (`stages.run_options`): `--run`, `--profile`, `--budget`, the opt-ins, `--probe`, and an overridden `--usd-cap`, which a cap stop's line replaces with `<higher>`; `--no-cache` and `--replay` are modes of one command and stay out (added 2026-09-29). A partial stage adds a `needs-human.md` entry with the resume command (when a stage later completes without asking for a person during that run, its open requests get a `resolved` entry, whatever asked for them) and still feeds the stages below it (the deck shows its reasons, read from its `done.json`), but it never counts as done: it stays out of the manifest's `stages_done`, and `simula run` reruns it, its finished calls from the cache, until it completes. Under `--replay`, which can't complete it, a partial marker whose hashes all match is taken as recorded (added 2026-09-29). The cap itself is not hashed, since a cap that turned nothing away can't have changed the output.
- `failure.json` records why a stage failed. A live run has already removed the stage's `done.json`; under `--replay` the committed `done.json` stays, and the newer `failure.json` makes the stage not done and blocks the stages below it.
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
  - **Core-loop passes** (the explorer repeating the app's core action): `loop_pass` is 1, 2, … on each pass and null on every tour move. `change_summary` carries the pass's measurements as comma-separated `<what> <number> <unit>` parts, in any unit (stage 2 takes the units from that line, and never reads a line that says what changed on screen, `3 → 2` or `+'typed text'`, as one), e.g. `reply started 2.1 s, finished 9.4 s, 612 chars` or `no reply within 45 s`; a part without a number is ignored. `loop_stop` is set on the pass where a limit, paywall, or ad appeared, in a few words (`limit banner`, `paywall`, `interstitial ad`), and the loop stops there. Stage 2 turns the passes into `experience` ledger items (§6).
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
- **`ModelMeaning` is at the API's compiled-grammar limit** (measured 2026-09-29 on claude-opus-5-5): 45 properties across its objects are refused with "The compiled grammar is too large", 44 compile; descriptions don't count. `tests/test_schema_gate.py` pins 44, so a new field needs one out (or the call split in two). `app_name` went in and `QuestionDraft.id` came out.
- Fixed named fields wherever the keys are known: the judge's 5 gates and 6 judgment checks are properties, not a map.
- No `minLength`, `maximum`, or `pattern` (the API drops them). Code checks those after parsing.
- `CostInputs.currency_amount`: the USD price the app charges for exactly what the reward grants; 0 when no price was observed.
- `CandidateDraft.after_reward` (added in PR 5, Aadi-approved): one plain sentence on what the user sees when the reward runs out and why that moves them toward paying, returning, or watching again. The proposer's schema (`CandidateDraft`) requires it; the stored `Candidate` defaults it to `""` so older files parse, and propose drops a candidate that leaves it empty.
- `CandidateDraft.daily_cap` (added 2026-09-29, Aadi-approved): how many times one user can take the offer in a day, as an integer: the per-user limit only, never a per-character, per-item, or per-screen one (those stay in the `frequency_cap` prose). `rank()` reads reach as trigger-depth weight × `daily_cap`, never the prose; it replaces a regex over `frequency_cap` that read "1 per user per day; each character at most 10 times per day" as 10. An offer one user can take less than once a day (one-time, weekly, monthly, every few days) is `0`: it stays live, gets reach 0, and so ranks after every daily offer and loses a duplicate pair to a daily twin (red team PR4 @eddcdef #1; typing it 1 gave a weekly offer a daily offer's reach). The proposer's schema requires it; the stored `Candidate` defaults it to `0` so older files parse, and propose drops only a negative `daily_cap`.
- `CandidateDraft.for_users` and `CandidateDraft.grants_id` (added in PR 5, Aadi-approved): who is offered the reward (`free`, `paying`, or `everyone`), and the `value_ledger` id of the paid benefit the reward is a piece of, or more of (null for a new resource). Both are required. Propose drops an idea whose `grants_id` isn't a ledger id, and more of a bullet for payers when the bullet has no number and no observed limit cites it; it dedupes on (`grants_id`, `for_users`).
- `ModelMeaning.terms` and `ModelMeaning.open_questions` are required in the model's answer (it may return `[]`); `ModelMeaning` is never stored, so no file changes. The stored `ProductModel.terms` and `ProductModel.questions` default to `[]`.
- `ModelMeaning.app_name` (added 2026-09-29): the app's name as its own screens show it, with its capitalization, for people to read; empty when no screen shows it. Required in the model's answer; the stored `ProductModel.app_name` defaults to `""` so older files parse. Anything that shows the app's name to people (the deck's cover, the product model's title) uses `app_name`, falling back to `app`, the config key.
- **Every check of a model's words against app text uses `simula/text.py`**: `text.find(quote, app_text, spaced=True, ignore_case=False)` returns the app's own span or None (any whitespace run matches any other; `spaced=False` ignores whitespace, for numbers), and `text.same(a, b)` compares with whitespace normalized. App trees carry non-breaking spaces (`$\xa01.99`) that a model types back as plain spaces.

## 6. Who writes which field

Code writes every number and id; a model writes meaning keyed by ids code gave it; code validates the merge.

| Object | Code writes | A model writes |
|---|---|---|
| ProductModel | app (the config key), app_version, run_id, device, coverage, provenance, edges, the `experience` ledger items and the `pb` paywall bullets it quotes, `open_questions` (the text of `questions`) | app_name, app_category, flows, mechanics, cross_screen_values, value_ledger (except `experience` and `pb`), terms, questions |
| State | id, kind, parent_id, fingerprint, canonical_png, elements, in_mock_scope, dynamic_regions (**device px**, like `rect_px`; QA converts them to content dp), blocked_reason | name, purpose, content_rating |
| Element | id, mcp_ref, type, text, label (the tree's, without the placeholder characters U+FFFC and U+FFFD, via `text.strip_placeholders`), source, rects, asset_png, colors, font_px, in_mock, repeat_group | role, font_guess |
| Edge | everything, including `transition` (closing a modal → back, opening a modal or sheet → modal, a tab tap → tab, BACK → back, root replaced → replace, else push) and `change_summary`: the explorer's, or else, for a move whose two captures sit right around it (it first captured its to-state, and the screen before it still matched the from-state's capture), the values that changed in one spot between the captures when they show the same screen (most texts without a number are the same text in the same spot, so two lists that share a layout are not one screen) (`7 chats → 8 chats`: same role, left edge, top and height, same words, a different number; words that occur more than once on either capture, like a list's `N chats` rows that can re-sort, a bare number such as `171` or `1 / 295`, and a clock time, are skipped). A state is captured once, so any other move's captures may show changes it didn't make (added 2026-09-29) | nothing |
| Flow | validates that every edge id exists and the hops connect | id, name, purpose, edge_ids, evidence_ids |
| Evidence ids (mechanics, ledger, values, flows) | each must be a recorded state, element, or edge id; a paywall, limit, or currency must cite an element | the ids |
| LedgerItem | checks `verbatim` matches its evidence element's text or label (via `simula/text.py`) and stores the element's exact span. Writes every `experience` item: `verbatim` is then the measured fact (median, min, max, n over the core-loop passes, or "(1 measurement)" when there is one; or what stopped the loop, or "After N passes … nothing limited it", which says the account's plan wasn't recorded), **not tree text**, and `evidence_ids` are the edges of those passes. A model may not write `experience` items. Every paywall bullet gets an item: code quotes whole each text item of a kept `paywall_bullet`'s repeated list (same `repeat_group`, same left edge) that no item cites yet and whose words no paywall bullet, and no item citing such a list, already quotes (a paywall captured twice lists each bullet twice), since the model sees only two items of a repeated list (ids `pb1`, `pb2`, …; added 2026-09-29) | everything else |
| Term | `defined_by` keeps the cited elements that show the term's meaning. An **anchor** is app text that carries the term (as a whole word) and, with the term cut out, still holds a word of two or more letters (a letter counts with the marks written on it, such as an Indic vowel sign), so a bare name or a count ("1.8k tokens") is never one. A screen is explained when a cited element on it is an anchor, or when a recorded tap on an anchor, cited or not, opened it ("Upgrade to <term>" opening the plan's benefit list; a tap that changed its own screen opens nothing). On an explained screen, a cited element that carries the term counts only if it is an anchor itself, and one that doesn't (the bullets under a plan's name) counts when it has any word character. So `defined_by` holds only the model's own citations and may name none of the anchors; `anchor_taps` lists the recorded taps it relied on, and the model stage writes a `term_tap` trace line for each such term. Only on-screen text decides: whether the model also quoted an element in the ledger doesn't matter, and neither does what a mechanic cites as evidence. A label a model wrote (an explore icon-pass name, a vision-pass element) is never app text, so it neither shows the term nor explains it. `observed` is true when any are left; otherwise `meaning` becomes "meaning not observed" and **no idea may rest on the term**: an idea that uses it stays live with a flag for a person reviewing the output (Stage 5), and the judge never sees the flag. Known limits: a call to action (`Unlock <term>`), a role word in an app's own label (`<term> tab`) or a sentence that only uses the term (`monthly <term> with our models`) reads as an explanation, a tap on an element that names the term only in passing (a list row `..., 7 <term>`) carries it to whatever screen that tap opened, and a price on a plan card doesn't (a "Weekly" plan next to "$1.99" stays unobserved); which cited text explains the term stays the model's reading. Rejects a term that no kept mechanic or ledger line in `used_in` uses as a whole word. An `everyday` term (its plain-English meaning is what it means in the app) keeps its `observed` value, but no idea is flagged for it and the proposer isn't told to avoid it; the model must answer `everyday`, and only a stored product model defaults it to false, so one written before the field existed still parses and flags as before | term, meaning, defined_by, used_in, everyday |
| OpenQuestion | `id` (`q1`, `q2`, … in the model's order; the model writes no id, since 2026-09-29); rejects a `start_state` that isn't a recorded state; keeps at most 5 (the model orders them, most monetization-relevant first); `answered` starts false and is set by the targeted explore pass (a later PR) | question, start_state, look_for |
| Candidate | id (`c01`, `c02`, … in draft order), the bucket prefix in `title` (`Existing opportunity: ` or `Product change: `), economics, reach_score, rank_score, dropped_reason, flags (concerns code raises that don't drop the idea, one readable sentence each, e.g. `uses "Pro", whose meaning was never observed`, checked against every field the slides print; for a person reviewing the output, never the judge). The economics cost term comes from `reward.kind`; `app_category` only breaks ties | everything else in CandidateDraft |
| Verdict | nothing | every check, `other_concern`, `fixable` |
| Decision | everything. `checks_total` = the 11 LLM-judged checks (5 gates + 6 judgment checks); `checks_passed` counts a check only when every judge that ran passed it | nothing |

## 7. Mock contract (data attributes)

- One static `mock/index.html`, inline CSS/JS, no build step. Images only from `assets/<element_id>.png`. Google Fonts allowed; every other request is blocked.
- Art: `assets/<element_id>.art.png` is the real picture painted inside that element, cropped from its screenshot by code. `mock/art.json` = `{"schema_version": 1, "art": {"assets/<element_id>.art.png": <content-dp rect it was cropped from>}}` lists every one, so QA can mask it.
- `<section data-screen="s03">` per in-scope state. Modals: `data-screen="s07" data-parent="s03"`, drawn as an overlay.
- `data-el="s03.e07"` on each `in_mock` element; only the `data-el` tag is limited to the first 2 items of a repeated list. `in_mock` means "the mock draws this element", and every element that starts an edge is `in_mock`. A modal or sheet's copy of an element its parent's layer draws (same class, words, and box) is not `in_mock`: the parent's layer draws it once, under the backdrop (added 2026-09-29). The runtime shows one parent layer, so a dialog over a dialog still draws the screen under both itself.
- `data-edge="s03.e07>s05"` on each tappable element, plus **`data-transition="push|modal|tab|back|replace|unknown"`** copied from the edge. A click shows the edge's `to_state` screen with that transition: push slides, modal fades in an overlay, tab switches instantly; `prefers-reduced-motion` turns animation off. `unknown` renders as `push`, and QA skips the transition check for it.
- `data-chrome="header|tabbar"` on shared chrome (one shared DOM fragment). `data-value="<cross_screen_value id>"` on shared values.
- `window.simula.go(id)`, `window.simula.state()`, `window.simula.reset()`.
- No screenshot wallpaper: no single `<img>` may cover more than 40% of a screen's content area.
- Flow screens added in stage 7: `data-screen="new:<slug>"`, `data-flow="<candidate id>"`.
- Scope is chosen by code (stage 2): the root, every state showing a paywall, limit, currency, or ad, every state showing any other mechanic (a modal or sheet brings every layer under it, down to the screen), and every state on the core flows; no fixed cap; a tab or depth-1 screen is in only when it is on a flow or holds a mechanic; never `blocked` or `external`. An `unsafe` state stays out unless it is on a core flow or is a layer under a flow's modal or sheet, where a gap would break the flow (decided 2026-09-28, replacing "unsafe never removes a state"; the layers under a flow's dialog added 2026-09-29). The mock draws exactly this scope, an unsafe layer as it looks (nothing blurs it), and propose never triggers an offer on an unsafe screen. `ProductModel.mock_order` lists the scope in that priority order (added 2026-09-28).

## 8. Trace, cache, money

- **Trace line:** `{ts, stage, step, decider: code|jev|model|human, model, effort, confidence, tokens_in, tokens_out, tokens_cached, usd, cache_hit, outcome, note}`. `confidence` is null unless the decider returns one (Jev does). Build spend and hand fixes go to `build/trace.jsonl` through `simula note`.
- **Cache** (`cache/<sha256>.json`): the key covers provider, model, system prompt, messages (image bytes replaced by their sha256), effort, max_tokens, the JSON schema, and the attempt number. Only answers that pass validation are stored as answers, and a retry bumps the attempt, so a bad answer is never replayed as an answer. Every try is stored and an entry is never overwritten: a new try of the same attempt goes to the next file (`sha256("<key>:<n>")`). An entry that can't be read (not JSON, or not a reply) is skipped with a trace line, in a normal run and under `--no-cache` alike, and never overwritten: the next try goes after it. Under `--replay`, an unreadable entry the run's own trace names is a miss, never a fallback to another run's try (added 2026-09-29). A try the model answered but that failed (`max_tokens`, `refusal`, `schema_fail`) is a **failed-attempt entry** that always replays: under `--replay` and on a normal rerun alike, a hit raises the same `LLMFailure` with no call, so a rerun never pays for a known failure again, and a new try needs a changed input (effort, max_tokens, the prompt), which is a new key. A lost call (`timeout`, `error`, with what streamed) is stored only so `--replay` can follow the recorded path; a normal run ignores it and tries that attempt again, because it is transient. A normal run takes each attempt's latest answered try; `--replay` takes the try the run's own trace names last (every model trace line names its cache file as `key <first 12 hex>`), so a run replays its own path even after another run tried the same call again (updated 2026-09-29). A `ProviderUnavailable` (the provider refusing the account) is never stored, not even for `--replay`: an outage isn't a model answer, so a replay of that run misses there and a rerun calls again (added 2026-09-29). `--no-cache` skips reads and stores its try as the attempt's latest; `--replay` allows cache hits only and fails on a miss before any network call. A call the live run's $ cap turned away was never made, so nothing is cached for it: its trace line (outcome `cap`, `key <first 12 hex>` of the call) is the record, and `--replay` raises the same `CapReached` there, with the recorded message and whatever the replay's own cap, so a stage that caught its cap replays to the same partial output. If a later live run in the same run folder answered that call, the later record wins (added 2026-09-29).
- **$ cap:** before each call, code adds the worst case (estimated input + `max_tokens` output) to the stage's spend so far and stops cleanly if it would cross the cap (`needs-human.md`, exit 4), with one trace line (outcome `cap`) naming the refused call's step and cache key. A stage that catches the stop and ships what it has finishes partial (see `done.json` above). Stage caps sum to $49 per full app run; validation has its own $20. Only that cap is a `CapReached`. A provider refusing the account (a usage, spend, quota or billing limit) is an outage, not our cap: `ProviderUnavailable`, never cached; the stage fails with no `done.json`, `needs-human.md` gives the command to resume from that stage, and the CLI exits 5. A failed attempt is charged too: an answered failure for its usage, an aborted stream for its worst case, the input it reported plus `max_tokens` of output (or what it streamed, if more), because the API bills thinking a stream may not carry, or only its estimated input if no message had started (nothing was generated, so a dropped connection can't trip the cap). Other API errors (5xx, connection) become `LLMFailure("error")`, and so does any error raised while a stream is read that the SDK leaves untyped (its event accumulator raises a plain `RuntimeError`, `TypeError` or `IndexError`), since it comes from provider data; any other non-SDK exception is a bug in our code and is raised as itself, not retried; streamed calls use `max_retries=0`, so every retry goes through the attempts loop and is charged.
- **Capability table** (`config/models.toml`): prices, `supports_effort`, streaming threshold. Each role's `max_tokens` lives in `config/profiles.toml` (the widest call, for a role with several); the stage code and `doctor --keys` both read it, so doctor probes each model at the budget the code asks for and streams exactly when the call does (added 2026-09-29). Haiku 4.5 never gets `effort` (it returns a 400).

## 9. Field names

These names are final: `Edge.from_state` / `Edge.to_state` (not `from` / `to`), `Check.passed` (not `pass`), and `Verdict` has no `judge` or `round` field: the judge stage stores each verdict under `judge/verdicts/<cand>_<judge>_r<round>.json`.
