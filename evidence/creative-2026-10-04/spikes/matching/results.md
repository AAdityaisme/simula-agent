# Does the best creative depend on the character? Spike on take-home 1 data (2026-10-03)

Repo cf97985 untouched (read only; venv built with `uv sync`). Real data, 1,000,000 impressions, 5,000 characters. Bundle B reused as committed (`load_bundle`, `predict` = `transform` + calibration shift), no re-implementation of the feature contract.

## Verdict

**Weakens, mildly.** The raw data look like a strong yes (horror characters click creative C14=21611 at 24.4% vs 4.3% for other genres), but that gap is identical for every other creative on the same app: it is a surface x genre effect, not a creative x genre effect. With surface/day/hour controls the creative x genre interaction is not detectable (LR 167.1 on 160 df, p = 0.33; permutation p = 0.56), and tailoring the creative by genre loses out of sample (-0.06 log-odds, about -0.8 CTR pp, versus +0.22 log-odds in sample). Bundle B can express such interactions and uses them a little (top creative changes for 12% of requests across genres, 75% across mature vs sfw), but those flips are mostly near-ties and the model's own tier interaction is not supported by the data. Limit: creatives are hashed ids with no content, so this cannot test the semantic claim (Batman chat -> DC game ad); it only says no creative-id-level interaction shows up among creatives that run side by side.

## Headline numbers

Measurement (a), data, no model (full 1M rows; top-20 creatives per definition; K x G cells):

| question | result |
|---|---|
| naive pooled LR, additive surface/day/hour controls only (tuple x genre) | stat 2,089.8, df 171, p < 1e-300 (looks like a huge yes; tier: 91.1 / 38, p = 2.9e-6) |
| same test, group effect allowed to differ by surface and by day, exact surface effects (primary) | tuple x genre 167.1 / 160, p = 0.33 (perm 0.561, B = 40, null mean 170.2, 95th pct 196.3); tuple x tier 41.4 / 36, p = 0.25 (perm 0.333) |
| same, other creative definitions | look (tuple minus C14) x genre 155.4 / 148, p = 0.32; x tier 36.0 / 34, p = 0.38. C14 x genre 163.7 / 171, p = 0.64; x tier 45.0 / 38, p = 0.20 |
| within one surface at a time, statistics summed (3 to 5 surfaces) | tuple x genre 306.9 / 268, p = 0.051 (perm 0.059, B = 100, null mean 270.7, 95th pct 305.9); look x genre 180.1 / 198, p = 0.815 (perm 0.901); C14 x genre 205.5 / 207, p = 0.517; tier p = 0.71 / 0.59 / 0.66 |
| share of genres whose best creative differs from the overall best AND is distinguishable (95%) | within surface, tuple: 1 of 30 surface-genre pairs = 3% (null mean 0.91, max 3 in 100 permutations); look: 0 of 40; C14: 0 of 50; tier: 0 of 9 (tuple) / 12 (look). The argmax alone differs in 23/30 (null 21.7) and 17/40 (null 17.2) pairs, so "differs" without a significance filter is noise. The one nominal hit: site:1fbe01fe, historical, +0.424 log-odds [0.085, 0.763] (not multiplicity-adjusted). Naive pooled raw CTR: 2/10 genres differ, 1/10 significant (mentor), and that one is a surface effect (below) |
| held-out value of tailoring, within surface (pick per-genre best on half the characters, score on the other half; 20 evaluations) | tuple x genre: in sample +0.216 log-odds (+3.05 CTR pp), held out -0.062 (SD 0.061, Wald SE 0.062) = -0.78 pp (SD 1.05), positive in 3 of 20. look x genre: +0.118 (+1.59 pp) in sample, -0.105 (-1.40 pp) held out, positive in 0 of 20. Tier: -0.007 / -0.008 log-odds |
| held-out log-likelihood, interaction model vs additive (pooled primary) | genre: -18.8 per 10k impressions (SD 5.4; tuple), -20.0 (look), interaction wins 0 of 20 evaluations; tier: -2.1 / -3.3 |
| power (planted +delta log-odds on one random creative per genre, simulated clicks, 8 reps each) | LR rejects at 5%: delta 0 -> 1/8, 0.05 -> 2/8, 0.1 -> 2/8, 0.2 (+2.8 pp) -> 4/8, 0.3 (+4.2 pp) -> 6/8. A single cell contrast has a 95% interval about +-0.3 log-odds wide (e.g. fantasy +0.215 [-0.173, +0.602]). So small per-cell tailoring gains cannot be excluded; the aggregate held-out gain (-0.062, Wald SE 0.062) leaves room for at most about +0.06 log-odds (about +0.8 pp) at the top of its interval |

Why the raw data mislead (counts, Wilson 95% intervals; group vs the other nine genres, on the creative's own busiest surface; "other creatives" = every other creative shown on that same surface):

| creative | surface | group | this creative: group CTR (n) | this creative: rest CTR (n) | other creatives, same surface: group CTR (n) | other creatives: rest CTR (n) |
|---|---|---|---|---|---|---|
| C14=21611 | app:febd1138 | horror | 0.244 [0.222, 0.266] (1,486) | 0.043 [0.039, 0.047] (10,290) | 0.271 [0.239, 0.305] (680) | 0.068 [0.062, 0.075] (6,235) |
| C14=4687 pos1 | site:e151e245 | mentor | 0.144 [0.125, 0.164] (1,296) | 0.347 [0.338, 0.356] (10,487) | 0.138 [0.129, 0.147] (5,622) | 0.308 [0.304, 0.313] (47,994) |
| C14=21189 | app:92f5800b | horror | 0.218 [0.193, 0.245] (956) | 0.019 [0.016, 0.023] (6,320) | 0.237 [0.225, 0.250] (4,619) | 0.018 [0.017, 0.020] (29,953) |

Each creative runs mostly on one surface (56% / 99% / 96% of its rows; for the first, that surface is 63% this creative), so a creative x genre gap and a surface x genre gap are nearly the same number. The test is only identified from creatives that share a surface, and the surface's own genre pattern shows up for all of them.

Measurement (b), bundle B, 2,000 random test-window requests x 20 most common training-window creative tuples (10 distinct looks; 10 of the 20 are one ad under 10 C14 ids), only the character changed:

| quantity (K = 20 menu) | value (95% interval, request-level) |
|---|---|
| top creative changes, swap to a character of another genre, same tier | 12.9% [11.8, 14.0] at tuple level; 12.2% [11.1, 13.2] counting only look changes. Feature-only genre swap over the 42 pairs the model scores differently: 12.9% [12.2, 13.6]. Only horror, mentor, romance differ from the other seven genres; those 24 pairs flip 14% to 34% (mean 22.5%), the other 21 pairs about 0 |
| top creative changes, mature vs sfw (feature-only) | 75.8% [74.0, 77.7] (look level 75.0% [73.1, 76.9]); same genre, other tier (real characters, look level) 42.9% [41.7, 44.2] |
| control: another character of the same genre and tier | 0.0% (exactly; creator_type and character_age_days have zero split gain) |
| rank correlation of creative scores across characters | genre-only pairs: mean Spearman 0.942 (median 0.994); tier: 0.978 (mature vs sfw 0.967) |
| size of the interaction in the model (logit SD, per-request double-centred) | creative x genre 0.084, creative x tier 0.033, against a creative main effect of 0.43 (ratio 0.19 and 0.08) |
| what a flip is worth (model's own pCTR) | ignoring genre costs (averaged over all 45 genre pairs) 0.9% relative pCTR on average, 7.6% when the top creative changes, >= 2% in 8.5% of cases; ignoring tier costs 4.1% on average, >= 2% in 47% of cases. Median top-1 to top-2 gap is 4.2% relative; 34.5% of requests have a gap under 2% |
| can the model express it? | Yes in principle: trees (31 leaves, no interaction constraints) can split on genre/tier and C14..C21 along one path; 55.7% of the 183 trees do, carrying 10.0% of split gain. In practice genre carries 5.2% of total gain, safety_tier 0.4%, creator_type 0, age 0 |

## Method (five lines)

1. Creative = the full candidate tuple (banner_pos + C14..C21), because C14 alone maps to several tuples and C15xC16 behaves like banner size; sensitivity with C14 alone and with the tuple minus C14 ("look", which collapses the 10 C14 twins). Top 20 by volume each. Genre = character_name prefix; tier from characters.csv.
2. Pooled and within-surface logistic LR tests of creative x group (cell means vs additive creative + group), nuisance = surface, day, hour, control group, plus surface x group and day x group; a naive spec without those is shown to expose the confound; character-level label permutations (shuffling labels among characters, within modal surface for the pooled test) calibrate the p-values.
3. Best-vs-overall-best contrasts use only comparisons the data can identify (estimable contrasts via the information-matrix null space), Wald intervals; held-out gain and held-out log-likelihood use random character halves.
4. Bundle B: real test-window requests x fixed menu; character swapped through the contract's own fields (genre, tier, creator_type, age recomputed from created_at; checked equal to `add_flags`); drawn real characters from all 30 genre x tier cells, 2 draws each, plus feature-only overrides.
5. Out-of-time check of the model's character-effect heterogeneity on all 127,406 test rows.

Files: `lib.py` (loading, sparse IRLS), `a_data.py <B_pooled> <B_within>` (run with 40 100; about 18 min), `b_model.py 2000` (about 4 min cold, 35 s warm), `make_b_md.py`, `check_confound.py` (output in `confound_out.txt`), `exp_pooled.py` (found that lumping about 1,500 small surfaces into one "other" bucket manufactured the residual signal; it was run against an earlier `a_data.pooled_test` signature and does not run as committed). `a_results.json`, `b_results.json` hold every number.

## Caveats

- Creatives are hashed ids with no content. The CEO's claim is about content relevance (Batman -> DC game); a null here only says that creative id does not interact with genre among co-running ads. A real content-matched test needs creatives labelled by theme.
- Identification: creatives are tied mostly to one or a few surfaces, genre effects vary by surface (horror 22% to 27% vs 2% to 7% on two apps; mentor 14% vs 31% to 35% on one site), and genre mix shifts by day (characters were created over time). Creative x genre is identified only from creatives sharing a surface and day. Among the 12 biggest surfaces only 3 to 5 have three or more creatives with at least 2,000 impressions, and most of those creatives are near-identical twins. The pooled spec with a lumped "other" surface bucket gave p = 4.9e-7 (tuple x genre), the exact-surface spec p = 0.33: the answer depends on this control.
- Click, not install. Base CTR 18.0%; the mix of accidental banner clicks may not move with creative-character fit the way installs would.
- Model vs data disagree on tier: B flips the top creative 75% of the time for mature vs sfw (and thinks it is worth 4.1% pCTR) while the data show no tier x creative interaction (pooled p = 0.25, within-surface p = 0.71, held-out log-likelihood worse). The flips are mostly near-ties among near-identical ads, and the menu crosses requests with creatives that never ran on those surfaces. The model's character-effect heterogeneity is real at surface level out of time (coefficient 1.22 [1.06, 1.39] for mature, 1.15 [1.06, 1.23] for horror/romance/mentor), but its creative-within-surface part cannot be separated from device and hour mix at this cell size (mature: 0.34 [-0.77, 1.45]; genre: 1.36 [0.96, 1.76]).
- Statistics: LR tests assume independent impressions; permutations respect character clusters but not users (device_ip), so p-values may be a bit optimistic. Permutation counts are small (40 / 20 / 100 / 50), power runs have 8 reps, and 2,000 requests drive (b). "Distinguishable" uses nominal 95% Wald intervals without multiplicity adjustment (the permutation null shows what chance gives).
- The ranker's tier gate (content tier <= min(publisher, character tier)) is not applied in (b); the menu has no content tiers.


# Measurement (a) tables: data, no model

### 1. Pooled likelihood-ratio tests of creative x group interaction (top 20 creatives per definition)

Specs: naive = additive surface(top30+other)/day/hour/control only; inter+other = adds surface x group and day x group nuisance terms; primary = same nuisance terms but surface effects exact (rows on the 100 biggest surfaces only, no lumped bucket).

| creative def | group | spec | rows (share of top-K rows kept) | K x G | LR stat | df | p (chi-sq) | permutation p (B) | perm null mean stat |
|---|---|---|---|---|---|---|---|---|---|
| tuple | genre | naive | 167,320 (1.00) | 20x10 | 2089.8 | 171 | <1e-300 | - | - |
| tuple | safety_tier | naive | 167,320 (1.00) | 20x3 | 91.1 | 38 | 2.9e-06 | - | - |
| C14 | genre | naive | 313,974 (1.00) | 20x10 | 3814.0 | 171 | <1e-300 | - | - |
| C14 | safety_tier | naive | 313,974 (1.00) | 20x3 | 291.0 | 38 | 9.7e-41 | - | - |
| look | genre | naive | 291,834 (1.00) | 20x10 | 3296.3 | 171 | <1e-300 | - | - |
| look | safety_tier | naive | 291,834 (1.00) | 20x3 | 236.6 | 38 | 1.6e-30 | - | - |
| tuple | genre | inter+other | 167,320 (1.00) | 20x10 | 273.5 | 168 | 4.9e-07 | - | - |
| tuple | safety_tier | inter+other | 167,320 (1.00) | 20x3 | 48.3 | 38 | 0.12 | - | - |
| tuple | genre | primary | 156,238 (0.93) | 20x10 | 167.1 | 160 | 0.33 | 0.561 (40) | 170.2 |
| tuple | safety_tier | primary | 156,238 (0.93) | 20x3 | 41.4 | 36 | 0.25 | 0.333 (20) | 37.2 |
| C14 | genre | primary | 290,464 (0.93) | 20x10 | 163.7 | 171 | 0.64 | - | - |
| C14 | safety_tier | primary | 290,464 (0.93) | 20x3 | 45.0 | 38 | 0.2 | - | - |
| look | genre | primary | 273,888 (0.94) | 20x10 | 155.4 | 148 | 0.32 | 0.634 (40) | 163.7 |
| look | safety_tier | primary | 273,888 (0.94) | 20x3 | 36.0 | 34 | 0.38 | 0.381 (20) | 36.1 |

### 2. Within-surface tests (one model per big surface, creatives with >= 2,000 impressions there, day/hour/control + day x group nuisance; statistics summed across surfaces)

| creative def | group | surfaces used | LR sum stat / df | p (chi-sq) | permutation p (B) | perm null mean / 95th pct of sum | (surface x group) pairs | best != overall best | ... and 95%-distinguishable | perm null: mean differs / mean distinguishable (max) |
|---|---|---|---|---|---|---|---|---|---|---|
| tuple | genre | 3 | 306.9 / 268 | 0.051 | 0.059 (100) | 270.7 / 305.9 | 30 | 23 | 1 | 21.7 / 0.91 (3) |
| tuple | safety_tier | 3 | 53.6 / 60 | 0.706 | 0.706 (50) | 60.8 / 77.5 | 9 | 4 | 0 | 4.8 / 0.12 (2) |
| C14 | genre | 5 | 205.5 / 207 | 0.517 | - | - | 50 | 13 | 0 | - |
| C14 | safety_tier | 5 | 43.2 / 46 | 0.592 | - | - | 15 | 1 | 0 | - |
| look | genre | 4 | 180.1 / 198 | 0.815 | 0.901 (100) | 201.3 / 233.5 | 40 | 17 | 0 | 17.2 / 0.62 (4) |
| look | safety_tier | 4 | 39.7 / 44 | 0.656 | 0.647 (50) | 43.2 / 59.2 | 12 | 2 | 0 | 3.7 / 0.02 (1) |

Per-surface detail (tuple and look definitions):

| creative def | group | surface | rows | K | LR stat / df | p | genres/tiers with best != overall best | ... distinguishable |
|---|---|---|---|---|---|---|---|---|
| tuple | genre | site:1fbe01fe | 97,354 | 21 | 194.8 / 180 | 0.214 | 8/10 | 1 |
| tuple | genre | site:e151e245 | 29,927 | 5 | 43.9 / 36 | 0.172 | 8/10 | 0 |
| tuple | genre | app:92f5800b | 30,259 | 7 | 68.3 / 52 | 0.064 | 7/10 | 0 |
| tuple | safety_tier | site:1fbe01fe | 97,354 | 21 | 38.7 / 40 | 0.529 | 1/3 | 0 |
| tuple | safety_tier | site:e151e245 | 29,927 | 5 | 6.3 / 8 | 0.609 | 1/3 | 0 |
| tuple | safety_tier | app:92f5800b | 30,259 | 7 | 8.6 / 12 | 0.737 | 2/3 | 0 |
| look | genre | site:1fbe01fe | 136,845 | 13 | 102.2 / 108 | 0.639 | 3/10 | 0 |
| look | genre | site:e151e245 | 39,369 | 5 | 30.1 / 36 | 0.746 | 5/10 | 0 |
| look | genre | app:92f5800b | 34,967 | 5 | 30.1 / 36 | 0.744 | 9/10 | 0 |
| look | genre | site:5b08c53b | 11,238 | 3 | 17.7 / 18 | 0.479 | 0/10 | 0 |
| look | safety_tier | site:1fbe01fe | 136,845 | 13 | 22.4 / 24 | 0.556 | 1/3 | 0 |
| look | safety_tier | site:e151e245 | 39,369 | 5 | 7.3 / 8 | 0.508 | 0/3 | 0 |
| look | safety_tier | app:92f5800b | 34,967 | 5 | 8.1 / 8 | 0.421 | 1/3 | 0 |
| look | safety_tier | site:5b08c53b | 11,238 | 3 | 1.9 / 4 | 0.752 | 0/3 | 0 |

### 3. Naive raw-CTR version of 'best creative differs' (pooled across surfaces, unadjusted, top-20 creatives, cells >= 200 imps)

| creative def | group | groups | raw best != overall raw best | ... gap significant (two-prop z, 95%) |
|---|---|---|---|---|
| tuple | genre | 10 | 2 | 1 |
| tuple | safety_tier | 3 | 0 | 0 |
| C14 | genre | 10 | 2 | 1 |
| C14 | safety_tier | 3 | 0 | 0 |
| look | genre | 10 | 0 | 0 |
| look | safety_tier | 3 | 1 | 1 |

### 4. Raw counts and CTRs, top creatives x genre (tuple definition, pooled over surfaces; unadjusted, so creative and genre mix are confounded here)

Creative index: #1 = `pos0 C14=21611 320x50 C17=2480 C18=3 C19=297 C20=100111 C21=61` (n=19,438); #2 = `pos1 C14=4687 320x50 C17=423 C18=2 C19=39 C20=100148 C21=32` (n=11,783); #3 = `pos0 C14=4687 320x50 C17=423 C18=2 C19=39 C20=100148 C21=32` (n=9,141); #4 = `pos0 C14=15705 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,812); #5 = `pos0 C14=15708 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,743); #6 = `pos0 C14=15701 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,721); #7 = `pos0 C14=15699 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,679); #8 = `pos0 C14=21189 320x50 C17=2424 C18=1 C19=161 C20=100193 C21=71` (n=7,568); #9 = `pos0 C14=15707 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,558); #10 = `pos0 C14=15704 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,556); #11 = `pos0 C14=15703 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,525); #12 = `pos0 C14=21191 320x50 C17=2424 C18=1 C19=161 C20=100193 C21=71` (n=7,426); #13 = `pos0 C14=15702 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,415); #14 = `pos0 C14=15706 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=7,347); #15 = `pos0 C14=20108 320x50 C17=2299 C18=2 C19=1327 C20=-1 C21=52` (n=7,316); #16 = `pos0 C14=23804 320x50 C17=2726 C18=3 C19=803 C20=-1 C21=229` (n=6,035); #17 = `pos0 C14=21767 320x50 C17=2506 C18=0 C19=35 C20=-1 C21=157` (n=5,318); #18 = `pos0 C14=21768 320x50 C17=2506 C18=0 C19=35 C20=-1 C21=157` (n=5,306); #19 = `pos0 C14=16615 320x50 C17=1863 C18=3 C19=39 C20=-1 C21=23` (n=4,747); #20 = `pos0 C14=19251 320x50 C17=2201 C18=3 C19=35 C20=-1 C21=43` (n=3,804)

| genre | n | CTR | raw-best creative | its CTR [95% CI] | overall raw-best creative (#2) CTR [95% CI] | best differs / gap significant |
|---|---|---|---|---|---|---|
| anime | 17,227 | 0.161 | #2 | 0.318 [0.294, 0.343] | 0.318 [0.294, 0.343] | no / no |
| comedic | 15,591 | 0.164 | #2 | 0.373 [0.346, 0.401] | 0.373 [0.346, 0.401] | no / no |
| fantasy | 13,849 | 0.161 | #2 | 0.336 [0.308, 0.365] | 0.336 [0.308, 0.365] | no / no |
| historical | 14,633 | 0.157 | #2 | 0.344 [0.316, 0.374] | 0.344 [0.316, 0.374] | no / no |
| horror | 17,547 | 0.242 | #3 | 0.359 [0.331, 0.388] | 0.350 [0.325, 0.376] | yes / no |
| mentor | 17,451 | 0.152 | #15 | 0.269 [0.236, 0.305] | 0.144 [0.125, 0.164] | yes / yes |
| mystery | 14,521 | 0.166 | #2 | 0.335 [0.308, 0.364] | 0.335 [0.308, 0.364] | no / no |
| romance | 16,172 | 0.201 | #2 | 0.384 [0.357, 0.412] | 0.384 [0.357, 0.412] | no / no |
| sci | 14,216 | 0.161 | #2 | 0.339 [0.312, 0.367] | 0.339 [0.312, 0.367] | no / no |
| slice | 15,031 | 0.162 | #2 | 0.343 [0.315, 0.371] | 0.343 [0.315, 0.371] | no / no |

Raw CTR grid, top 8 creatives x genre (n / CTR):

| creative | anime | comedic | fantasy | historical | horror | mentor | mystery | romance | sci | slice |
|---|---|---|---|---|---|---|---|---|---|---|
| #1 | 2,237 / 0.056 | 1,928 / 0.054 | 1,746 / 0.063 | 1,862 / 0.047 | 2,388 / 0.254 | 1,996 / 0.059 | 1,731 / 0.057 | 2,006 / 0.064 | 1,735 / 0.051 | 1,809 / 0.061 |
| #2 | 1,356 / 0.318 | 1,163 / 0.373 | 1,078 / 0.336 | 1,028 / 0.344 | 1,337 / 0.350 | 1,296 / 0.144 | 1,086 / 0.335 | 1,229 / 0.384 | 1,116 / 0.339 | 1,094 / 0.343 |
| #3 | 985 / 0.203 | 846 / 0.170 | 796 / 0.181 | 887 / 0.200 | 1,101 / 0.359 | 1,018 / 0.184 | 836 / 0.173 | 941 / 0.189 | 863 / 0.171 | 868 / 0.209 |
| #4 | 822 / 0.215 | 753 / 0.203 | 659 / 0.219 | 710 / 0.208 | 819 / 0.222 | 921 / 0.232 | 776 / 0.201 | 857 / 0.272 | 705 / 0.190 | 790 / 0.199 |
| #5 | 811 / 0.236 | 808 / 0.189 | 704 / 0.200 | 752 / 0.174 | 800 / 0.214 | 871 / 0.200 | 687 / 0.191 | 754 / 0.281 | 768 / 0.216 | 788 / 0.212 |
| #6 | 781 / 0.201 | 770 / 0.216 | 673 / 0.214 | 738 / 0.210 | 823 / 0.220 | 918 / 0.203 | 740 / 0.226 | 879 / 0.275 | 654 / 0.206 | 745 / 0.203 |
| #7 | 853 / 0.213 | 816 / 0.203 | 655 / 0.200 | 725 / 0.182 | 814 / 0.209 | 855 / 0.216 | 703 / 0.223 | 772 / 0.290 | 725 / 0.221 | 761 / 0.219 |
| #8 | 920 / 0.026 | 759 / 0.021 | 714 / 0.018 | 705 / 0.013 | 974 / 0.215 | 708 / 0.028 | 701 / 0.014 | 786 / 0.018 | 655 / 0.026 | 646 / 0.019 |

### 5. Within the biggest surface (site:1fbe01fe, 136,845 rows, 13 look creatives): adjusted best creative per genre vs overall best

Creatives: #1 = `0|320|50|1722|0|35|-1|79` (n=60,474); #2 = `0|320|50|1722|0|35|100084|79` (n=21,458); #3 = `0|320|50|1722|0|35|100083|79` (n=11,292); #4 = `0|320|50|2502|0|35|-1|221` (n=10,240); #5 = `0|320|50|2299|2|1327|-1|52` (n=7,253); #6 = `0|320|50|2299|2|1327|100084|52` (n=5,149); #7 = `0|320|50|2545|0|167|-1|221` (n=3,898); #8 = `0|320|50|2502|0|35|100084|221` (n=3,725); #9 = `0|320|50|2545|0|167|100084|221` (n=3,114); #10 = `0|320|50|2616|0|35|-1|51` (n=3,020); #11 = `0|320|50|2617|0|35|-1|51` (n=2,624); #12 = `0|320|50|2545|0|35|-1|221` (n=2,576); #13 = `0|320|50|2667|0|35|-1|221` (n=2,022).  Overall best = #9

| genre | n | CTR | adjusted best | log-odds gain over overall best [95% CI] | z | raw CTR of best [95% CI] | raw CTR of overall best [95% CI] |
|---|---|---|---|---|---|---|---|
| anime | 14,748 | 0.209 | = overall | - | - | 0.241 [0.203, 0.284] | 0.241 [0.203, 0.284] |
| comedic | 13,713 | 0.211 | = overall | - | - | 0.260 [0.212, 0.313] | 0.260 [0.212, 0.313] |
| fantasy | 12,165 | 0.208 | #7 | +0.215 [-0.173, +0.602] | 1.09 | 0.237 [0.196, 0.283] | 0.204 [0.160, 0.256] |
| historical | 12,833 | 0.203 | #7 | +0.076 [-0.285, +0.437] | 0.41 | 0.242 [0.201, 0.288] | 0.229 [0.185, 0.279] |
| horror | 14,616 | 0.212 | = overall | - | - | 0.239 [0.200, 0.283] | 0.239 [0.200, 0.283] |
| mentor | 15,747 | 0.211 | #5 | +0.009 [-0.332, +0.351] | 0.05 | 0.269 [0.236, 0.306] | 0.254 [0.205, 0.310] |
| mystery | 12,924 | 0.213 | = overall | - | - | 0.271 [0.223, 0.325] | 0.271 [0.223, 0.325] |
| romance | 14,193 | 0.284 | = overall | - | - | 0.352 [0.301, 0.407] | 0.352 [0.301, 0.407] |
| sci | 12,381 | 0.206 | = overall | - | - | 0.233 [0.188, 0.285] | 0.233 [0.188, 0.285] |
| slice | 13,525 | 0.206 | = overall | - | - | 0.252 [0.203, 0.307] | 0.252 [0.203, 0.307] |

### 5. Within the biggest surface (site:1fbe01fe, 97,354 rows, 21 tuple creatives): adjusted best creative per genre vs overall best

Creatives: #1 = `pos0 C14=20108 320x50 C17=2299 C18=2 C19=1327 C20=-1 C21=52` (n=7,253); #2 = `pos0 C14=15705 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,897); #3 = `pos0 C14=15708 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,866); #4 = `pos0 C14=15701 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,837); #5 = `pos0 C14=15699 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,832); #6 = `pos0 C14=15704 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,703); #7 = `pos0 C14=15707 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,650); #8 = `pos0 C14=15703 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,620); #9 = `pos0 C14=15702 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,562); #10 = `pos0 C14=15706 320x50 C17=1722 C18=0 C19=35 C20=-1 C21=79` (n=6,507); #11 = `pos0 C14=20108 320x50 C17=2299 C18=2 C19=1327 C20=100084 C21=52` (n=5,149); #12 = `pos0 C14=22676 320x50 C17=2616 C18=0 C19=35 C20=-1 C21=51` (n=3,020); #13 = `pos0 C14=15704 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,526); #14 = `pos0 C14=15705 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,471); #15 = `pos0 C14=15708 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,442); #16 = `pos0 C14=15699 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,421); #17 = `pos0 C14=15701 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,418); #18 = `pos0 C14=15707 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,357); #19 = `pos0 C14=15703 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,335); #20 = `pos0 C14=15702 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,311); #21 = `pos0 C14=15706 320x50 C17=1722 C18=0 C19=35 C20=100084 C21=79` (n=2,177).  Overall best = #1

| genre | n | CTR | adjusted best | log-odds gain over overall best [95% CI] | z | raw CTR of best [95% CI] | raw CTR of overall best [95% CI] |
|---|---|---|---|---|---|---|---|
| anime | 10,674 | 0.216 | #11 | +0.118 [-0.112, +0.348] | 1.00 | 0.265 [0.233, 0.300] | 0.240 [0.214, 0.269] |
| comedic | 9,728 | 0.211 | #11 | +0.101 [-0.166, +0.368] | 0.74 | 0.279 [0.240, 0.321] | 0.260 [0.229, 0.294] |
| fantasy | 8,625 | 0.214 | #16 | +0.250 [-0.121, +0.620] | 1.32 | 0.279 [0.221, 0.344] | 0.259 [0.227, 0.294] |
| historical | 9,061 | 0.206 | #15 | +0.424 [+0.085, +0.763] | 2.45 | 0.305 [0.251, 0.365] | 0.232 [0.202, 0.266] |
| horror | 10,795 | 0.220 | #18 | +0.071 [-0.255, +0.397] | 0.43 | 0.270 [0.218, 0.329] | 0.269 [0.244, 0.297] |
| mentor | 10,955 | 0.214 | #15 | +0.033 [-0.291, +0.357] | 0.20 | 0.271 [0.225, 0.323] | 0.269 [0.236, 0.306] |
| mystery | 9,238 | 0.216 | #14 | +0.094 [-0.261, +0.449] | 0.52 | 0.284 [0.229, 0.348] | 0.274 [0.241, 0.310] |
| romance | 10,060 | 0.283 | = overall | - | - | 0.323 [0.290, 0.359] | 0.323 [0.290, 0.359] |
| sci | 8,756 | 0.210 | = overall | - | - | 0.256 [0.224, 0.291] | 0.256 [0.224, 0.291] |
| slice | 9,462 | 0.208 | #18 | +0.101 [-0.242, +0.444] | 0.58 | 0.281 [0.229, 0.340] | 0.273 [0.240, 0.309] |

### 6. Held-out value of tailoring (within surface; pick each group's best creative on half the characters, score on the other half vs the half-A overall best; 10 random character splits x 2 directions)

| creative def | group | surfaces | in-sample gain (log-odds / CTR pp) | held-out gain log-odds (mean; SD across evals; mean Wald SE) | held-out gain CTR pp (mean; SD) | share of evals with gain > 0 |
|---|---|---|---|---|---|---|
| tuple | genre | 3 | +0.216 / +3.05 | -0.0616; 0.0614; 0.0624 | -0.776; 1.049 | 0.15 |
| tuple | safety_tier | 3 | +0.051 / +0.72 | -0.0072; 0.0419; 0.0522 | +0.108; 0.712 | 0.45 |
| look | genre | 4 | +0.118 / +1.59 | -0.1054; 0.0330; 0.0497 | -1.402; 0.544 | 0.00 |
| look | safety_tier | 4 | +0.034 / +0.43 | -0.0075; 0.0303; 0.0384 | -0.365; 0.486 | 0.45 |

### 6b. Out-of-sample log-likelihood: does the creative x group model beat the additive one on held-out characters? (pooled primary design, 10 random character splits x 2 directions)

| creative def | group | held-out LL per impression, additive | ... with interaction | gain per 10k impressions (mean; SD) | share of evals where interaction wins |
|---|---|---|---|---|---|
| tuple | genre | -0.46566 | -0.46753 | -18.76 (5.41) | 0.00 |
| tuple | safety_tier | -0.43658 | -0.43679 | -2.12 (0.77) | 0.00 |
| look | genre | -0.47853 | -0.48053 | -20.01 (12.24) | 0.00 |
| look | safety_tier | -0.46824 | -0.46857 | -3.31 (4.07) | 0.05 |

### 7. Power: planted interaction (+delta log-odds on one random creative per genre), tuple x genre, pooled primary design, simulated clicks from the fitted null (independent impressions), 8 reps per delta

| delta (log-odds) | approx. CTR pp at 17% base | LR test reject rate (alpha 0.05) | mean excess LR stat over df |
|---|---|---|---|
| 0 | 0.00 | 0.12 | 12.4 |
| 0.05 | 0.71 | 0.25 | 11.9 |
| 0.1 | 1.41 | 0.25 | 11.2 |
| 0.2 | 2.82 | 0.50 | 25.6 |
| 0.3 | 4.23 | 0.75 | 38.6 |

# Measurement (b) tables: bundle B

Requests: 2000 random held-out (test-window) rows; menu: 20 most common training-window candidate tuples (10 distinct looks = tuples without C14; ten items are one ad under 10 C14 ids).

### B1. How often does the top-scoring creative change when only the character changes?

Columns: change = share of (request, character-pair) cases where argmax creative differs; rho = mean Spearman correlation of the creative score vectors; regret = mean relative pCTR loss (under the first character's own scores) from using the second character's top creative, over all cases / over changed cases; >=2% = share of cases where that loss is at least 2%.

| comparison | menu K | n cases | top-1 change | mean rho (median) | regret all / if changed | share with regret >= 2% |
|---|---|---|---|---|---|---|
| actual character -> a random character of another genre (any tier) | 20 | 54,000 | 0.376 | 0.921 (0.982) | 0.0336 / 0.0894 | 0.330 |
| CONTROL: actual character -> another character, same genre and tier | 20 | 2,000 | 0.000 | 1.000 (1.000) | 0.0000 / 0.0000 | 0.000 |
| character pairs: same tier, different genre | 20 | 270,000 | 0.104 | 0.943 (0.994) | 0.0079 / 0.0756 | 0.074 |
| CONTROL: two different characters, same genre and tier | 20 | 60,000 | 0.000 | 1.000 (1.000) | 0.0000 / 0.0000 | 0.000 |
| character pairs: same genre, different tier | 20 | 60,000 | 0.503 | 0.979 (0.988) | 0.0414 / 0.0822 | 0.468 |
| feature-only: swap just the genre value (all genre pairs) | 20 | 90,000 | 0.120 | 0.942 (0.994) | 0.0091 / 0.0757 | 0.085 |
| feature-only: swap just the safety_tier value (all tier pairs) | 20 | 6,000 | 0.506 | 0.978 (0.988) | 0.0413 / 0.0818 | 0.472 |
| actual character -> a random character of another genre (any tier) | 10 | 54,000 | 0.221 | 0.861 (0.951) | 0.0156 / 0.0707 | 0.178 |
| CONTROL: actual character -> another character, same genre and tier | 10 | 2,000 | 0.000 | 1.000 (1.000) | 0.0000 / 0.0000 | 0.000 |
| character pairs: same tier, different genre | 10 | 270,000 | 0.120 | 0.902 (1.000) | 0.0074 / 0.0618 | 0.086 |
| CONTROL: two different characters, same genre and tier | 10 | 60,000 | 0.000 | 1.000 (1.000) | 0.0000 / 0.0000 | 0.000 |
| character pairs: same genre, different tier | 10 | 60,000 | 0.225 | 0.920 (0.973) | 0.0111 / 0.0493 | 0.192 |
| feature-only: swap just the genre value (all genre pairs) | 10 | 90,000 | 0.145 | 0.895 (1.000) | 0.0088 / 0.0609 | 0.103 |
| feature-only: swap just the safety_tier value (all tier pairs) | 10 | 6,000 | 0.219 | 0.920 (0.974) | 0.0107 / 0.0490 | 0.187 |
| actual character -> a random character of another genre (any tier) | 5 | 54,000 | 0.170 | 0.873 (0.975) | 0.0117 / 0.0691 | 0.140 |
| CONTROL: actual character -> another character, same genre and tier | 5 | 2,000 | 0.000 | 1.000 (1.000) | 0.0000 / 0.0000 | 0.000 |
| character pairs: same tier, different genre | 5 | 270,000 | 0.078 | 0.920 (1.000) | 0.0049 / 0.0627 | 0.059 |
| CONTROL: two different characters, same genre and tier | 5 | 60,000 | 0.000 | 1.000 (1.000) | 0.0000 / 0.0000 | 0.000 |
| character pairs: same genre, different tier | 5 | 60,000 | 0.197 | 0.905 (0.975) | 0.0090 / 0.0456 | 0.167 |
| feature-only: swap just the genre value (all genre pairs) | 5 | 90,000 | 0.095 | 0.912 (1.000) | 0.0058 / 0.0618 | 0.072 |
| feature-only: swap just the safety_tier value (all tier pairs) | 5 | 6,000 | 0.192 | 0.907 (0.975) | 0.0087 / 0.0456 | 0.162 |

What the model separates: by exact score it has 8 genre classes [['anime', 'fantasy', 'slice'], ['comedic'], ['historical'], ['horror'], ['mentor'], ['mystery'], ['romance'], ['sci']] and tiers [['sfw', 'suggestive'], ['mature']], but in top-creative terms only horror, mentor and romance differ from the other seven genres (flip rate between any two of the seven is <= 0.003). The 24 genre pairs with a real flip rate average 0.225 (range 0.14 to 0.34, K=20); averaged over all 45 pairs it is 0.120. `creator_type` and `character_age_days` have zero split gain, which is why two different characters of the same genre and tier never change anything (the control rows are exactly 0).

Counting only changes of the creative look (not a switch among the 10 C14-id twins), K=20: genre pairs the model separates mean 0.122 (range 0.000 to 0.305); mature vs sfw 0.750; actual -> other genre, same tier 0.122; actual -> other tier, same genre 0.429. Share of requests where all 10 genres get the same top look: 0.568 (same top tuple: 0.549).

Request-level 95% intervals (unit = request, K=20; mean [low, high]):

- genre feature only tuple level 42 separated pairs: 0.129 [0.122, 0.136]
- genre feature only look level 42 separated pairs: 0.122 [0.115, 0.129]
- tier feature only mature vs sfw tuple level: 0.758 [0.740, 0.777]
- tier feature only mature vs sfw look level: 0.750 [0.731, 0.769]
- actual to other genre same tier tuple level: 0.129 [0.118, 0.140]
- actual to other genre same tier look level: 0.122 [0.111, 0.132]
- actual to other tier same genre look level: 0.429 [0.417, 0.442]
- control same genre tier other character: 0.000 [0.000, 0.000]

Genre-pair top-change matrix (feature-only swap, K=20, tuple level):

| | anime | comedic | fantasy | historical | horror | mentor | mystery | romance | sci | slice |
|---|---|---|---|---|---|---|---|---|---|---|
| anime | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |
| comedic | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |
| fantasy | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |
| historical | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |
| horror | 0.14 | 0.14 | 0.14 | 0.14 | 0.00 | 0.30 | 0.14 | 0.32 | 0.14 | 0.14 |
| mentor | 0.21 | 0.21 | 0.21 | 0.21 | 0.30 | 0.00 | 0.21 | 0.34 | 0.21 | 0.21 |
| mystery | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |
| romance | 0.28 | 0.28 | 0.28 | 0.28 | 0.32 | 0.34 | 0.28 | 0.00 | 0.28 | 0.28 |
| sci | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |
| slice | 0.00 | 0.00 | 0.00 | 0.00 | 0.14 | 0.21 | 0.00 | 0.28 | 0.00 | 0.00 |

Tier-pair top-change (feature-only, K=20): sfw vs suggestive change 0.000, rho 1.000; sfw vs mature change 0.758, rho 0.967; suggestive vs mature change 0.758, rho 0.967

### B2. Size of the interaction in the model (logit scale, feature-only swaps, per-request double-centred over character value x creative)

| character feature | SD creative main effect | SD character main effect | SD creative x character interaction | interaction / creative |
|---|---|---|---|---|
| genre | 0.432 | 0.223 | 0.084 | 0.19 |
| safety_tier | 0.428 | 0.059 | 0.033 | 0.08 |

Creative spread under the actual character: SD of logit score across the 20 creatives 0.405; mean pCTR of top creative 0.2243 vs median creative 0.1823; mean relative gap top-1 to top-2 0.042; share of requests with gap < 2%: 0.345.

### B3. Can the model express creative x character interactions? Tree inspection (183 trees)

- Feature set B contains genre, safety_tier, creator_type, character_age_days next to banner_pos and C14-C21; GBDT trees (31 leaves, no interaction constraints) can split on both along one path, so it can express them.
- 55.7% of trees contain a root-to-leaf path with both a character feature and a candidate feature; 12.1% of all splits and 10.0% of total split gain sit on such a path (a split counts if one of its ancestors is from the other group).
- Share of total gain by character feature: genre 5.18%, safety_tier 0.43%, creator_type 0.00%, character_age_days 0.00%. Top features by gain: site_id 20.8%, app_id 15.1%, device_model 13.4%, C21 11.6%, site_domain 11.4%, C17 10.4%, C14 7.7%, genre 5.2%.
- What it learned: genres split into the classes above (horror, romance, mentor separate; most others tied), tier into mature vs not. Per-genre top-creative win shares (feature-only swap) are in b_results.json.

### B4. Does the model's character-effect heterogeneity hold up on held-out rows? (all test-window rows, actual character and creative)

h_i = model's effect of the character feature on row i's logit pCTR (actual value vs reference: mature->sfw; horror/romance/mentor->anime class), centred in the group, split into surface-level, creative-within-surface and residual (device/hour/...) parts. Offset = model logit with that heterogeneity removed; coefficient a on each part (1 = real, 0 = not; Wald 95% CI).

| subset | rows | (surface, creative) cells | part | SD of part | a | 95% CI |
|---|---|---|---|---|---|---|
| mature rows: model's mature-vs-sfw effect, all cells | 19,803 | 6,584 | surface level | 0.220 | 1.22 | [1.06, 1.39] |
| mature rows: model's mature-vs-sfw effect, all cells | 19,803 | 6,584 | creative within surface | 0.059 | 0.72 | [0.12, 1.31] |
| mature rows: model's mature-vs-sfw effect, all cells | 19,803 | 6,584 | residual (device, hour, ...) | 0.051 | 0.16 | [-0.54, 0.86] |
| horror/romance/mentor rows: effect vs 'anime' class, all cells | 42,503 | 10,869 | surface level | 0.324 | 1.15 | [1.06, 1.23] |
| horror/romance/mentor rows: effect vs 'anime' class, all cells | 42,503 | 10,869 | creative within surface | 0.169 | 1.20 | [1.04, 1.36] |
| horror/romance/mentor rows: effect vs 'anime' class, all cells | 42,503 | 10,869 | residual (device, hour, ...) | 0.369 | 1.30 | [1.22, 1.38] |
| mature rows, (surface, creative) cells with >= 30 test rows | 10,979 | 598 | surface level | 0.206 | 1.09 | [0.87, 1.32] |
| mature rows, (surface, creative) cells with >= 30 test rows | 10,979 | 598 | creative within surface | 0.042 | 0.34 | [-0.77, 1.45] |
| mature rows, (surface, creative) cells with >= 30 test rows | 10,979 | 598 | residual (device, hour, ...) | 0.059 | 0.05 | [-0.74, 0.84] |
| horror/romance/mentor rows, (surface, creative) cells with >= 30 test rows | 23,519 | 600 | surface level | 0.384 | 1.05 | [0.96, 1.15] |
| horror/romance/mentor rows, (surface, creative) cells with >= 30 test rows | 23,519 | 600 | creative within surface | 0.085 | 1.36 | [0.96, 1.76] |
| horror/romance/mentor rows, (surface, creative) cells with >= 30 test rows | 23,519 | 600 | residual (device, hour, ...) | 0.453 | 1.32 | [1.23, 1.41] |
