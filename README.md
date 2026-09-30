# simula-agent

Mobile app → product model → clickable mock, measured against the real screens → reviewed rewarded-ad proposals. This is Simula take-home #2; the assignment, verbatim, is in [docs/ASSIGNMENT.md](docs/ASSIGNMENT.md), and every file format is in [docs/CONTRACTS.md](docs/CONTRACTS.md).

## Start here

simula-agent takes an installed Android app through exploration, a product model, a clickable mock and reviewed rewarded-ad flows, with no per-app code. <RUN: one sentence on the outcome>.

- **Recording** (10–15 min): <RECORDING>
- **JanitorAI, the deep run:** [deck](runs/janitorai/20260929-203310-1f19585/flows/slides.pdf) · [clickable mock](runs/janitorai/20260929-203310-1f19585/qa/approved/index.html) (open it locally; GitHub shows HTML as source)
- **Coverage:** <RUN: which apps ran and how far>. The main limitation: neither judge rubric met its bar, so an accepted idea is two models' call, and a split waits for a person.
- **Evidence:** [Results](#results) · [Replay without keys or a device](#replay-without-keys-or-a-device)

## Results

Every run below ran on commit `1f19585` and is committed with its cache. Each row says how far that app got; a replay covers the stages its run finished (see Replay).

| App | Why it's here | Budget | Run | Outcome | Screens | Ideas: proposed → accepted / split / rejected | API $ | Wall time | Judge-2 fallback used |
|---|---|---|---|---|---|---|---|---|---|
| JanitorAI | the deep run | `deep` | `runs/janitorai/20260929-203310-1f19585/` | All 7 stages done on a partial explore (it used its 3 relaunches and stopped at 41 of 80 actions); deck draws the 2 accepted ideas, 2 split ideas wait on Needs your call. QA approved: 488 of 488 tagged elements within 4 dp, 14 of 14 taps land, 6 of 6 core flows walk | 20 (explore partial, relaunch limit) | 8 → 2 / 2 / 4 (all 4 dropped by code: 3 duplicates, 1 claiming a payer benefit never observed; both accepted ideas passed after one revision) | $15.15 | 50 min (explore 13) | no |
| Luzia | checks the system transfers | `transfer` | `runs/luzia/20260929-204554-1f19585/` | All 7 stages done; deck draws 3 ideas. QA partial: 198 of 198 tagged elements within 4 dp and 19 of 19 taps land, but 1 of 6 core flows fails (the mock's chat screen has no text field) | 15 (explore complete, 40-action cap) | 10 → 3 / 2 / 5 (one of the 5 a duplicate code dropped; 3 ideas revised once) | $10.69 | 30 min (explore 7) | no |
| AOL | checks the system transfers | `transfer` | `runs/aol/<RUN>/` | <RUN> | <RUN> | <RUN> | <RUN> | <RUN> | <RUN> |
| OOC | checks the system transfers | `transfer` | `runs/ooc/20260929-212755-1f19585/` | Blocked by the app: OOC showed its own "Emulator Detected" dialog ("Not allowed to launch the App on android emulator, app will get closed.") and closed itself, so explore stopped with 1 screen and 0 actions. No stages 2–7, since there was nothing of OOC to model. OOC needs a real device. *(Corrected 2026-09-29 21:55: an earlier version of this row said the phone showed the previous app and that the record didn't show the cause. The captured screen, `explore/states/s01`, is OOC's own dialog.)* | 1 (OOC's "Emulator Detected" dialog) | — | $0.00 | 15 s | — |
| Perplexity | extra transfer evidence, outside the test set: nothing was built or tuned against it | `transfer` | `runs/perplexity/20260929-212810-1f19585/` | All 7 stages done on a partial explore (it used its 3 relaunches, and its core-loop pass found no core action). Nothing was accepted, so the deck draws the top split, c01, labelled as the closest idea and not a recommendation; the other 2 splits wait on Needs your call. QA approved: 91 of 91 tagged elements within 4 dp, 2 of 2 taps land, 4 of 5 core flows walk | 13 (explore partial, relaunch limit) | 4 → 0 / 3 / 1 (the reject depends on a busy queue the explore never saw, so both judges failed its evidence) | $4.72 | 27 min (explore 10) | no |

"Split" counts ideas the judges split on: they wait on the deck's "Needs your call" page. "Judge-2 fallback used" says whether the run's trace has a `declared fallback used` line, meaning that judge-2 call ran on gpt-6-luna, which the validation never measured.

**OOC** runs with the same command as every other app. It showed its own "Emulator Detected" dialog and closed itself; the run records that and moved on. The fix is a real device (the device pool in [docs/GOAL5.md](docs/GOAL5.md)), not a workaround in this system.

**Hand fixes.** A person stepping in during a run is a `hand_fix` line, decided by `human`, in that run's `trace.jsonl` (`simula note --run ID`). Tonight's runs: none; no committed trace has a `hand_fix` line. Outside the committed runs, two earlier JanitorAI attempts are not committed: the first failed after its tour when a screenshot timed out under heavy host load, and the emulator then crashed; the second was stopped at once because the restored emulator had JanitorAI logged out. Aadi logged JanitorAI back in by hand, and the committed run started after that.

## What it does

Given an Android app on an emulator, the pipeline explores the app and writes a product model of what it found: screens, flows, and money mechanics such as paywalls, limits, currencies, and ads. It draws the relevant screens as a clickable HTML mock and repairs the mock against the real screenshots. It then proposes rewarded-ad ideas that fit the app's own economy, has blind judges review them, and lays the ones it draws out as slides for the app's product team. The rewarded interaction is always a short sponsored game, because that is Simula's ad unit (a playable); what the system varies from idea to idea is the moment, the placement and the reward. Code records every fact and makes every decision it can check; model calls read and interpret, and code checks what it can of their answers against what was captured.

| Stage | Purpose |
|---|---|
| `explore` | Drives the app through mobile-mcp and records every distinct screen, the tap graph, full-size screenshots, and element trees. It then repeats the app's core action and measures the free experience. |
| `model` | Turns the capture into `product_model.json`. Code writes the facts, and one model call adds meaning (names, purposes, flows, mechanics, content ratings) keyed to them. Code checks cited IDs, quoted UI text, and connected edges; models interpret meaning, and that interpretation can be wrong. |
| `mock` | Draws the screens in scope as one clickable HTML page, in parallel batches, reusing pictures cropped from the screenshots. |
| `qa` | Scores each mock screen against its screenshot on element bounds, navigation, and masked SSIM. A critic explains the gaps, a fixer edits the page, and the best round goes to `qa/approved/`. Structure, taps and SSIM are reported separately, with no pass mark. |
| `propose` | One model call per lens proposes rewarded-ad ideas. Code checks each idea against the product model, adds a cost line, and ranks the ideas. |
| `judge` | Blind judges score every idea on gates and judgment checks. Code turns their verdicts into accept, reject, conditional (the judges split; it waits on Needs your call), or needs-human, and a fixable reject gets one revision. |
| `flows` | Adds each drawn idea's new screens (at most 4 ideas) to a copy of the approved mock, taps through every step in Playwright, and writes `slides.html` and `slides.pdf`. |

## Replay without keys or a device

Every committed run replays from a clean clone: no API keys, no emulator, and no model calls. You still need Chromium, because mock, qa and flows render their pages when they run.

```sh
git clone https://github.com/AAdityaisme/simula-agent && cd simula-agent   # on a Mac, clone outside ~/Desktop (iCloud)
uv sync
uv run playwright install chromium
uv run simula run janitorai --run 20260929-203310-1f19585 --budget deep --from model --replay
uv run simula run luzia --run 20260929-204554-1f19585 --from model --replay
uv run simula run aol --run <RUN> --from model --replay
uv run simula run ooc --run 20260929-212755-1f19585 --from model --replay
uv run simula run perplexity --run 20260929-212810-1f19585 --from model --replay
```

- `--from model --replay` re-executes model through flows, taking every model reply from `cache/`. Explore can't replay, since it drives the device, so its capture comes from the run folder. A call that isn't in the cache stops the run with exit code 4 instead of calling an API.
- Plain `--replay`, with no `--from`, skips every stage whose inputs, prompts, settings, code and outputs still hash the same as when it finished, so on a finished run at this commit it re-executes nothing.
- A replay covers only the stages the run finished: it stops at the first one the run never finished, says so, and exits 0. A run that stopped at explore (an app that refused the emulator) replays explore alone.
- `tests/test_replay.py` runs both on every committed run, in a fresh clone of the commit with the keys removed and outside requests refused. It checks that plain `--replay` skips every stage the run finished and that `--from model --replay` writes the same JSON outputs. Renders (PNG, PDF) aren't byte-stable across machines, so they aren't compared.

## Run folders

Each run lives in `runs/<app>/<run_id>/`:
- `<stage>/` holds each stage's output, plus `done.json` or `failure.json`.
- `manifest.json` records the git sha, profile, budget, models, prompt hashes, tool versions, provenance, and total spend.
- `trace.jsonl` has one line per decision or model call.
- `exhibits/NN-<stage>.md` is a readable summary of each stage.
- `needs-human.md` appears only when a person has to act.

`runs/<app>/latest` points at the newest run. The full layout is in docs/CONTRACTS.md.

## Setup

You need:
- macOS or Linux
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 20+
- For `explore` only: an Android emulator at 1080 × 2400 px and 420 dpi (the `Device` in `simula/contracts.py`), with the apps in `config/apps/` installed (`aol`, `janitorai`, `luzia`, `ooc`, `perplexity`), and `adb` on the path or `ANDROID_HOME` set.

```sh
uv sync
npm ci                              # mobile-mcp: @mobilenext/mobile-mcp 1.0.5, pinned in package.json
uv run playwright install chromium  # Playwright 1.63.0, pinned in pyproject.toml
cp .env.example .env                # then fill in the keys below
uv run simula doctor                # free preflight: packages, mobile-mcp, Chromium, emulator, apps, keys present
uv run simula doctor --keys         # also makes one tiny call per model (about a cent)
```

**Once, after installing the apps,** compile them ahead of time. On a busy emulator, an app's first cold start can take longer than Android allows, and Android kills it as not responding before explore sees a screen. In a rehearsal Perplexity was killed on 3 of 3 launches until it was compiled; afterwards it started in 9 s. It takes a few minutes per app:

```sh
for pkg in $(sed -n 's/^package = "\(.*\)"/\1/p' config/apps/*.toml); do adb shell cmd package compile -m speed -f "$pkg"; done
```

On a Mac with iCloud Desktop sync, `No module named 'simula'` means iCloud hid `.venv`, and Python 3.12.2+ skips hidden `.pth` files. Run `chflags -R nohidden .venv`, or clone outside `~/Desktop`.

Set these in `.env`, by name:

| Name | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Every Claude call: Opus 5.5, Sonnet 5.5, Haiku 4.5 (`dev` profile). |
| `OPENAI_API_KEY` | Judge #2: gpt-6-sol. A call that errors or times out on both tries moves to gpt-6-luna, and its trace line says `declared fallback used`. Both judges score every idea (`judges` in `config/profiles.toml`). |
| `TYPESAFE_API_KEY` | Jev (`jev-latest`), which ranks the explorer's next tap in the `real` profile. |
| `SIMULA_REDACT` | Explore. A comma-separated list of the emulator account's handle and names. See Safety and privacy. |

`SIMULA_REDACT` works at capture time. Explore replaces each listed string (any case), and every email address, with `[redacted]` in the element trees it captures, and paints over those elements in its screenshots before anything is saved. Explore refuses to start while `SIMULA_REDACT` is empty.

Tests:

```sh
uv run pytest -m "not live" -q      # offline, what CI runs; plain `uv run pytest` does the same
uv run pytest -m live               # real API calls and the emulator; a few cents
```

## Run it

`APP` is a file name in `config/apps/`. A stage command works on the run given by `--run ID`, else on `runs/<app>/latest`; it starts a new run only when the app has none.

```sh
uv run simula explore janitorai                 # add --new for a fresh run folder
uv run simula model janitorai
uv run simula mock janitorai
uv run simula qa janitorai
uv run simula propose janitorai
uv run simula judge janitorai
uv run simula flows janitorai

uv run simula run janitorai --new               # all seven stages in a new run
uv run simula run janitorai --from qa           # rerun qa and everything after it, in the latest run
```

A stage command always reruns its stage. `simula run` skips a stage whose inputs, prompts, and settings still hash the same as when it finished, and with `--from` it reruns that stage and every one after it. Explore is the exception: `simula run` reuses a captured explore as it is (a complete one, or a partial one that later stages are built on), even under a different `--budget`, and since no other stage hashes the budget, a budget change alone reruns nothing; `--new` or `--from explore` explores again. A stage refuses to start when a stage it reads from isn't done.

| Flag | Effect |
|---|---|
| `--profile real\|dev` | `real` (the default) uses the models in `[real.roles]` of `config/profiles.toml`. `dev` uses cheaper models and lower effort. |
| `--budget deep\|transfer` | Exploration size. `transfer` (the default) is 40 actions or 12 minutes; `deep` is 80 actions or 25 minutes. On a run that already has an explore, a new budget takes effect only with `--new` or `--from explore`. |
| `--no-send` | Explore skips the core-loop pass and sends no chat messages. `explore` and `run` only. |
| `--device SERIAL` | The emulator to explore on (default: `ANDROID_SERIAL`, else the only device online). `explore` and `run` only. |
| `--new` / `--from STAGE` | Start a new run folder; rerun from a stage onward (`run` only). |
| `--replay` | Cache only: no model calls, no device. A cache miss stops with exit code 4. |
| `--no-cache` | Skip cache reads; replies are still written. |
| `--usd-cap USD` | Override this stage's $ cap. |
| `--allow-account-create` | Recorded with the run's settings, but nothing acts on it yet: the explorer never creates an account, and its deny-list blocks sign-up taps. |

**Fixtures.** `--allow-fixtures --fixture STAGE=PATH` seeds one stage folder of a new run from a fixture, so later stages can run without the earlier ones:

```sh
uv run simula run janitorai --new --allow-fixtures --fixture model=tests/fixtures/golden/janitorai --from mock
```

Fixture runs are test data only:
- The run id ends in `-fixture`, exhibits carry a banner, and the slides carry a watermark.
- A stage refuses fixture input without `--allow-fixtures`.
- A fixture can only seed a run that the same command creates.

Exit codes: 0 done (a partial stage too; `needs-human.md` says what's missing); 2 a fixture refused, or `simula explore` refusing to replace the explore that later stages of the latest run were built on (add `--new`, or `--run ID`); 3 a command that isn't built (`compare-rankers`); 4 a $ cap stopped a stage (`needs-human.md` has the command to continue) or `--replay` missed the cache; 5 the model provider is refusing calls (a usage limit or an outage).

## Money

Each stage has a $ cap in `config/profiles.toml` (`[caps_usd]`); override one with `--usd-cap`.

| explore | model | mock | qa | propose | judge | flows | total | validation |
|---|---|---|---|---|---|---|---|---|
| $3 | $4 | $25 | $12 | $6 | $8 | $8 | $66 | $20 (`validation_usd_cap`) |

- **Before each model call:** code holds that call's worst case, the estimated input plus the maximum output tokens. If the stage's spend in this run, plus calls in flight, plus this call could pass the cap, the call is refused and the trace says so. The stage stops, writes `needs-human.md`, and exits 4; a stage that can go on without that call (flows, one idea at a time) finishes marked partial instead, with the command to rerun it under a higher cap.
- **Every model call** writes a line to `trace.jsonl` with its model, tokens, $, and cache key. A cache hit costs $0, and a failed call is charged for what it used. `manifest.json` keeps the run's `usd_total`.
- **Spend outside a run:** `uv run simula note "what I fixed by hand" --usd 0.40` appends a line to `build/trace.jsonl`. With `--run ID`, it goes into that run's trace as a hand fix.
- **`caps_usd` in `manifest.json`** is a copy of `[caps_usd]` from when the run was created. A stage runs under `--usd-cap` if given, else under `[caps_usd]` as it is when that stage runs, so a stage rerun later can have had a different cap than the manifest shows. Its spend is in its trace lines either way.

**Spend.** The committed runs cost $<RUN> in API calls, summed from their traces. Everything billed to the APIs while building this came to about $<RUN>: Anthropic $<RUN>, OpenAI $<RUN>. That covers these runs, development runs, PR merge-gate runs on fixtures, and the judge validation. Writing and reviewing the code ran on Claude Max and ChatGPT subscriptions, which aren't billed per call.

## Safety and privacy

- **Deny-list.** Explore never subscribes, buys, or signs in. Code blocks any tap whose text matches the deny-list in `simula/device/observe.py`, and each blocked tap is logged in the trace and counted in the explore exhibit. The list includes:
  - subscribe, buy, pay, purchase, restore, confirm, and "start … trial"
  - sign in or up, log in or out, and create account
  - delete, report, block, permission prompts, post, and more
  - on an upsell screen, its call-to-action words too (continue, try, start, get, claim, unlock, …)
- **Play billing BACK.** If Google Play's billing screen (`com.android.vending`) comes to the front, explore presses BACK before anything else runs.
- **Sending.** The core-loop pass sends a few neutral chat messages (3 on `transfer`, 8 on `deep`) to measure the free experience; gifts, coins, gems, tips, and credits stay blocked. `--no-send` skips the pass.
- **Content filter.** Explore looks for the app's content or safety filter (for example "SFW only" or "Hide NSFW") and picks its most restrictive option.
- **`content_rating`.** The model stage rates every screen safe, mixed, or unsafe. A generic adult-keyword list (`[content]` in `config/profiles.toml`) can only raise a rating to unsafe. The proposer and the judges see an unsafe screen's id, name, and rating but none of its text. An idea may only trigger on a screen rated safe or mixed.
- **Redaction at capture.** `SIMULA_REDACT` (see Setup) hides the account's handle, names, and email addresses before anything is saved.
- **Mock scope.** An unsafe screen stays out of the mock unless it is on a core flow, where leaving it out would break the flow. In the slides, card art from a screen not rated safe is blurred wherever it is drawn.

## Decisions

Three calls carry the design:

1. **Only the package name is per-app.** Content filters, content rating and budget are run flags that work the same on every app, so one command runs JanitorAI, Luzia, AOL, OOC and Perplexity. When the general rule costs one app something, that app takes the loss.
2. **Code checks the evidence; models interpret it.** Code checks cited IDs, quoted UI text, and connected edges, and drops an item that cites something it never recorded. What a screen means is a model's reading, and that reading can be wrong; the judge's evidence check is the second line.
3. **Proposals are reviewed, and the uncertainty stays on the page.** Five hard gates, then six judgment checks, scored by two judges from two providers. Accepted ideas get full flows; splits normally await approval on one "Needs your call" page; if none is accepted, the top split is drawn as the closest idea and labelled not a recommendation. **The judge failed its own bar:** neither rubric met the adoption bar, which was set after seeing earlier rounds' failures, and each let only 1 of 4 known-good ideas through both judges cleanly (`validation/report.md`).

<details>
<summary><b>All ten decisions: why, and what each one gave up</b></summary>

| # | Decision | Why | What it costs |
|---|---|---|---|
| 1 | **Nothing is tuned to one app.** The only per-app config is the package name. Content filters, content rating and budget are run flags that work the same on every app. | The system should be able to open any app, understand it, mock it and propose for it. Tuning to the test apps would make the demo look better and the system worse. | When the general rule costs one app something, we take the loss (a Luzia screen and an AOL screen stay out of their reference mocks). |
| 2 | **Every deliverable comes from a real explore run.** Fixtures and hand-built reference models are test data; a real run refuses them. | A deck built on hand-made input proves nothing about the explorer. | An app gets deliverables only as fast as a phone can explore it. |
| 3 | **One app deep, the rest to show it transfers.** JanitorAI gets the deep run. Luzia, AOL and OOC check transfer. Perplexity is extra evidence: an app nothing was built or tuned against. | Deep on one app beats shallow on five, but one app alone can't show the system is general. | An app that detects the emulator and closes gets a recorded run, not a workaround. Getting past emulator detection isn't this system's job. |
| 4 | **The mock draws the screens the model stage scoped, in parallel batches, and plans its spend before the first call.** Each batch's worst case is checked against the stage's cap before anything is sent. | The mock is half the cost of a run ($10.35 of $19.84 on a development JanitorAI run). A cap checked one call at a time can be blown by batches running at the same time. | Screens outside the scope aren't in the mock. |
| 5 | **The judge has hard gates, and a split goes to a person.** Five gates (policy, no cash rewards, no chat content, nothing free taken away, brand safety) must all pass. Six judgment checks are scored by the judges. An idea that fails something fixable gets one revision and is judged again from scratch. If the judges split on a judgment check, the idea isn't drawn: it goes on one "Needs your call" page at the end of the deck, with the open question and both judges' reasons, and its "To approve" entry. A person draws it by putting that entry in `flows/approvals.json`. The file replaces the draw list, so it must also name every accepted idea that should stay drawn. When nothing is accepted, the top-ranked split gets full slides, labelled as the closest idea, not a recommendation. | One overall model score lets a good-sounding idea through on charm. Gates make the lines that can't be crossed explicit. | More ideas get rejected or held back, and a person decides the held ones from one page. |
| 6 | **An idea that uses an app term we never saw explained is flagged, not dropped, and the judge never sees the flag.** | Dropping it threw away good ideas. Showing the flag to the judge would bias the verdict. The flag is for the person reading the deck. | A flagged idea can reach the slides; its flag is on its appendix card. |
| 7 | **The cost check is a mark, never a verdict.** Code estimates what the reward costs the app to serve and the ad rate at which it pays for itself. A result that isn't PASS is shown on that idea's slides and in the appendix. It never blocks the idea, changes its verdict or moves it in the ranking. | From outside the app we don't know its real serving costs or ad rates, so a hard cost rule would reject ideas on guesses. | An idea that might lose money can still be accepted. The mark is how the reader sees that. |
| 8 | **Two judges from two providers, and the stricter rubric, adopted without passing its bar.** Judge 1 is Claude Sonnet 5.5 and judge 2 is OpenAI gpt-6-sol (a call that errors or times out on both tries moves to gpt-6-luna, and the trace says so); both score every idea. We tested two rubrics on 22 planted defects and 4 ideas picked as good, against a bar written down after earlier rounds had failed. **Neither rubric met it:** each let only 1 of the 4 good ideas through both judges cleanly. We adopted the newer one (VF′) because, with both judges pooled, it was equal or better on every line we measured. Judge 1 alone caught all 22 planted defects; judge 2 alone caught 20, and neither of the 2 planted "specific to this app" (C7) defects. VF′ also rejects JanitorAI's "boost a small creator" idea, where the player gets nothing and other creators lose list places. | One model family grading its own family's output is the weakest check there is. | Judge 2 is strict on evidence: it failed that check on 3 of the 4 good ideas, so a good idea can end up on the "Needs your call" page instead of getting full slides. Three of VF′'s clauses were written after seeing the failures. All of it is in `validation/report.md`. |
| 9 | **The slides are for the app's product team.** Each drawn idea gets two slides: its flow step by step on the app's own screens, then why it works, in plain words with arrows and short callouts. Judge scores, cost assumptions and evidence ids go in the score pages and the appendix; engineers get `candidates.json`. | The people who'd say yes to an ad placement are product people, and they won't read an information dump. | The reasoning lives at the back, so a reader who wants it has to go there. |
| 10 | **Each stage stops and resumes on its own; people restart earlier stages, code doesn't.** Every stage writes a done file with hashes of its inputs, prompts, settings, code and outputs, and is skipped only when all of them match; a captured explore is reused as it is until `--new` or `--from explore`. A spend cap, a login wall or a provider outage stops the run with a note saying what a person needs to do. | Many steps here are probabilistic, so a failure has to stay inside its stage: you know which stage went wrong and restart from there. | A person steps in more often. During a development run, an API usage limit stopped QA, and QA was re-run by hand once access came back. |

</details>

## Goal 5: a proposed service for hundreds of apps (not built)

**Proposed service, not built.** The sketch is in [docs/GOAL5.md](docs/GOAL5.md). What exists today is the single-run CLI with its per-stage $ caps and traces, and one lock per emulator. In short:
- **Storage and versioning.** The service would keep one immutable folder per app, platform, version and run. Two versions would diff screen by screen on the fingerprint the explorer already computes.
- **When to re-explore.** On a new version (iOS has a public version lookup; on Android, the service would read the installed version after each run), plus a slow scheduled rerun for changes made on the server. A re-visit would replay the recorded taps and explore only the screens that changed.
- **Where people review.** Login walls and age gates, ideas the judges split on (the deck's "Needs your call" page), judge calls that failed twice (`judge/human-queue.md`), and approving which ideas go to the app's product team. A single run raises the first three today, in `needs-human.md`, the deck and the judge folder; the service would gather them in a review queue.
- **Cost per app.** One development JanitorAI run (n = 1, its stages at two commits, no judge 2) cost $19.84 in API calls, half of it the mock ($0.47 per screen drawn), plus about 17 minutes of emulator time. Under an assumed 15-minute review (about $12.50), that API spend exceeds the estimated review cost, so the lever at scale would be redrawing only the screens an update changed. Tonight's totals in Results are the numbers to quote.
- **Plugging into Simula.** The slides would be the sales artifact. An accepted idea maps onto the rewarded ad in Simula's native SDKs, which grant the reward on `REWARD_VERIFIED`; the deck's simulated ad does the same. The sketch's first table says what's built and what's only proposed.

## Known limits

- The judge failed its adoption bar on a small, reused set (4 known-good ideas, 22 planted defects) with no human labels; its verdicts are two models' opinion, not a validated call.
- A closest-idea slide carries an unresolved split; it is drawn only because nothing was accepted, and it says so.
- `flows/approvals.json` replaces the draw list rather than adding to it, so an approval file must also name the accepted ideas; the Needs your call page's footer says to "add its 'To approve' entry to the list", which drops the accepted ideas unless they are listed too.
- An approval of a split holds only while the judges split on exactly the checks it names; if the disagreement changes, the idea goes back to a person.
- The Needs your call page and the score pages' footers are written for Simula's reviewer; the deck for the app's product team is the one produced after approvals.
- The Needs your call page prints each idea's raw approval snippet. Where code strips element ids from a judge's reason, the Needs your call and Closest idea boxes can show stray punctuation or a broken sentence (",,", "matches's", JanitorAI's "…3+ days old from. That element…", Perplexity's "backed by, and").
- The score table's reason names the evidence check without quotes, so it reads "didn't pass backed by what was seen in the app".
- The duplicate check compares the benefit's name and who gets it, so two ideas that name their benefit differently can both be drawn: Luzia's deck draws two ideas that each feature a user's creation on Explore for 24 hours (c06, c08-rev). It also cuts the other way: it compares the benefit, not the moment, so a timing or cohort variant is dropped as a duplicate. JanitorAI's memory offer to a free user closing the paywall (c01) was dropped as a duplicate of the come-back-after-three-days memory idea (c06).
- The judges pass promotion ideas that add a separate row, because nobody else moves down (Luzia's c06 and c08-rev). A new row still spends the same finite attention, and the rubric counts what other creators lose but doesn't price attention, so a product team would likely cut these.
- No check weighs showing an ad to paying subscribers: JanitorAI's accepted c03-rev is offered only to Janitor Plus subscribers. Its cost mark is CONDITIONAL for a separate reason (at 8k context it pays for itself only above $13 eCPM, against a $10.16 benchmark).
- A trace line `declared fallback used` means that judge-2 call ran on gpt-6-luna, which the validation never measured.
- The deck draws at most 4 ideas; any survivor past the cap is scored and listed as not drawn, with no flow.
- QA's keep score picks the round QA keeps; it is not a fidelity percentage. Fonts and layered drawers are where the mocks visibly miss.
- A drawn reward confirms the grant rather than demonstrating the benefit: a confirmation screen ("10× memory is on", "Toki grew 1 step!"), or, where the mock can't show it working, a label on the screen that the caption calls a label. Broken interactions and undrawn screens are flagged on the slide, not hidden.
- The model stage's evidence check catches ids and quotes that were never captured, not wrong readings of what a screen means.
- The paywall detector can fire on a sponsored price in a feed: AOL's explore recorded s05, an article with a Temu ad in its Taboola block ("jackets for $2.36"), as a paywall, and its core-loop pass stopped there. The model stage then noted that no upgrade screen was seen.
- `config/profiles.toml`'s comment beside `judges` still says a split "goes to a person"; it goes to the Needs your call page.
- A config value the code reads but doesn't hash, such as the adult-keyword list, doesn't trigger a rerun when it changes.
- QA's measurement records in `cache/qa` are keyed by the page and its inputs, not by run, so two runs that draw the same page from the same inputs share one record.
- The deny-list is English only. The Play billing BACK works in any language, but covers Play purchases only.
- The mock copies its web fonts in when it's built, and renders refuse every outside request. A font that can't be fetched at build time is replaced by system fonts, and the trace says so.
- The mock draws 4 screens per batch, whatever their size; a batch of dense screens can still run out of output tokens.
- Art detection counts distinct colors, so a flat-shaded illustration with few colors isn't cropped as a picture.
- A count badge drawn over a picture in the app can leave a strip on the cropped art.
- In QA, a screen with nothing to measure (no tagged element, no tap, too little unmasked screen) scores 0 and still counts in the round's mean.
- When the QA critic refuses a group of screens, that group gets no fixes that round (and the round stops if every group refuses); the refused images aren't bisected out.
- The model stage names elements up to a naming budget; past it, later screens' elements go unnamed.
- Propose ranks by reach: the users who can see the trigger screen, from how deep it is, times the proposer's typed per-user `daily_cap`. Reach and the cost mark are scenarios from assumed rates, not a measured audience or advertiser ROAS.
- Propose's "shown in a chat" check is a keyword match per clause, so recall is left to the judge's brand-safety gate.
- The judge's revision naming call (about $0.01) is traced under `propose`, so it counts toward propose's cap.
- When a stream is cut off, thinking the API didn't stream back isn't charged.
- `simula compare-rankers` prints "not built yet".

## Not built, and why

| Not built | Why |
|---|---|
| Per-app code or config beyond the package name | The system has to work on apps it has never seen (Decision 1). |
| A way past emulator detection, or an iPhone driver | An app may refuse to run on an emulator; that run is recorded as it happened. Goal 5's answer is a real-device tier. |
| Automatic loop-back to an earlier stage | People restart earlier stages, by design (Decision 10). |
| A QA score that blocks flows | Nobody has measured what score is good enough, and blocking would leave an app with no slides. A QA that didn't finish is labeled `qa_incomplete`, and flows still runs. |
| A vector store or a database | A run folder of JSON files is enough at this scale. |
| A device pool and a job queue | One lock per device and `--device` let two explores run on two emulators. The rest is in Goal 5. |

## What I'd build next

- **An automated sign-in and sign-up phase.** Account creation was optional for this take-home, so the explorer never signs up: `--allow-account-create` is recorded with a run but not acted on, and an app that opens on a sign-up wall stops for a person. Next is an opt-in phase, run before explore, that signs up or signs in with a test identity and hands explore a logged-in app.
- **Partner test accounts.** For an app that's a signed customer, its own test build and accounts (Goal 5's partner tier) reach the screens behind a login without creating anything.
- **Additive approvals.** `flows/approvals.json` should add a split idea to the accepted ones instead of replacing the draw list.
- **A more realistic mock.** (a) For screens an idea doesn't change, use the captured screenshot as the base layer and draw only the new offer and ad on top; the trade-off is that those screens are no longer generated, so they stop showing what the mock can rebuild. (b) Set each text's font size from its recorded element geometry; today the builder model reads sizes off the screenshot. (c) Put the app's own character in the sponsored ad, where the simulated ad card now shows a tile in the app's colors: Simula's unit is a playable with sponsored characters, and the proposer already writes how each idea uses one.
- **Give the judge a human baseline.** About 15 blind human labels (`simula label`), and a measured fix for judge 2's strictness on evidence, tested on the same known-good ideas.
- **Redraw only what changed.** The mock is half of a run's cost, so an app update should redraw only the screens whose fingerprint changed.
- **Split the big modules.** Explore is about 2,100 lines, and mock and model about 1,000 each, with several responsibilities per file; split them by responsibility, as flows already is.
- **The Goal 5 service.** A versioned store, the re-explore triggers, a review queue in place of `needs-human.md`, and a device pool with a real-device tier for apps that refuse an emulator.
