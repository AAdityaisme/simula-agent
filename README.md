# simula-agent

Mobile app → product model → 1:1 mock → rewarded-ad proposal. This is Simula take-home #2; the assignment, verbatim, is in [docs/ASSIGNMENT.md](docs/ASSIGNMENT.md), and every file format is in [docs/CONTRACTS.md](docs/CONTRACTS.md).

## What it does

Given an Android app on an emulator, the pipeline explores the app and writes a product model of what it found: screens, flows, and money mechanics such as paywalls, limits, currencies, and ads. It draws the relevant screens as a clickable HTML mock and repairs the mock against the real screenshots. It then proposes rewarded-ad ideas that fit the app's own economy, has blind judges review them, and lays the survivors out as slides for the app's product team. Code records every fact and makes every decision it can check. Model calls read and write, and code checks their answers against what was captured.

| Stage | Purpose |
|---|---|
| `explore` | Drives the app through mobile-mcp and records every distinct screen, the tap graph, full-size screenshots, and element trees. It then repeats the app's core action and measures the free experience. |
| `model` | Turns the capture into `product_model.json`. Code writes the facts, and one model call adds meaning (names, purposes, flows, mechanics, content ratings) keyed to them. Code drops any claim the capture doesn't show. |
| `mock` | Draws the screens in scope as one clickable HTML page, in parallel batches, reusing pictures cropped from the screenshots. |
| `qa` | Scores each mock screen against its screenshot on element bounds, navigation, and masked SSIM. A critic explains the gaps, a fixer edits the page, and the best round goes to `qa/approved/`. |
| `propose` | One model call per lens proposes rewarded-ad ideas. Code checks each idea against the product model, adds a cost line, and ranks the ideas. |
| `judge` | Blind judges score every idea on gates and judgment checks. Code turns their verdicts into accept, reject, conditional, or needs-human, and a fixable reject gets one revision. |
| `flows` | Adds each surviving idea's new screens to a copy of the approved mock, taps through every step in Playwright, and writes `slides.html` and `slides.pdf`. |

Each run lives in `runs/<app>/<run_id>/`:
- `<stage>/` holds each stage's output, plus `done.json` or `failure.json`.
- `manifest.json` records the git sha, profile, budget, models, prompt hashes, tool versions, provenance, and total spend.
- `trace.jsonl` has one line per decision or model call.
- `exhibits/NN-<stage>.md` is a readable summary of each stage.
- `needs-human.md` appears only when a person has to act.

`runs/<app>/latest` points at the newest run. The full layout is in docs/CONTRACTS.md.

## Results

Every run below is committed with its cache, ran on the same commit (`<RUN: sha>`), and replays without keys or a device (see Replay). `<RUN>` marks what tonight's runs fill in.

| App | Why it's here | Budget | Run | Screens | Ideas: proposed → accepted / CONDITIONAL / to a person / rejected | Deck | API $ |
|---|---|---|---|---|---|---|---|
| JanitorAI | the deep run | `deep` | `runs/janitorai/<RUN>/` | <RUN> | <RUN> | `flows/slides.pdf` | <RUN> |
| Luzia | checks the system transfers | `transfer` | `runs/luzia/<RUN>/` | <RUN> | <RUN> | `flows/slides.pdf` | <RUN> |
| AOL | checks the system transfers | `transfer` | `runs/aol/<RUN>/` | <RUN> | <RUN> | `flows/slides.pdf` | <RUN> |
| Perplexity | outside the test set: nothing was built or tuned against it | `transfer` | `runs/perplexity/<RUN>/` | <RUN> | <RUN> | `flows/slides.pdf` | <RUN> |

**OOC isn't run: it doesn't run on the Android emulator.** Getting past an app's emulator detection isn't this system's job; [docs/GOAL5.md](docs/GOAL5.md) says how a service would handle such apps (a real-device tier).

**Hand fixes.** A person stepping in during a run is a `hand_fix` line, decided by `human`, in that run's `trace.jsonl` (`simula note --run ID`). Tonight's runs: <RUN: each hand fix, or "none">.

## Setup

You need:
- macOS or Linux
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 20+
- For `explore` only: an Android emulator at 1080 × 2400 px and 420 dpi (the `Device` in `simula/contracts.py`), with the apps in `config/apps/` installed (`aol`, `janitorai`, `luzia`, `perplexity`), and `adb` on the path or `ANDROID_HOME` set. `ooc.toml` is there too, but OOC doesn't run on the Android emulator (see Results).

```sh
uv sync
npm ci                              # mobile-mcp: @mobilenext/mobile-mcp 1.0.5, pinned in package.json
uv run playwright install chromium  # Playwright 1.63.0, pinned in pyproject.toml
cp .env.example .env                # then fill in the keys below
uv run simula doctor                # free preflight: packages, mobile-mcp, Chromium, emulator, apps, keys present
uv run simula doctor --keys         # also makes one tiny call per model (about a cent)
```

On a Mac with iCloud Desktop sync, `No module named 'simula'` means iCloud hid `.venv`, and Python 3.12.2+ skips hidden `.pth` files. Run `chflags -R nohidden .venv`, or clone outside `~/Desktop`.

Set these in `.env`, by name:

| Name | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | Every Claude call: Opus 5.5, Sonnet 5.5, Haiku 4.5 (`dev` profile). |
| `OPENAI_API_KEY` | Judge #2 (gpt-6-sol, falling back to gpt-6-luna). Both judges score every idea (`judges` in `config/profiles.toml`). |
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

A stage command always reruns its stage. `simula run` skips a stage whose inputs, prompts, and settings still hash the same as when it finished, and with `--from` it reruns that stage and every one after it. A stage refuses to start when a stage it reads from isn't done.

| Flag | Effect |
|---|---|
| `--profile real\|dev` | `real` (the default) uses the models in `[real.roles]` of `config/profiles.toml`. `dev` uses cheaper models and lower effort. |
| `--budget deep\|transfer` | Exploration size. `transfer` (the default) is 40 actions or 12 minutes; `deep` is 80 actions or 25 minutes. |
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

## Replay without keys or a device

Every committed run replays from a clean clone: no API keys, no emulator, and no model calls. You still need Chromium, because mock, qa and flows render their pages when they run.

```sh
git clone https://github.com/AAdityaisme/simula-agent && cd simula-agent   # on a Mac, clone outside ~/Desktop (iCloud)
uv sync
uv run playwright install chromium
uv run simula run janitorai --run <RUN> --budget deep --from model --replay
uv run simula run luzia --run <RUN> --from model --replay
uv run simula run aol --run <RUN> --from model --replay
uv run simula run perplexity --run <RUN> --from model --replay
```

- `--from model --replay` re-executes model through flows, taking every model reply from `cache/`. Explore can't replay, since it drives the device, so its capture comes from the run folder. A call that isn't in the cache stops the run with exit code 4 instead of calling an API.
- Plain `--replay`, with no `--from`, skips every stage whose inputs, prompts, settings, code and outputs still hash the same as when it finished, so on a finished run at this commit it re-executes nothing.
- `tests/test_replay.py` runs both on every committed run, in a fresh clone of the commit with the keys removed and outside requests refused. It checks that plain `--replay` skips all seven stages and that `--from model --replay` writes the same JSON outputs. Renders (PNG, PDF) aren't byte-stable across machines, so they aren't compared.

## Money

Each stage has a $ cap in `config/profiles.toml` (`[caps_usd]`); override one with `--usd-cap`.

| explore | model | mock | qa | propose | judge | flows | total | validation |
|---|---|---|---|---|---|---|---|---|
| $3 | $4 | $25 | $12 | $6 | $8 | $8 | $66 | $20 (`validation_usd_cap`) |

- **Before each model call:** code holds that call's worst case, the estimated input plus the maximum output tokens. If the stage's spend in this run, plus calls in flight, plus this call could pass the cap, the call is refused and the trace says so. The stage stops, writes `needs-human.md`, and exits 4; a stage that can go on without that call (flows, one idea at a time) finishes marked partial instead, with the command to rerun it under a higher cap.
- **Every model call** writes a line to `trace.jsonl` with its model, tokens, $, and cache key. A cache hit costs $0, and a failed call is charged for what it used. `manifest.json` keeps the run's `usd_total`.
- **Spend outside a run:** `uv run simula note "what I fixed by hand" --usd 0.40` appends a line to `build/trace.jsonl`. With `--run ID`, it goes into that run's trace as a hand fix.
- **`caps_usd` in `manifest.json`** is a copy of `[caps_usd]` from when the run was created. A stage runs under `--usd-cap` if given, else under `[caps_usd]` as it is when that stage runs, so a stage rerun later can have had a different cap than the manifest shows. Its spend is in its trace lines either way.

**Spend.** The four committed runs cost $<RUN> in API calls, summed from their traces. Everything billed to the APIs while building this came to about $<RUN>: Anthropic $<RUN>, OpenAI $<RUN>. That covers these runs, development runs, PR merge-gate runs on fixtures, and the judge validation. Writing and reviewing the code ran on Claude Max and ChatGPT subscriptions, which aren't billed per call.

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

The ten calls that shaped this system, why, and what each one gave up.

| # | Decision | Why | What it costs |
|---|---|---|---|
| 1 | **Nothing is tuned to one app.** The only per-app config is the package name. Content filters, content rating and budget are run flags that work the same on every app. | The system should be able to open any app, understand it, mock it and propose for it. Tuning to the test apps would make the demo look better and the system worse. | When the general rule costs one app something, we take the loss (a Luzia screen and an AOL screen stay out of their reference mocks). |
| 2 | **Every deliverable comes from a real explore run.** Fixtures and hand-built reference models are test data; a real run refuses them. | A deck built on hand-made input proves nothing about the explorer. | An app gets deliverables only as fast as a phone can explore it. |
| 3 | **One app deep, three more to show it transfers.** JanitorAI gets the deep run. Luzia and AOL check transfer. Perplexity is an app nothing was built or tuned against. OOC isn't run: it doesn't run on the Android emulator. | Deep on one app beats shallow on four, but one app alone can't show the system is general. | OOC gets one line here instead of a workaround. Getting past emulator detection isn't this system's job. |
| 4 | **The mock draws the screens the model stage scoped, in parallel batches, and plans its spend before the first call.** Each batch's worst case is checked against the stage's cap before anything is sent. | The mock is half the cost of a run ($10.35 of $19.84 on a development JanitorAI run). A cap checked one call at a time can be blown by batches running at the same time. | Screens outside the scope aren't in the mock. |
| 5 | **The judge has hard gates, and a split goes to a person.** Five gates (policy, no cash rewards, no chat content, nothing free taken away, brand safety) must all pass. Six judgment checks are scored by the judges. An idea that fails something fixable gets one revision and is judged again from scratch. If the judges disagree, the idea goes to a human queue (`judge/human-queue.md`). | One overall model score lets a good-sounding idea through on charm. Gates make the lines that can't be crossed explicit. | More ideas get rejected or held, and a person has to settle the held ones. |
| 6 | **An idea that uses an app term we never saw explained is flagged, not dropped, and the judge never sees the flag.** | Dropping it threw away good ideas. Showing the flag to the judge would bias the verdict. The flag is for the person reading the deck. | A flagged idea can reach the slides; its flag is on its appendix card. |
| 7 | **The cost check is a mark, never a verdict.** Code estimates what the reward costs the app to serve and the ad rate at which it pays for itself. A result that isn't PASS is shown on that idea's slides and in the appendix. It never blocks the idea, changes its verdict or moves it in the ranking. | From outside the app we don't know its real serving costs or ad rates, so a hard cost rule would reject ideas on guesses. | An idea that might lose money can still be accepted. The mark is how the reader sees that. |
| 8 | **Two judges from two providers, and the stricter rubric, adopted without passing its bar.** Judge 1 is Claude Sonnet 5.5 and judge 2 is OpenAI gpt-6-sol; both score every idea. We tested two rubrics on 22 planted defects and 4 ideas picked as good, against a bar written down after earlier rounds had failed. **Neither rubric met it:** each let only 1 of the 4 good ideas through both judges cleanly. We adopted the newer one (VF′) because it was equal or better on every line we measured: with both judges it caught all 22 planted defects, and it rejects JanitorAI's "boost a small creator" idea, where the player gets nothing and other creators lose list places. | One model family grading its own family's output is the weakest check there is. | Judge 2 is strict on evidence: it failed that check on 3 of the 4 good ideas, so a good idea can land in the human queue. Three of VF′'s clauses were written after seeing the failures. All of it is in `validation/report.md`. |
| 9 | **The slides are for the app's product team.** Each idea gets two slides: its flow step by step on the app's own screens, then why it works, in plain words with arrows and short callouts. Judge scores, cost assumptions and evidence ids go in the score pages and the appendix; engineers get `candidates.json`. | The people who'd say yes to an ad placement are product people, and they won't read an information dump. | The reasoning lives at the back, so a reader who wants it has to go there. |
| 10 | **Each stage stops and resumes on its own; people restart earlier stages, code doesn't.** Every stage writes a done file with hashes of its inputs, prompts, settings, code and outputs, and is skipped only when all of them match. A spend cap, a login wall or a provider outage stops the run with a note saying what a person needs to do. | Many steps here are probabilistic, so a failure has to stay inside its stage: you know which stage went wrong and restart from there. | A person steps in more often. During a development run, an API usage limit stopped QA, and QA was re-run by hand once access came back. |

## Goal 5: running this across hundreds of apps

The sketch is in [docs/GOAL5.md](docs/GOAL5.md). In short:
- **Storage and versioning.** One immutable folder per app, platform, version and run. Two versions diff screen by screen on the fingerprint the explorer already computes.
- **When to re-explore.** On a new version (iOS has a public version lookup; on Android, read the installed version after each run), plus a slow scheduled rerun for changes made on the server. A re-visit replays the recorded taps and explores only the screens that changed.
- **Where people review.** Login walls and age gates, judge splits (`judge/human-queue.md`), and approving which ideas go to the app's product team.
- **Cost per app, measured.** $19.84 in API calls on one development JanitorAI run, half of it the mock ($0.47 per screen drawn), plus about 17 minutes of emulator time for explore. Tokens outweigh an assumed 15 minutes of human review (about $12.50), so the lever at scale is redrawing only the screens an update changed. Tonight's runs are in Results.
- **Plugging into Simula.** The slides are the sales artifact. An accepted idea maps onto the rewarded ad in Simula's native SDKs, which grant the reward on `REWARD_VERIFIED`; the deck's simulated ad does the same. The sketch's last table says what's built and what's only proposed.

## Known limits

- The deny-list is English only. The Play billing BACK works in any language, but covers Play purchases only.
- The mock copies its web fonts in when it's built, and renders refuse every outside request. A font that can't be fetched at build time is replaced by system fonts, and the trace says so.
- The mock draws 4 screens per batch, whatever their size; a batch of dense screens can still run out of output tokens.
- Art detection counts distinct colors, so a flat-shaded illustration with few colors isn't cropped as a picture.
- A count badge drawn over a picture in the app can leave a strip on the cropped art.
- In QA, a screen with nothing to measure (no tagged element, no tap, too little unmasked screen) scores 0 and still counts in the round's mean.
- When the QA critic refuses a group of screens, that group gets no fixes that round (and the round stops if every group refuses); the refused images aren't bisected out.
- The model stage names elements up to a naming budget; past it, later screens' elements go unnamed.
- Propose ranks by reach: the users who can see the trigger screen, from how deep it is, times the proposer's typed per-user `daily_cap`. It's a scenario, not a measured audience.
- Propose's "shown in a chat" check is a keyword match per clause, so recall is left to the judge's brand-safety gate.
- The judge's revision naming call (about $0.01) is traced under `propose`, so it counts toward propose's cap.
- When a stream is cut off, thinking the API didn't stream back isn't charged.
- `simula compare-rankers` prints "not built yet".

## Not built, and why

| Not built | Why |
|---|---|
| Per-app code or config beyond the package name | The system has to work on apps it has never seen (Decision 1). |
| A way past emulator detection, or an iPhone driver | OOC is the one test app it blocks. Goal 5's answer is a real-device tier. |
| Automatic loop-back to an earlier stage | People restart earlier stages, by design (Decision 10). |
| A QA score that blocks flows | Nobody has measured what score is good enough, and blocking would leave an app with no slides. A QA that didn't finish is labeled `qa_incomplete`, and flows still runs. |
| A vector store or a database | A run folder of JSON files is enough at this scale. |
| A device pool and a job queue | One lock per device and `--device` let two explores run on two emulators. The rest is in Goal 5. |

## What I'd build next

- **Draw the app's character on the ad card.** Simula's unit is a playable with sponsored characters. The proposer already writes how each idea uses a character, but the simulated ad card shows only the app's colors.
- **Give the judge a human baseline.** About 15 blind human labels (`simula label`), and a measured fix for judge 2's strictness on evidence, tested on the same known-good ideas.
- **Redraw only what changed.** The mock is half of a run's cost, so an app update should redraw only the screens whose fingerprint changed.
- **The Goal 5 service.** A versioned store, the re-explore triggers, a review queue in place of `needs-human.md`, and a device pool with a real-device tier for apps like OOC.
- **Guest accounts.** `--allow-account-create` is recorded with a run, but the explorer doesn't act on it yet, so an app that opens on a sign-up wall stops for a person.
