# Rewarded-ad bible (proposer knowledge file)

Version 1.1, built 2026-09-27 (verification pass applied). Facts carry a source id in brackets, like [F01]. Every id resolves in `bible/SOURCES.md` with URL, date, and a verbatim quote. Tier tags: **P** primary (the company's own page), **V** vendor (ad network or MMP, sells ads), **S** secondary (press, blogs), **OLD** (dated before 2026-03-27 or historical). Numbers for code live in `bible/data/*.json`.

## 0. How to use this file

You get this file plus one app's product model (and its value ledger: limits, meters, currencies, paywall bullets, actors). Your job: invent rewarded-ad opportunities that fit **that** app.

- Product facts come only from the product model. Nothing here describes the app you are working on. If the model doesn't show a limit, a price, or a screen, you don't know it. Say "not observed" instead of guessing.
- The catalog (section 6) is for analogy, not copying. For each idea, name the mechanic you adapted (`adapted_from`: an M-id, or "none") and say what makes it specific to this app (`what_is_specific_here`: product-model element ids). A one-to-one port of a catalog row with the name changed is a weak idea.
- Every idea needs a `reward_kind` (section 4) with its cost fields filled in. Code computes the cost and break-even eCPM from those. Don't hide an inference reward under "cosmetic". Code checks.
- "No good opportunity here" is an allowed answer for any lens.

## 1. Anatomy of a rewarded exchange

A rewarded ad is one the user **chooses** to watch or play in exchange for something they value. Every idea must fill these eight slots. Each slot has a failure mode, listed next to it.

| Slot | What it is | Good | Failure |
|---|---|---|---|
| Anchor | The scarce thing the user already wants | A limit the user hits, a paid feature, a locked item, a currency | Nothing is scarce, so the reward is worthless |
| Trigger | The app event that shows the offer | The moment of need: out of lives, out of energy, a locked episode, a paywall closed [B07] | Mid-task interruption; a trigger inferred from chat content (Simula can't see it [G15]) |
| Offer | The copy shown before the ad | States the action and the reward, every time [F02][F10] | Vague ("watch to unlock goodies") |
| Reward | What the user gets, with size and duration | Small, partial, temporary; smaller than the paid version [C02][C03] | Gives away the subscription's main benefit |
| Cap | How often it can repeat | A daily count with a reset time [C04][C08] | Unlimited, which inflates the economy [H20] |
| Decline path | What happens on "no", on skip, and on no-fill | The app is exactly as usable as before [F05]; the offer hides when no ad is available [C15] | "No" costs the user something; no-fill eats the attempt [H17] |
| Payer treatment | What paying users see | Payers don't need it, but can still opt in [B20] | Payers get ad offers for what they bought |
| Grant | When the reward lands | On Simula's `REWARD_VERIFIED` [G03] | On click, close, or `EARNED_REWARD` |

What well-performing placements look like, per Google: "over 50% of Daily Active Users engaging, and an 80% completion rate" [B02, P]. Users opt in to rewarded ads 20-40% more than to other formats [B01, P].

## 2. Rules (the policy floor)

Google's AdMob policy is the floor most networks follow. Quote it when judging.

| Rule | Source quote |
|---|---|
| Opt-in first | "Rewarded Ads must only be served after a user affirmatively and unambiguously opts in" [F01, P] |
| State the reward before every ad | "clear, accurate and conspicuous disclosure of the action(s) required and reward(s) offered prior to each instance" [F02, P] |
| Skippable | "it must be possible to skip or dismiss them" [F03, P] |
| No cash | "Direct monetary items may not be offered as rewards under any circumstance" [F04, P] |
| Declining leaves the app usable | "Skipping the Rewarded Ad or selecting the 'no' or 'don't accept' option must not impede or interfere with the normal usage" [F05, P] |
| Deliver the reward | "Publishers must deliver the promised reward(s) to the user upon completion of the required action(s)" [F06, P] |
| Rewarded interstitial (no opt-in tap) needs an intro screen | "rewarded interstitials require an intro screen that announces the reward and gives users a chance to opt-out" [F08, P] |
| Google Play exempts opt-in rewarded ads from its disruptive-ad rules | "This policy does not apply to rewarded ads which are explicitly opted-in by users" [F19, P] |
| Apple allows ad incentives, bans store-action incentives | "Apps may otherwise incentivize users to take specific actions within apps (e.g. completing a level, watching an ad)" [F13, P, Guideline 3.2.2(x)] |
| Apple: paid unlocks go through IAP | "Apps may not use their own mechanisms to unlock content or functionality" [F12, P]. A rewarded path is fine; the *paid* path to the same thing must be IAP. |
| No incentives for ratings, reviews, installs | Google: "fraudulent or incentivized reviews and ratings" are banned [F21, P]; Apple: same in 3.2.2(x) [F13] |
| Don't gate on tracking consent | "You cannot gate functionality on agreeing to allow tracking" [F14, P] |
| Kids | Apple Kids Category: "should not include third-party analytics or third-party advertising" [F17, P]. US COPPA: verifiable parental consent before collecting a child's data [F28, P]; the 2025 rule update (enforced from 2026-04-22) requires separate consent per third party for targeted ads [H19, P]. |
| Don't reward raw screen time | The EU forced TikTok Lite's "Tasks and Rewards" (points with monetary value for time in app) off the EU market in 2024, the first case closed under the DSA [H01][H02][H03, S, OLD]. Reward a discrete completed action, never time spent. |

**Format choice.** Rewarded (opt-in tap, then ad) is the default. Rewarded interstitial (shown at a natural transition with a skip intro) is allowed but weaker on consent; use it only where a transition already exists. Offerwall = a menu of ways to earn (ads, surveys, tasks); eCPMs are high ($400-530 per 1,000 offerwall impressions [D06, V]) but it invites install-for-reward fraud [H15, S].

## 3. Simula (who this is for)

Public facts only, read 2026-09-27.

- **Formats.** React Native SDK: "Native Ad", "Interstitial Ad", "Rewarded Ad | Play-to-earn ad with server-side reward verification", "Character Selector | Pre-built character discovery UI" [G05, P]. Kotlin and Swift SDKs: native slot, interstitial, rewarded [G06][G07, P]. Web/React SDK (`@simula/ads`): "In-Chat Ad Slots", "Native Banner Ads", "Mini Games - Playable mini game experiences with ad monetization" [G01][G02, P]. The rewarded unit is a **playable**: the user plays a short sponsored game, not only watches a video.
- **Events.** `LOADED`, `LOAD_FAILED`, `DISPLAYED`, `IMPRESSION`, `DISPLAY_FAILED`, `CLICKED`, `PAID`, `CLOSED`, plus reward events `EARNED_REWARD`, `REWARD_VERIFIED`, `REWARD_VERIFICATION_FAILED` [G09, P].
- **Grant rule.** "Grant rewards on REWARD_VERIFIED, not EARNED_REWARD." [G03, P]. "do not infer a reward from CLICKED or CLOSED" [G04, P]. `EARNED_REWARD` is the client saying the play threshold was met; `REWARD_VERIFIED` is Simula's server confirming it. An explicit `verified: false` denies the reward for good; a malformed response stays retryable [G10, P]. (Other networks tell apps to grant at once and check later [F24, V]. For Simula, follow Simula.)
- **Every idea must handle three branches:** verified (grant), verification failed (no grant, say so kindly, don't charge the attempt), load failed or no fill (hide the offer or show the unchanged app; no penalty).
- **What Simula sees.** "We do not receive the content of your conversations or chats within Publisher Apps, including any messages exchanged with AI characters." [G15, P]. It gets device data, IP-level country or region [G16][G17, P], an app-supplied hashed user id and an ad context object (search terms, tags, category, title, NSFW flag, custom JSON) [G23, P], and at most 10 metadata key-value pairs per ad load, no PII [G20, P]. **So triggers must be app events** (limit hit, paywall closed, locked item tapped, session count), never "when the user talks about X".
- **Consent plumbing exists**: TCF, CCPA, GPP, COPPA flag, ATT [G07][G23, P]. A child-directed app is out of scope for targeted rewarded ads [F17][H19].
- **Their own demos.** `uno-multiplayer` ("rewarded-mini-games"): a "Reward ad 'Watch to Block' penalty system" in an UNO game [G14, P]: the ad protects the player from a loss rather than granting a gain. `flutter-mock-public`: a mock "styled to look like the CHAI app" with a native ad slot every 3rd feed position [G12, P]. Their web mocks are v0 exports [G11][G13, P].
- **Positioning.** "Fixing advertising for good" [G25, P]; building into AI apps "covering 5M+ DAUs and 100M+ downloads" [G24, P]. LinkedIn: "The AdTech layer for consumer AI, starting with gaming and entertainment." [R03, P]
- **Publisher dashboard** at publisher.simula.ad: ad units, analytics, server-side verification setup [G22, P].

## 4. Economics: does the ad pay for the reward?

This is the AI-specific fact. In a game, an extra life costs the publisher nothing. In an AI app, most rewards are inference someone pays for. Code computes, per idea:

`cost = cost of one grant (by reward_kind)`; `break_even_eCPM = 1000 × cost / views_per_grant`.
Verdict: **PASS** if break-even under central assumptions ≤ the low benchmark for the app's main region and platform; **CONDITIONAL** if break-even under favorable assumptions ≤ the high benchmark; else **FAIL**. Only FAIL blocks. Put the assumption on the slide: "Pays for itself above $X eCPM, or if your serving cost is below $Y per reply."

The product model rarely shows where the users are. If it doesn't, don't guess a region: report the verdict for North America and for LATAM (the top and bottom of the table) and say which audience the idea needs.

### 4.1 What one rewarded view earns (USD per 1,000 completed views)

| Region | Android low / central / high | iOS low / central / high |
|---|---|---|
| North America | 9.20 / 10.16 / 16.49 | 13.90 / 16.33 / 19.63 |
| Europe | 5.10 | 8.80 |
| APAC | 8.20 | 7.50 |
| Middle East | 2.30 | 8.40 |
| LATAM | 1.90 | 3.75 |

Low = Appodeal data via Mistplay (page dated 2026-03-13, data updated Q4 2024) [D02, V]. NA central = Business of Apps, updated 2026-09-25 [D15, V]; NA high = Playio, 2026-05-18 [D13, S]. Single-source regions have one number. Country averages (both platforms): US 15.15, Australia 13.80, Japan 10.80, UK 10.65 [D03, V]. For comparison, NA interstitial is $9.70 Android / $13.60 iOS [D04], NA banner $0.55 / $0.35 [D05, V]. No public Simula eCPM exists; its direct-deal playables may earn more. Treat that as upside, not the base case. **All ad-revenue data here is vendor-reported and mostly from games.** Non-game rewarded economics are much less measured.

So one completed view earns about **$0.009 in North America on Android, $0.005 in Europe, $0.002 in LATAM.**

### 4.2 What one grant costs, by reward kind (per 1,000 grants, favorable / central / unfavorable)

| reward_kind | Cost term | Per 1,000 grants | Break-even reading |
|---|---|---|---|
| `inference`, 1 reply | tokens × price | $0.20 / $1.60 / $13.00 | Central: one NA Android view pays for ~5.7 replies; one LATAM view for ~1.2 |
| `inference`, 5 replies | same | $0.99 / $8.00 / $65.00 | Central PASS in NA/APAC, CONDITIONAL in Europe/LATAM |
| `image`, 1 image | $/image | $25 / $34 / $67 | **FAIL everywhere at 1 view.** One image costs ~3-4 NA views |
| `voice`, 3 min | $/min | $40 / $135 / $270 | FAIL. Voice is the most expensive reward |
| `feature_time`, 30 min ad-free | minutes × ads/min × displaced eCPM | $1.65 / $58 / $204 | CONDITIONAL at best: pays only if the ads it removes are banners. If the app runs interstitials, one view doesn't cover them |
| `content_unlock`, 1 unit at $0.30 | p(would pay) × price | $3 / $6 / $15 | PASS in NA and APAC, CONDITIONAL in Europe, FAIL in LATAM; the cost is lost sales, not serving |
| `currency`, 10 coins at $0.01 | p(would pay) × value | $1 / $2 / $5 | PASS (CONDITIONAL in LATAM); watch inflation, not cost |
| `cosmetic`, `streak_protection`, `queue_priority` | ~0 | $0 | PASS; queue priority has a real but unknown peak-capacity cost |

Verdicts above are for Android; `breakeven.py` is the source of truth. Inputs (in `data/economics.json`): prices from live pricing pages read 2026-09-27: favorable = DeepInfra Llama-3.3-70B $0.10 in / $0.32 out per million tokens [E18, P]; central = OpenAI gpt-5-mini $0.25 / $2.00 [E05, P]; unfavorable = Claude Haiku 4.5 $1 / $5 [E01, P]. Tokens per reply: no measured source was found (two candidate sources failed verification). Central 4,000 in / 300 out is an assumption: a roleplay app sends the character definition plus recent history with each request, so input dwarfs output. Replace it with the app's real numbers when known. Prompt caching cuts cached input by ~90% [E02][E05, P]. Images: FLUX dev $0.025 [E21, P], Gemini Flash Lite Image $0.034 [E13, P], Gemini Flash Image $0.067 [E12, P]. Voice: OpenAI tts-1 $15 per million characters [E09, P], ElevenLabs $0.05-0.10 per 1,000 characters [E22, P], at an assumed 900 characters per spoken minute. Purchase probability: "less than 2% of app users make in-app purchases" (Unity 2024, via Verve) [D26, V].

The guide's earlier worked number still reproduces: 5 replies at 2,000 in / 300 out at $1 / $5 = $0.0175, break-even $17.50 (checked by `breakeven.py`).

### 4.3 Cost term by category

- **AI chat:** the reward is the cost (messages, images, voice). Prefer rewards that cost no fresh inference: a cosmetic, a badge, a saved slot, streak protection, a queue skip, a model tier the app already serves cheaply, or a *partial* top-up.
- **Content, news, reading:** the cost is displaced ads (ad-free time means ads not shown) or a paid unlock given away. Reading apps give the ad path **shorter access** than the paid path for the same item: WEBTOON ad-unlocked episodes re-lock after 3 days, while coin-unlocked ones stay [C02][C03, P]; Tapas: 72 hours, 3 per day [C04, P].
- **Games:** marginal cost ≈ 0. The risk is economy inflation [H20] and IAP cannibalization.
- **Subscription feature:** cost = the benefit's value × the chance the user would have paid. Keep it partial (a taste, not the thing) and temporary.

### 4.4 What the economics imply

1. A reward that costs fresh inference needs a high-eCPM region, cheap serving, or several views per grant. State which.
2. Image and voice rewards fail at one view per grant under every listed price. Either require more plays per grant, grant a lower-cost variant (smaller resolution, cached image, shorter clip), or pick a different reward.
3. Ad-free time is expensive if the app runs full-screen ads: 30 minutes can displace several interstitials, each worth about as much as the one rewarded view.
4. Cheapest rewards: things the app already has and that cost nothing per use (cosmetics, visibility, protection from a loss, a skip of an artificial wait).
5. Offering a rewarded path does not by itself grow IAP. Vendors report engagers are 4x more likely to buy [B09][D07, V] and 4.5x in a 2017 study [H13, V, OLD], but those are correlations among users who were already engaged [H13]. Don't argue "it will lift subscriptions" as fact.

## 5. Two kinds of opportunity

### 5.1 Existing anchor: the app already has something scarce

Look in the value ledger for: a limit with a counter or reset (messages, energy, generations), a wait (slow mode, regen timer, "wait until free"), a paid feature listed on a paywall, a locked item with a price, a currency, a loss the user can suffer (streak, penalty, failed attempt).

| Pattern | Real example | Safe when |
|---|---|---|
| Refill at the limit | Duolingo Energy: "watch a rewarded ad to earn back some Energy, or buy an Energy refill using gems" [C01, P]; Royal Match lives [B17, P]; Subway Surfers City revives [B19, P] | The refill is partial, the cap is daily, and the full refill stays paid |
| Temporary taste of a paid feature | Grindr "Rewarded Video": free users watch to temporarily unlock a boost or filter [A11, S]; ad revenue grew 37% to $74M in FY2025 with rewarded video expansion cited [A12, S] | Time-boxed, smaller than the plan, and the plan offers more than this one feature |
| Short-lived unlock of a locked item | WEBTOON Ad Pass 3 days [C02, P]; Tapas 72 h, 3 per day [C04, P]; Pocket FM episode unlock [C07, P] | The paid path gives permanent access; the ad path gives temporary access |
| Currency top-up | Wattpad Bonus Coins, 1 per full ad, up to 10 a day, expiring in 30 days [C08][C09, P] | There's a sink worth spending on; earned coins expire or are capped |
| Skip a wait | Character.AI lets quest-earned Charms "skip slow-mode" [R01, P] | The wait exists for a real reason (load), not only to sell skips |
| Protect from a loss | Simula's own "Watch to Block" penalty in UNO [G14, P]; revives [B19] | The loss is real to the user and the protection is one-time |
| Paywall declined → rewarded taste | The assignment's own example; Google Offerwall puts "rewarded ad / survey / micropayment / newsletter" on one choice screen [C14, P] | Shown only after the user says no to paying; never before |

Payer rule from Wordscapes: even after buying ad removal, players "will still have opportunities to voluntarily watch video ads for in-game rewards such as coins and hints" [B20, P]. Opt-in rewarded offers can stay for payers; forced ads can't.

### 5.2 Product change: create the scarcity or surface

Only when no anchor exists. Adding a constraint the user feels is the riskiest change (section 7).

| Change | Real example | Conditions for it to be safe |
|---|---|---|
| New depletable resource | Duolingo replaced Hearts with Energy, which drains on every answer, and kept a rewarded-ad refill [C01, P, OLD] (Hearts had one too [A2-11, S]), so the change was the resource, not the ad; users with long streaks called it "a money grab" [H05][H06, S] | Grandfather existing users or make the new limit looser than the old one for most people; never frame it as a benefit if it's a tighter cap |
| New choice screen at an existing wall | Google Offerwall: after N free articles, reader picks an ad, a survey, a micropayment, or signup; the ad option hides when there's no fill; the wall can be dismissed a set number of times [C14][C15][C16, P]. GA on AdSense 2026-04-13 | Metered; dismissible; no-fill handled |
| Opt out of ads by accepting less | ChatGPT free users can "opt out of ads in the Free tier in exchange for fewer daily free messages" [R02, P][A09, S] | The trade is explicit and reversible |
| Ad-free window bought with one ad | Spotify Sponsored Session: a 15-30 s video for 30 min ad-free (2014) [C11, OLD]; Trivia Star: 5 ads for 24 h ad-free [B28, OLD] | Only when displaced ad value is low (banners, audio ads spaced minutes apart); see 4.4 |
| Paid feature, one session, after a search | Pandora Sponsored Listening: video ad for one on-demand session after searching a song (2017) [C12, OLD] | The trigger is an explicit request the free tier can't fulfill |
| New earnable currency with quests | Character.AI Charms: earned by daily login, notifications on, persona, posting; spent on ad bypass, slow-mode skip, extra images [R01, P] | Watch the loop: if a currency earned by ads can buy ad skips, one ad buys the right to skip others |
| Daily bonus slot | Hay Day "Night at the Movies", 4 ads a day, random reward [B24, OLD]; 8 Ball Pool 1 a day [B25, OLD]; Choices diamonds 3 a day [B26, OLD] | Capped, and the bonus is additive, not a cut from the old free amount |
| Two-sided / creator reward | Twitch: US viewers could pick "Watch Ad" under "Get Bits", watch a 30-second ad, and receive 5-100 Bits, a currency they then chose to spend cheering a streamer, with a daily limit on ads (Twitch's own blog, 2016-10-06) [A2-23, P, OLD]; a 2026 guide says it still runs in some countries [A2-18, S]. Webnovel: ads earn Spirit Stones spent on votes that rank a novel [A2-17, S]. In both, the viewer earns a currency and chooses to spend it on a creator; the Twitch one reorders no list, and Webnovel's votes feed a vote leaderboard. There's no inference cost. Contrast: Kick's ads pay creators only; viewers get nothing [A2-19, P] | **Gate: only when the app has no anchor (5.1).** The viewer earns something for their own use first; support lands in a labeled slot or leaderboard that exists to show it, never by reordering a list the app ranks or curates on other grounds |

## 6. Mechanics catalog

Full rows (trigger, offer, reward, duration, cap, decline, payers, results, claim ids) are in `data/mechanics.json`. Status: live = live page or source dated after 2026-03-27; OLD = historical or not re-confirmed.

| id | App (category) | Anchor → reward | Cap | Payers | Kind | Status |
|---|---|---|---|---|---|---|
| M01 | ChatGPT (AI assistant) | Ads on free tier → opt out by taking fewer daily messages | toggle | Plus and up never see ads | product change | live [A08][A09] |
| M02 | Grindr (dating) | Paid boost or filter → temporary unlock per rewarded video | unstated | subscribers have it | existing | live [A11][A12] |
| M05 | Character.AI (AI companion) | Charms from quests (not ads) → bypass ads, skip slow mode, extra images | Charms earned | lite: fewer ads; c.ai+: none | product change | live [A01][R01] |
| M06 | Royal Match (game) | Lives (5, or 8 with pass) → a life or booster | unconfirmed | pass raises cap | existing | live [B17] |
| M07 | Subway Surfers City (game) | Revive after crash → 1 revive, stockpile no limit | none | buy revives | existing | live [B18][B19] |
| M08 | Wordscapes (game) | Stuck on a puzzle → coins or a hint | unconfirmed | ad-removal buyers still offered | existing | live [B20] |
| M10 | Subway Surfers classic (game) | First run done → 3 keys; double coins | per placement | n/a | existing | OLD [B23] |
| M11 | Hay Day (game) | Daily reward slot → random item | 4/day | n/a | existing | OLD [B24] |
| M12 | 8 Ball Pool (game) | Free rewards → hard-currency stack | 1/day | n/a | existing | OLD [B25] |
| M13 | Choices (interactive stories) | Premium choice → diamonds | 3/day | buy diamonds | existing | OLD [B26] |
| M14 | Trivia Star (game) | Ad load → 5 ads buy 24 h ad-free | one bundle | n/a | existing | OLD [B28] |
| M15 | Castle Clash (game) | Hero hire → one free hire | 1 per 24 h | pay | existing | OLD [B27] |
| M16 | Simula UNO demo | Penalty → "Watch to Block" | unstated | n/a | product change | live [G14] |
| M17 | Duolingo (language) | Energy (new, replaced Hearts) → partial refill | unpublished | subscribers unlimited | product change | OLD, 2025 sources [C01][A2-11] |
| M18 | WEBTOON (comics) | Locked episode → 3-day unlock (coins = permanent) | per episode | coin path | existing | live [C02][C03] |
| M19 | Tapas (comics) | Locked episode → 72 h unlock; overflow to an offerwall | 3/day, midnight PT reset | ink path | existing | live [C04][C05][C06] |
| M20 | Pocket FM (audio series) | Locked episode → ad unlock, or coins via videos/tasks | unpublished | coins | existing | live [C07] |
| M21 | Wattpad (fiction) | Coins for Originals → 1 bonus coin per full ad, expire 30 days | 10/day (page also says 3) | Premium+ gets monthly coins | existing | live [C08][C09] |
| M22 | Spotify (music) | Ad interruptions → 30 min ad-free per sponsored video | unpublished | n/a | existing | OLD [C11] |
| M23 | Pandora (music) | On-demand (paid) → one session after a search | unpublished | n/a | existing | OLD [C12] |
| M24 | Pandora Video Plus (music) | Skips, replays → extra per video | unpublished | n/a | existing | OLD [C13] |
| M25 | Google Offerwall (publishers) | Metered content → choose ad / survey / micropay / signup | publisher-set | subscribers bypass | product change | live [C14][C15][C16] |
| M26 | Webnovel (web fiction) | Votes that rank a novel → "Get Free Spirit Stones" by watching ads | unstated | unstated | existing | unverified [A2-17] |
| M27 | Twitch (live streaming) | Bits to cheer a streamer → 5-100 Bits per ad, by country | daily limit, unstated number | unstated | existing | OLD [A2-23][A2-18] |
| M28 | Remini (AI photo) | Paid enhancement → small ad-gated daily allowance (historically ~5 a day) | daily | subscribers skip | existing | unverified [A2-13] |
| M29 | FaceApp (AI photo) | Pro filter → temporary unlock per ad | unstated | Pro has all | existing | unverified [A2-14] |

Patterns across the catalog:
- **Ad path < paid path, same item.** Temporary vs permanent (M18, M19), partial vs full (M17), one session vs the plan (M02, M23). This is how apps protect the purchase.
- **Two-tier caps.** A hard daily cap on the main offer, then an overflow surface (M19 → offerwall; M21 videos + coin offers).
- **Anchors cluster at failure and waiting**: out of lives, crashed, stuck, locked, out of energy.
- **In AI chat, rewarded ads are rare.** Only M01 (a trade in the other direction) and M05 (quest currency, not ads) are live in major companion or assistant apps. Most monetize by caps and subscriptions: Character.AI lite keeps a daily premium-model meter and 15 → 30 memory pins [A03][A04, P]; free Character.AI keeps "unlimited messaging" [A02, P]; PolyBuzz interrupts free sessions with ads and keeps free users on a "Standard" model [A16, S]; Linky shows "ads appearing after nearly every AI response" with ~20-30 messages a day [A2-03, S]; Talkie caps around 50 messages a day with "ads at regular intervals" [A2-01, S]; SpicyChat cut its free daily cap from 2,000 to 150 to 100 [A2-07, S]; Poe cut free compute points from 3,000 to ~300 a day [A2-09, S]. No companion app studied lets users watch an ad for messages (Linky: sources conflict [A2-04]). AI *photo* apps do: Remini (a small ad-gated daily allowance) and FaceApp (temporary Pro filter) [A2-13][A2-14, S]. Rewarded ads in AI chat are open ground, and the economics in section 4 are why.

## 7. Anti-patterns (documented failures)

| Anti-pattern | Evidence | Rule it implies |
|---|---|---|
| Cash or cash-equivalent rewards | TikTok Lite points "with monetary value" for tasks; EU proceedings in April 2024, permanent EU withdrawal made binding in August 2024 [H01][H02][H03, S, OLD] | Never. Also banned by AdMob [F04] |
| Tightening a free limit and calling it a feature | Duolingo Energy drains on correct answers too; users: "Hearts = punished for making a mistake / Energy = punished for using the app"; one user needed "4-5 ads" to finish a normal day's lessons [H05][H06, S] | If a change tightens a limit, say so; size the ad refill so a typical day still works |
| Escalating ad placement past a promise | Character.AI ads moved from between chats (mid-2025) to mid-conversation (Oct 2025): "I thought they weren't going to do that." [H07, S]; then caps on swipes and memos for free users (Mar 2026) [H08, S] | Never interrupt the core loop; don't stack new caps on new ads |
| Irrelevant promotion in a chat surface | ChatGPT "app suggestions" (Peloton mid-conversation) read as ads, shown even to a $200/mo Pro user; OpenAI: "the lack of relevancy makes it a bad/confusing experience" [H09][H10, S] | Promotional content in chat needs a relevance gate and must never reach payers |
| Chat content as an ad signal | Meta uses Meta AI conversations as a signal for ads across its apps from 2025-12-16 [H11, S] | Simula can't and won't [G15]. Triggers are app events |
| Currency without sinks | "Too many sources without meaningful sinks lead to inflation and the trivialization of progress." [H20, V] | Every currency reward names what it's spent on and a cap |
| Frequency fatigue | "A Reward Doubler is a gift the first time and background noise by the tenth." [H21, V]. Rule-of-thumb caps: casual games 15-20 rewarded views/day, midcore 10-15 [H22, snippet-only]. Interstitial every level: 15-25% first-session abandonment [H23, snippet-only] | Daily cap on every offer; test against D1/D7 retention |
| Reward for installs or external actions | Incentivized installs draw fraud and low-value users [H15][H16, snippet-only] | Reward only in-app, server-verified completions |
| No-fill not designed for | AdMob forums: "No fill" is routine [H17, P] | Hide the offer on no-fill; never consume the user's attempt |
| Children | Disney paid $10M over mislabeled kids' videos used for targeted ads [H18, P] | No targeted rewarded ads in child-directed flows |

## 8. Decision heuristics (testable)

Each rule is phrased so a judge can check it against one idea.

1. **Anchor is observed.** The idea cites a product-model element (limit, meter, price, locked item, wait, loss) by id. No citation → not an existing anchor.
2. **Trigger is an app event** the app can detect without reading chat: counter hits zero, paywall dismissed, locked item tapped, timer running, session N. Never a topic or sentiment.
3. **Moment of need.** The offer appears when the user is blocked or has just lost something, never mid-task or mid-conversation.
4. **Reward stated in the offer copy with a number and a duration** ("3 more replies today", "unlocked for 72 hours").
5. **Ad path < paid path.** The reward is smaller, shorter, or partial compared with what the subscription or IAP gives. If the reward equals the main paid benefit for a day or more, fail.
6. **Economics.** Code's verdict is PASS or CONDITIONAL. If CONDITIONAL, the idea names the condition (region, serving price, views per grant).
7. **Prefer zero-inference rewards** in AI apps: visibility, cosmetics, loss protection, wait skips, temporary access to something already computed or cached. Rewards that cost fresh inference need a stated eCPM.
8. **Three branches handled**: verified → grant; verification failed or no fill → app unchanged, attempt not consumed; declined → app unchanged.
9. **Daily cap stated**, with a reset time, and no currency without a sink.
10. **Payers.** Payers never see ads they paid to remove. Opt-in offers for things payers lack can stay.
11. **No cash, no store actions, no screen-time rewards, no kids.**
12. **Specific.** Pasting the idea into a different app of the same category should break it: it depends on this app's own element, actor, or rule. Name the M-id you adapted and what differs.
13. **Product changes loosen or add, they don't cut.** A new limit is safe only if most current users are no worse off; otherwise flag the backlash risk (section 7).
14. **One ad, one clear reward.** Don't bundle several ads for one reward unless the economics require it (images), and then say so in the offer ("play 3 games for 1 image").
15. **No loops.** A reward earned by ads must not buy the removal of other ads at a better rate than one-for-one.

## 9. Known gaps (don't fill them with guesses)

- No public data on rewarded-ad performance inside AI chat apps. All eCPM data is vendor-reported and mostly games.
- No public Simula eCPM or revenue share. Ad networks don't publish publisher/network splits [D29].
- No measured tokens-per-turn for companion apps; the central 4,000 in / 300 out is an assumption.
- Spotify Sponsored Session and Pandora Sponsored Listening may no longer exist (sources 2014 and 2017).
- Rewarded-ads-grow-IAP claims are correlational vendor data.
- Of the two creator-support examples, M26 (Webnovel) is secondary-sourced only; M27 (Twitch) is primary-sourced for 2016 (US), and only a 2026 guide says it still runs.
