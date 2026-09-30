# 06 · judge

Judges: judge_1 (claude-sonnet-5-5, high), judge_2 (gpt-6-sol, high), blind (opaque ids, no lens or model names, one candidate per call). Economics mode: `annotate`. 10 candidates: 0 accepted, 4 conditional, 0 waiting on a person, 6 rejected; 1 revised once.

| Candidate | Final | Checks | Rank | Cost mark |
|---|---|---|---|---|
| c07 · Product change: 30 minutes of reading with no ads, sponsored by one brand | conditional | 10/11 | 1 | CONDITIONAL |
| c02 · Product change: Save your reading streak after missing a day | conditional | 10/11 | 0 | PASS |
| c06 · Product change: Open straight to your favorite section for 7 days | conditional | 10/11 | 0 | PASS |
| c09 · Product change: Pick which kinds of sponsored stories you see for 7 days | conditional | 9/11 | 0 | PASS |
| c03 · Product change: 15 minutes of stories without the ad blocks inside | reject | 10/11 | 0.5 | CONDITIONAL |
| c01 · Product change: Read stories without ads for 20 minutes | reject | 10/11 | 0.5 | CONDITIONAL |
| c08 · Product change: 30 minutes of articles without the advertisement blocks | reject | 10/11 | 0.5 | CONDITIONAL |
| c03-rev · Product change: 15 minutes of stories without the ad blocks inside | reject | 10/11 | 0.5 | CONDITIONAL |
| c04 · Product change: Keep your reading streak after a missed day | reject | 10/11 | 0 | PASS |
| c05 · Product change: Keep your reading streak after a missed day | reject | 9/11 | 0 | PASS |

## c07 · Product change: 30 minutes of reading with no ads, sponsored by one brand

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The 'what makes it work here' field claims the ad value displaced by a half hour is low. Mechanics m1 and m2 establish where ads appear, but do not support a claim about their current value) and another passes it (The claims about today's app are supported by the model: feed cards (m1), cards under the byline and in-body ADVERTISEMENT blocks (m2, s05.e13, s13.e34, s20.e27), and TEMU rotation. The no-paid-plan and paywall statements are worded as not seen, matching m4 and the open questions).
- Cost mark (CONDITIONAL): Costs ~$0.0582 per reward to serve; pays for itself above $58.20 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 30 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $3.30; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Hiding Taboola cards for 30 minutes may conflict with the app's ad revenue or Taboola terms. Whether one brand can sponsor the credit line beside Taboola inventory is an unverified assumption.
  - ✓ g_policy: The banner is opt-in and dismissible, and the offer copy states the reward (30 ad-free minutes) before the game. Declining leaves the feed unchanged, and the 'if the ad fails' field gives a path for a game that can't load or be confirmed.
  - ✓ g_no_cash: The reward is 30 minutes without ads, a feature-time reward with no cash value.
  - ✓ g_no_chat_content: The trigger is an app event, returning to the feed after the third article open. It needs no chat content.
  - ✓ g_no_free_removal: Nothing free is removed or capped. Ads are hidden temporarily only as a reward, and the feed and articles stay as they are for users who decline.
  - ✓ g_brand_safety: The banner is a list card under the header of the home feed (s01), which is rated safe. It is not in a conversation, and no chat screen is involved.
  - ✓ c1_revealed_value: This is a product_change. The new resource, an ad-free reading window, is used by the player in the observed core flows f1 and f2 (reading articles). None of the reward goes to another party.
  - ✓ c2_evidence: The claims about today's app are supported by the model: feed cards (m1), cards under the byline and in-body ADVERTISEMENT blocks (m2, s05.e13, s13.e34, s20.e27), and TEMU rotation. The no-paid-plan and paywall statements are worded as not seen, matching m4 and the open questions.
  - ✓ c4_protects_subscription: The reward is a 30-minute sample that ends, and no paid ad-free plan was observed. It takes nothing from other users or creators.
  - ✓ c5_moment: The banner appears on the home feed after the user comes back from an article, so it does not interrupt a task. It is also dismissible and sits on a persistent surface.
  - ✓ c6_fits_simula: It is a play-to-earn sponsored game, and the 30 minutes start only once the game is confirmed. The trigger, the third article open followed by a return to the feed, is an app event the app can detect.
  - ✓ c7_specific: It names this app's specific ad surfaces: the Taboola cards between stories on the home feed, the card under each article byline, and the in-article ADVERTISEMENT blocks.
- judge_2: fixable; other concern: The proposal does not establish whether existing ad arrangements permit suppressing sponsored cards and display blocks.
  - ✓ g_policy: The offer copy states the 30-minute reward before the user chooses to play. The decline and ad-failure fields leave normal reading available without consuming the day's chance.
  - ✓ g_no_cash: The reward field grants 30 minutes without in-app ads, not cash or a transferable equivalent.
  - ✓ g_no_chat_content: The trigger event counts article opens and a return to the feed; it does not need anything the user wrote or said.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields leave the existing free reading flow intact and offer temporary ad suppression as an extra.
  - ✓ g_brand_safety: The placement is a banner under the header of s01, a content-safe home feed, rather than inside a conversation or beside unsafe or unknown content.
  - ✓ c1_revealed_value: This product change gives the player an ad-free period for reading articles, their own use of the observed core flows f1 and f2.
  - ✗ c2_evidence: The 'what makes it work here' field claims the ad value displaced by a half hour is low. Mechanics m1 and m2 establish where ads appear, but do not support a claim about their current value.
  - ✓ c4_protects_subscription: The reward expires after 30 minutes, and the model does not establish a paid ad-free benefit that it would grant without a limit.
  - ✓ c5_moment: The trigger and placement put a dismissible banner on s01 after the user returns from reading, not in the middle of an article.
  - ✓ c6_fits_simula: The flow requires playing a sponsored game, while the reward and ad-failure fields withhold the ad-free period unless the game is confirmed. The third article open is a detectable app event.
  - ✓ c7_specific: The idea names s01's sponsored feed cards, s05's under-byline card, and the in-article ADVERTISEMENT blocks it would temporarily suppress.

## c02 · Product change: Save your reading streak after missing a day

- **conditional**, 10/11 checks passed by every judge; split: c1_revealed_value
- Condition: The judges split: on c1_revealed_value, one fails it (As a product_change, the new resource (a streak save) is not a slice of anything the app charges for. It is also not something the player uses in an observed core flow (f1 to f6 are reading, sign-in, receipts and search). It only protects a counter the proposal itself invents) and another passes it (As a product change, the save protects the reader's own streak earned by opening stories, an action in the observed reading flows f1 and f2).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: A streak mechanic could be bolted onto any news app. Declining resets the streak, which is a new loss the offer itself creates. Neither is a listed fail condition, since the streak did not exist before.
  - ✓ g_policy: The banner is opt-in and its offer copy states the reward ('keep your 5-day reading streak, one save this week'). Declining leaves the feed and stories as they are today, and the 'if the ad fails' path is defined.
  - ✓ g_no_cash: The reward is a streak save, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event, the first home feed open after a missed day with a streak of 3 or more. It needs no chat content.
  - ✓ g_no_free_removal: The proposal adds a new streak feature and removes or caps nothing that is free today. The 'removes nothing free: yes' field is consistent with the flow.
  - ✓ g_brand_safety: The banner sits under the Home header on s01, which is rated safe. It is app chrome, not a chat surface and not next to unsafe content.
  - ✗ c1_revealed_value: As a product_change, the new resource (a streak save) is not a slice of anything the app charges for. It is also not something the player uses in an observed core flow (f1 to f6 are reading, sign-in, receipts and search). It only protects a counter the proposal itself invents.
  - ✓ c2_evidence: Claims about the app rest on s01 (Top Stories, category tabs, '2h ago' timestamps). 'No streak, limit or reward system was seen' is worded as not seen, and the new streak, banner and copy are proposal additions.
  - ✓ c4_protects_subscription: The streak save is a single, weekly-capped protection for a new counter. It gives away no paid benefit, grants no lasting access, and takes nothing from other users.
  - ✓ c5_moment: The banner appears on the home feed open. That is a persistent surface, and the user is not in the middle of a task.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game, and the trigger is a detectable app event (feed open after a missed day). The failure path indicates the save is granted only when the game is confirmed.
  - ✓ c7_specific: The idea is tied to the s01 home feed, its Top Stories list and its daily-return pattern, and to the banner placement under the Home header.
- judge_2: not fixable; other concern: Clarify that a streak held after failed verification is only pending another chance to play, and is not marked permanently kept before REWARD_VERIFIED.
  - ✓ g_policy: The offer copy states that playing keeps a 5-day streak with one save this week. The banner is optional, declining leaves the feed and stories available, and the proposal gives paths for load and verification failures.
  - ✓ g_no_cash: The reward is one in-app reading streak save, not cash or a transferable or externally exchangeable item.
  - ✓ g_no_chat_content: The trigger uses a home-feed open and elapsed days since a story was opened, not chat content.
  - ✓ g_no_free_removal: The adds field introduces the streak and its save; the proposal does not remove or cap any existing free feature.
  - ✓ g_brand_safety: The placement is a banner under the Home header on s01, which the product model rates content safe.
  - ✓ c1_revealed_value: As a product change, the save protects the reader's own streak earned by opening stories, an action in the observed reading flows f1 and f2.
  - ✓ c2_evidence: s01 shows the Home feed, Top Stories, category tabs, and timestamps cited in what makes it work here. The streak is described as a new addition, not an existing feature.
  - ✓ c4_protects_subscription: The reward covers one missed day and is limited to one save per week; it grants no lasting paid access or advantage taken from other users.
  - ✓ c5_moment: The trigger is the first Home feed open after a missed day, and the placement is a banner rather than an interruption during article reading.
  - ✓ c6_fits_simula: The flow uses a short sponsored game triggered by a detectable feed-open event. The failure path says an unconfirmed game does not use the weekly save, rather than granting that save.
  - ✓ c7_specific: The idea names s01's Home header and Top Stories list and ties the proposed streak to opening stories from the news feed.

## c06 · Product change: Open straight to your favorite section for 7 days

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The 'what makes it work here' field says the home feed always opens on Top Stories, and the decline field says it keeps doing so today. s01.e13 shows Top Stories selected on an observed screen, but does not establish behavior on every app opening) and another passes it (The claims about the app today are supported. s01.e13 shows Top Stories selected with the other tabs beside it (s01.e15 to s01.e21). The absence of a way to change the opening tab is worded as 'not seen in the explored screens'. The new card, setting and copy are what the proposal itself adds).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The reward has very low perceived value and may not motivate opt-in. 'Always opens on Top Stories' rests on limited observation, and the Settings menu (s03.e128) was never opened, so an existing preference could be there. The cited evidence list is empty.
  - ✓ g_policy: The card is opt-in, the offer copy states the reward (open on Sports for 7 days), declining leaves the app as it is today, and 'if the ad fails' gives a retry path that does not use up the weekly offer.
  - ✓ g_no_cash: The reward is a cosmetic 7-day default-section setting with no cash value.
  - ✓ g_no_chat_content: The trigger is a tap on a section tab on s01 after 3 or more days away, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: Nothing free is removed or capped. The app keeps opening on Top Stories for everyone who declines, and the feed is unchanged.
  - ✓ g_brand_safety: The card sits over the home feed s01, which is rated safe and is not a chat screen, and there is no unsafe or unknown content next to it.
  - ✓ c1_revealed_value: This is a product_change. The new opening-section setting is for the player's own use on the home feed, where the observed core flows f1 and f2 start, and the reward goes to no one else. It is a weak but sufficient value.
  - ✓ c2_evidence: The claims about the app today are supported. s01.e13 shows Top Stories selected with the other tabs beside it (s01.e15 to s01.e21). The absence of a way to change the opening tab is worded as 'not seen in the explored screens'. The new card, setting and copy are what the proposal itself adds.
  - ✓ c4_protects_subscription: The reward is a cosmetic 7-day setting. The model records no paid plan, and nothing is taken from other users or creators.
  - ✓ c5_moment: The card appears after the user's own tab tap has loaded, which is a finished action and not an interruption mid-task.
  - ✓ c6_fits_simula: It is a play-to-earn unit. The setting is granted only after the game is confirmed, and the trigger (a tab tap after 3 or more days away) is detectable by the app.
  - ✓ c7_specific: The idea is built on the s01 home feed, its Top Stories default, and the Entertainment, Local, Sports and Business tabs.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the seven-day opening-section reward before the user chooses to play. The decline and ad-failure fields preserve the current experience and provide a path when a game is unavailable or cannot be confirmed.
  - ✓ g_no_cash: The reward is a seven-day choice of opening section, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses time since the last visit and a section-tab tap, not chat content.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce an optional opening-section setting without restricting the observed free news flow.
  - ✓ g_brand_safety: The placement is a card on the home feed, which is rated content safe in s01; it is not inside a conversation.
  - ✓ c1_revealed_value: As a product change, the reward gives the player a seven-day opening-section choice for their own use when entering the home-feed news flow observed in f1 and f2.
  - ✗ c2_evidence: The 'what makes it work here' field says the home feed always opens on Top Stories, and the decline field says it keeps doing so today. s01.e13 shows Top Stories selected on an observed screen, but does not establish behavior on every app opening.
  - ✓ c4_protects_subscription: The reward ends after seven days, and the model does not establish a paid opening-section benefit or a list advantage taken from other users.
  - ✓ c5_moment: The placement appears after the user taps a section tab and that section loads, rather than interrupting an article in progress.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by a detectable tab tap, and the ad-failure field withholds the reward if the game cannot be confirmed.
  - ✓ c7_specific: The idea relies on the home feed's Top Stories, Sports, and Local tabs shown in s01.e13–s01.e21.

## c09 · Product change: Pick which kinds of sponsored stories you see for 7 days

- **conditional**, 9/11 checks passed by every judge; split: c1_revealed_value, c2_evidence
- Condition: The judges split: on c1_revealed_value, one fails it (This is a product_change. The new resource is a say over which ads appear. It is not a slice of anything the app charges for, and it is not used in a core flow the model observed, since reading articles (f1, f2) is unaffected. The proposal itself says it mainly helps advertisers reach chosen readers, so the player gets little of the value) and another passes it (This product change gives the reader a choice they use in the home feed, which is part of the observed story-reading flows f1 and f2. The benefit is not predominantly given to another person); on c2_evidence, one fails it (The 'what makes it work here' field presents the quoted TEMU jacket deal as part of the home feed's sponsor mix, but that headline is observed on article s05.e15, not home-feed card s01.e44. Its claim that car accessories are among the observed sponsors is also unsupported by s01.e44's 'Elevate Your Your Ride' headline) and another passes it (Rotating Taboola sponsors and cards between stories are supported by m1 and the cited s01/s16/s32 elements. The claim that readers can't steer the mix is worded as 'not seen in any explored screen', and the open question about the more-options menu is acknowledged. The new card, counters and copy are the proposal's own additions).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The proposal attributes 'car accessories' to the Extend-A-Reach mix, but that ad is about cleaning tools, and the car-style headline ('Elevate Your Your Ride') belongs to the TEMU card. Any lasting value depends on whether the app can actually filter Taboola categories, which was not observed. The reward gives the reader nothing they clearly want.
  - ✓ g_policy: The offer is a dismissible card the reader chooses to play, and the offer copy states the reward (choose sponsored story kinds for 7 days). Declining keeps the usual feed, and the if_the_ad_fails field covers a game that is unavailable or can't be confirmed.
  - ✓ g_no_cash: The reward is a cosmetic 7-day choice of sponsored story categories, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is the third home-feed open of the day, which is an app event, and no chat content is used.
  - ✓ g_no_free_removal: Nothing free is removed, locked or capped. Only the mix of sponsored categories changes, and no cards are taken out.
  - ✓ g_brand_safety: The card sits on the home feed (s01), which is rated safe. It is app chrome, not inside a conversation or on an unsafe or unknown screen.
  - ✗ c1_revealed_value: This is a product_change. The new resource is a say over which ads appear. It is not a slice of anything the app charges for, and it is not used in a core flow the model observed, since reading articles (f1, f2) is unaffected. The proposal itself says it mainly helps advertisers reach chosen readers, so the player gets little of the value.
  - ✓ c2_evidence: Rotating Taboola sponsors and cards between stories are supported by m1 and the cited s01/s16/s32 elements. The claim that readers can't steer the mix is worded as 'not seen in any explored screen', and the open question about the more-options menu is acknowledged. The new card, counters and copy are the proposal's own additions.
  - ✓ c4_protects_subscription: The reward expires after 7 days. It gives away nothing the app charges for, since no paid plan was observed, and it takes nothing from other users or creators.
  - ✓ c5_moment: The offer is a dismissible card on the persistent home feed. It does not interrupt a task the user is in the middle of.
  - ✓ c6_fits_simula: It is a play-to-earn unit, and the picker only opens once the play is confirmed. The trigger, a third home-feed open in a day, is an app event the app can detect.
  - ✓ c7_specific: It names the home feed s01, the category tabs, and the rotating Taboola sponsors (TEMU, Toxin Free Life, Extend-A-Reach) that make it work here.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the choice and its seven-day duration before play. The card is optional, declining preserves the usual feed, and 'if the ad fails' provides a no-reward path.
  - ✓ g_no_cash: The reward is a seven-day choice of sponsored-story categories, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger is a count of home-feed opens, not anything the user typed or said.
  - ✓ g_no_free_removal: The 'adds' and 'if the user declines' fields keep the existing feed available; the optional choice changes the mix of sponsored cards rather than locking free content.
  - ✓ g_brand_safety: The placement is a dismissible card below the category tabs on s01, a content-safe home feed, rather than inside a conversation or next to unknown-rated content.
  - ✓ c1_revealed_value: This product change gives the reader a choice they use in the home feed, which is part of the observed story-reading flows f1 and f2. The benefit is not predominantly given to another person.
  - ✗ c2_evidence: The 'what makes it work here' field presents the quoted TEMU jacket deal as part of the home feed's sponsor mix, but that headline is observed on article s05.e15, not home-feed card s01.e44. Its claim that car accessories are among the observed sponsors is also unsupported by s01.e44's 'Elevate Your Your Ride' headline.
  - ✓ c4_protects_subscription: The reward expires after seven days, and the model establishes no paid benefit that it gives away. The proposal changes which paid sponsor categories appear, not another user's or creator's existing placement.
  - ✓ c5_moment: The 'placement' is a dismissible card on the home feed when it opens, not an interruption in the middle of reading an article.
  - ✓ c6_fits_simula: The flow uses a short sponsored game and grants the choice only once play is confirmed. Opening the home feed for the third time is a detectable app event.
  - ✓ c7_specific: The idea uses s01's category tabs and sponsored cards, whose rotation is observed in m1.

## c03 · Product change: 15 minutes of stories without the ad blocks inside

- **reject**, 10/11 checks passed by every judge
- Cost mark (CONDITIONAL): Costs ~$0.0291 per reward to serve; pays for itself above $29.10 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 15 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $1.65; one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The card sits above a bar that is pinned during the whole article, so its exact placement and the 'end of story' detection need care. Removing display ads for 15 minutes may cost ad revenue; that is a prediction and was not scored.
  - ✓ g_policy: The card is opt-in and the offer copy states the reward (15 minutes without in-story ad blocks). Declining leaves reading unchanged, and the 'Couldn't unlock this time' note covers a game that fails to load or verify.
  - ✓ g_no_cash: The reward is 15 minutes of reduced in-story ads, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event (reaching the end of the third story of the day). No typed or chat content is involved.
  - ✓ g_no_free_removal: Nothing free today is removed, locked or capped. Ads are only hidden temporarily as a reward, and 'removes nothing free: yes' holds.
  - ✓ g_brand_safety: The card sits on s24, an article screen rated safe, as app chrome above the next-story bar. It is not inside a chat and not next to unsafe or unknown content.
  - ✓ c1_revealed_value: This is a product change that adds ad-free reading time. The player uses it for their own benefit in the observed core flows f1 and f2, which are reading articles past in-article ads, and none of it goes to another party.
  - ✗ c2_evidence: The 'what makes it work here' field says the pinned next-story bar 'marks the moment one story ends'. The model describes s24 as a pinned bar shown throughout the article body, so this is unsupported. The claim of ad blocks 'inside every story' also generalizes from the two articles observed.
  - ✓ c4_protects_subscription: No paid ad-free plan was observed. The reward is a 15-minute window that expires, and it takes nothing from other users or creators.
  - ✓ c5_moment: The offer appears when a story finishes (end of the third story), which is a finished action and not an interruption in the middle of reading.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game. The unlock is tied to a confirmed play (the failure path handles an unconfirmed game), and the trigger is a detectable app event.
  - ✓ c7_specific: It names this app's in-article ADVERTISEMENT blocks (s13.e34, s20.e23, s24.e12), the 'Next in More For You' bar and the s24 screen, so it would not read the same in any app.
- judge_2: fixable; other concern: The proposal should distinguish an observed next-story bar from a verified story-end position when implementing the trigger.
  - ✓ g_policy: The offer copy states the 15-minute reward before the user chooses to play. The decline and ad-failure fields leave reading unchanged if the user declines or the game cannot be confirmed.
  - ✓ g_no_cash: The reward is 15 minutes without in-story ad blocks, not cash or a cash-like item.
  - ✓ g_no_chat_content: The trigger event uses a story count and the next-story bar, not anything the user typed or said.
  - ✓ g_no_free_removal: The adds and reward fields hide ad blocks temporarily; they do not remove, lock, or cap free reading.
  - ✓ g_brand_safety: The placement is a card by the next-story bar on s24, which the product model rates content safe, rather than an unsafe or chat surface.
  - ✓ c1_revealed_value: This product-change reward is for the reader's own use while reading stories, an observed core flow in f1 and f2.
  - ✗ c2_evidence: The what-makes-it-work-here field says several ad blocks appear inside every story, and the rationale says three or more do; m2 supports only one or more blocks per article. That field also calls the s24 next-story bar the point where the story ends, although the story continues through s31.
  - ✓ c4_protects_subscription: The reward lasts 15 minutes and the frequency cap limits it to twice daily; it grants no lasting access to a paid benefit.
  - ✓ c5_moment: The trigger event places the offer at a story end, and the placement describes an optional card by the next-story bar rather than an interruption that must be dismissed to continue.
  - ✓ c6_fits_simula: The flow requires playing a sponsored game, and the ad-failure field says the reward is withheld if the play cannot be confirmed. The story-count and bar trigger is an app-detectable event.
  - ✓ c7_specific: The placement names the 'Next in More For You' bar at s24.e24, and the reward concerns the in-article ADVERTISEMENT blocks shown at s20.e23.

## c01 · Product change: Read stories without ads for 20 minutes

- **reject**, 10/11 checks passed by every judge; dropped by code: duplicate of c07: same benefit (ad-free reading)
- Cost mark (CONDITIONAL): Costs ~$0.0388 per reward to serve; pays for itself above $38.80 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 20 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $2.20; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Suppressing Taboola cards and display blocks may depend on the ad partners allowing it. 'Third article in a session' also needs a definition, and the trigger screen names only one article (s12).
  - ✓ g_policy: The banner is opt-in and its offer copy states the reward (20 minutes of ad-free reading). Declining hides the banner and leaves the article as it is today, and the 'if the ad fails' field covers a game that is unavailable or unconfirmed.
  - ✓ g_no_cash: The reward is 20 minutes of ad-free reading, which has no cash value and can't be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event, the third article opened in a session, and needs no chat or typed content.
  - ✓ g_no_free_removal: Nothing free is removed or capped. Ads stay as they are unless the user plays, and the ad-free window is only a temporary extra.
  - ✓ g_brand_safety: The banner sits in the header area of the article screen s12, which is rated safe, so it counts as app chrome. It is not in a chat and not next to unsafe or unknown content.
  - ✓ c1_revealed_value: This is a product change that adds ad-free reading time, which the player uses in the observed core reading flows f1 and f2. Almost all of the benefit goes to the player.
  - ✓ c2_evidence: The claims about today's app are backed by the model. m2 shows a sponsored card under the byline and ADVERTISEMENT blocks in articles, and the open questions record no paid plan and an unconfirmed paywall, which the proposal words as 'not seen'. The revenue comparison is a prediction, not a claim about the app.
  - ✓ c4_protects_subscription: No paid ad-free plan was observed, and the reward expires after 20 minutes with a 1-per-day cap. It takes nothing from other users or creators.
  - ✓ c5_moment: A slim banner under the header does not block or interrupt reading, and it appears when the user opens their third story.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game, and the reward comes after the game ends. The trigger is a detectable app event, the third article opened, and the failure path grants nothing if the play can't be confirmed.
  - ✓ c7_specific: It names this app's article screen, the sponsored card under the byline, the ADVERTISEMENT display blocks, and the f2 gallery flow.
- judge_2: fixable; other concern: The claim that one completed game will outweigh the article-ad views it replaces is an unverified revenue forecast.
  - ✓ g_policy: The offer copy states the 20-minute reward before an optional game. The decline and ad-failure fields leave the article unchanged.
  - ✓ g_no_cash: The reward is 20 minutes of ad-free reading, not cash or transferable value.
  - ✓ g_no_chat_content: The trigger event counts article opens; it does not use chat content.
  - ✓ g_no_free_removal: The proposal does not cap or lock free reading; the decline path keeps the article as it is.
  - ✓ g_brand_safety: The placement is a banner on the s12 article screen, which the model rates content safe.
  - ✓ c1_revealed_value: As a product change, ad-free reading benefits the player in the observed article-reading core flows f1 and f2.
  - ✗ c2_evidence: The 'what makes it work here' field claims every article has several display blocks, but mechanic m2 supports only one or more blocks per article. That present-day claim exceeds the observed evidence.
  - ✓ c4_protects_subscription: The reward expires after 20 minutes, and the model does not establish a paid ad-free benefit that it would give away without a limit.
  - ✓ c5_moment: The placement is a slim banner when the third article opens, not an interruption in the middle of reading.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by a detectable article-open event, and the ad-failure field says nothing changes if play cannot be confirmed.
  - ✓ c7_specific: The idea names the s12 article screen and the sponsored cards and display ads documented in m2.

## c08 · Product change: 30 minutes of articles without the advertisement blocks

- **reject**, 10/11 checks passed by every judge; dropped by code: duplicate of c07: same benefit (ad-free reading)
- Cost mark (CONDITIONAL): Costs ~$0.0582 per reward to serve; pays for itself above $58.20 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 30 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $3.30; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The next-story bar on s24 is pinned mid-article, so detecting 'end of a second article' may need scroll-end logic. It is also unclear whether third-party Taboola and display blocks can be suppressed, and hiding them may cost ad revenue. The proposal itself flags the revenue point.
  - ✓ g_policy: The card is opt-in (the reader chooses to play), the offer copy states the reward (30 minutes without advertisement blocks) before the game, and declining just hides the card. There is a stated failure path ('That didn't go through, nothing was used').
  - ✓ g_no_cash: The reward is 30 minutes of ad-free reading (feature_time), which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event (reaching the end of a second article so the next-story bar appears), and no chat content is involved.
  - ✓ g_no_free_removal: Nothing free today is removed or capped. The reward only temporarily hides ad blocks, and 'removes nothing free: yes' holds.
  - ✓ g_brand_safety: The card sits on s24, an article screen rated safe, and it is app chrome above the next-story bar. It is not in a chat and not next to unsafe or unknown content.
  - ✓ c1_revealed_value: The new resource is ad-free reading, which the player uses themselves in the observed core reading flows f1 and f2. None of it goes to another party.
  - ✓ c2_evidence: The claims about today's app are supported: ad cards and ADVERTISEMENT blocks in each article (m2, s05.e13, s20.e27), the gutter ad repeating across s20 to s26, and the 'Next in More For You' bar (s24.e24). The 'no paywall or paid plan seen' claim is worded as not seen and matches the open question. The new card, timer and copy are the proposal's own additions.
  - ✓ c4_protects_subscription: No paid plan or ad-free tier was observed, so nothing the app charges for is given away. The 30-minute reward expires, and nothing is taken from other users or creators.
  - ✓ c5_moment: The offer appears at a finished-action moment, when the reader finishes a story and the next-story bar appears. It does not cut into a task in progress.
  - ✓ c6_fits_simula: This is a play-to-earn unit. The reward is granted only once the play is confirmed, and the trigger is a detectable article and next-story-bar event.
  - ✓ c7_specific: It is tied to this app's Taboola cards, the in-article ADVERTISEMENT blocks, and the pinned 'Next in More For You' bar on the article screen.
- judge_2: fixable; other concern: The 'when the reward runs out' copy suggests another game is available that day even if the stated two-per-day cap has already been reached.
  - ✓ g_policy: The offer copy states the 30-minute reward before play, and the card is dismissible. The decline and ad-failure fields leave articles unchanged if the reader declines or verification fails.
  - ✓ g_no_cash: The reward is 30 minutes without in-article advertisement blocks, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses article progress and the next-story bar, not anything a user typed or said.
  - ✓ g_no_free_removal: The adds and decline fields preserve ordinary article reading; the proposal does not lock or cap a free reader benefit.
  - ✓ g_brand_safety: The placement is a card by the next-story bar on s24, which the product model rates content safe.
  - ✓ c1_revealed_value: As a product change, the reward gives the player a timed cleaner-reading option for their own use in the observed article-reading flows f1 and f2.
  - ✗ c2_evidence: The 'what makes it work here' field says every article has several in-body advertisement blocks, but m2 supports only one or more. It also calls the bar at s24.e24 a break after finishing the story, although the article continues through s31.
  - ✓ c4_protects_subscription: The reward expires after 30 minutes, and the model does not establish a paid ad-free benefit that it would grant without a limit.
  - ✓ c5_moment: The trigger event and flow place the offer after the reader finishes a story, rather than interrupting reading in progress.
  - ✓ c6_fits_simula: The flow grants the reward once the sponsored play is confirmed, and reaching an article-progress event is detectable by the app.
  - ✓ c7_specific: The idea uses this app's in-article advertisement blocks and its 'Next in More For You' bar at s24.e24.

## c03-rev · Product change: 15 minutes of stories without the ad blocks inside

- **reject**, 10/11 checks passed by every judge, revision of c03; dropped by code: duplicate of c07: same benefit (temporary ad-free reading)
- Cost mark (CONDITIONAL): Costs ~$0.0291 per reward to serve; pays for itself above $29.10 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 15 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $1.65; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Hiding third-party Taboola or display blocks on demand may be technically or contractually hard, and the model does not show whether it is possible. Reduced ad revenue during the reward window is a business tradeoff.
  - ✓ g_policy: The user opts in by tapping the card. The offer copy states the reward (15 minutes without in-story ad blocks). Declining leaves reading unchanged, and the ad-fails field gives a no-show and a 'Couldn't unlock' path.
  - ✓ g_no_cash: The reward is 15 minutes of hidden in-story ad blocks, which has no cash value.
  - ✓ g_no_chat_content: The trigger is an app event: the third story of the day plus scrolling past an ad block. No chat content is involved.
  - ✓ g_no_free_removal: Nothing free is removed or capped. The reward only temporarily hides display ads, and the ad blocks stay as they are for users who decline.
  - ✓ g_brand_safety: The offer is a slim card in the app chrome of s24, an article screen rated safe. It is not in a conversation, and no unsafe or unknown content sits next to it.
  - ✓ c1_revealed_value: This is a product change that adds ad-free reading time. The player uses it themselves in the observed core flows f1 and f2, reading stories with in-body ad blocks, and none of it goes to another party.
  - ✓ c2_evidence: Its claims about today's app are supported by the model: in-body ADVERTISEMENT blocks (s13.e34, s20.e23, s24.e12) and the pinned next-story bar (s24.e24). The paid-plan point is worded as 'not seen', which matches the open question. Cited ids are empty, but nothing unsupported is asserted.
  - ✓ c4_protects_subscription: No paid ad-free plan was observed. The reward also expires after 15 minutes, is capped at 2 per day, and takes nothing from other users or creators.
  - ✓ c5_moment: The slim card sits above the pinned bar without covering the text, so it does not interrupt reading. It also appears right after the reader passes an ad block, which is a plausible moment of need.
  - ✓ c6_fits_simula: It is a play-to-earn unit. The reward is granted only once the game is confirmed, and the failure path withholds it. The trigger is a detectable app event.
  - ✓ c7_specific: It names the in-story ADVERTISEMENT blocks, the s24 pinned 'Next in More For You' bar and the article flow, so it is specific to this news app.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the 15-minute reward before the reader chooses to play. The decline and ad-failure fields leave the story unchanged if the reader declines or the game cannot be confirmed.
  - ✓ g_no_cash: The reward is 15 minutes without in-story ad blocks, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses a story count and scrolling past an ad block, not chat content.
  - ✓ g_no_free_removal: The proposal's decline path preserves reading with ads as before; it does not remove or cap free reading.
  - ✓ g_brand_safety: The placement is app chrome on s24, a screen rated content safe, rather than inside a conversation or next to unsafe or unknown-rated content.
  - ✓ c1_revealed_value: This product-change reward gives the player an ad-light period for their own story reading, a core flow observed in f1 and f2.
  - ✓ c2_evidence: The proposal's claims about in-story display blocks and the pinned next-story bar are supported by s13.e34, s20.e23, and s24.e12/e24/e26. It says a paid ad-free plan was not seen, rather than claiming none exists.
  - ✓ c4_protects_subscription: The reward field limits ad-light reading to 15 minutes; it grants no lasting access to a paid benefit.
  - ✗ c5_moment: The trigger and placement make the card appear while the reader is still reading a story, after scrolling past an ad block. That interrupts an ongoing task without a limit hit, locked-item tap, finished action, or persistent surface the user chooses to open.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by detectable story events, and the ad-failure field withholds the reward when the play cannot be confirmed.
  - ✓ c7_specific: The idea relies on this app's in-story ADVERTISEMENT blocks and s24's pinned 'Next in More For You' bar.

## c04 · Product change: Keep your reading streak after a missed day

- **reject**, 10/11 checks passed by every judge; dropped by code: duplicate of c02: same benefit (reading streak save)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The streak save is a manufactured scarcity on a feature that does not exist yet. The claim that the app can detect reading a story 'to the end' is asserted, not observed. The 'costs nothing to serve' and 'sponsored cards already earn' statements are unsupported predictions.
  - ✓ g_policy: The user opts in by tapping the banner. The offer copy states the reward (1 streak save, usable once every 7 days). Declining leaves the feed as before, and 'if the ad fails' gives a no-offer path and a 'Couldn't save it' path.
  - ✓ g_no_cash: The reward is a streak save inside the app, with no cash value and nothing that can be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event: the first open after a day with no story read to the end. No chat content is involved.
  - ✓ g_no_free_removal: The proposal adds a new free streak and a new capped save. 'removes nothing free' is yes, and nothing free today is removed or capped.
  - ✓ g_brand_safety: The offer is a banner under the header on s01, which is rated safe, and counts as app chrome. It is not in a chat and not next to unsafe or unknown content.
  - ✗ c1_revealed_value: This is a product_change that adds a new resource, a streak save protecting a counter the proposal itself invents. The save is not a slice of anything the app charges for. It is also not used in an observed core flow such as f1 or f2. It only guards a new gamification layer, so it fails the new-resource condition.
  - ✓ c2_evidence: The claims about today's app are supported. The Home feed s01 and related list s32 appear in flow f1, and the lack of a streak is worded as 'not seen'. The streak, save, banner and copy are what the proposal itself adds.
  - ✓ c4_protects_subscription: The save covers one missed day, is capped at once per 7 days, and gives away no paid benefit. No paid plan was observed, and nothing is taken from other users or creators.
  - ✓ c5_moment: The banner appears on app open on Home, a persistent surface, so it does not interrupt a task in progress.
  - ✓ c6_fits_simula: The unit is a play-to-earn rewarded game. The save is granted only after the play is confirmed, and the trigger is a detectable app event (an open after a missed day).
  - ✓ c7_specific: It names the Home feed s01, the category tabs, and finishing stories through to the related list s32, so it is tied to this app's screens.
- judge_2: not fixable; other concern: The model does not establish how many readers return daily or would value a streak.
  - ✓ g_policy: The offer copy states the reward is one streak save before the reader chooses to play. The decline and ad-failure fields preserve normal reading and provide a path when the game cannot load or be confirmed.
  - ✓ g_no_cash: The reward is one in-app streak save, not money or a transferable or externally exchangeable benefit.
  - ✓ g_no_chat_content: The trigger uses app-open and story-completion events, not anything the user typed, said, or wrote.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce a streak without restricting the free news reading described in m3.
  - ✓ g_brand_safety: The placement is under the Home header on s01, a content-safe screen, rather than inside a conversation.
  - ✓ c1_revealed_value: As a product change, the streak save benefits the reader personally on Home and is tied to finishing stories, as observed in core flow f1.
  - ✓ c2_evidence: s01 and core flow f1 support the Home-to-article-to-related-stories route, and the proposal says a streak was not seen rather than claiming the app has none. Daily readership is a user prediction; streak tracking is part of the proposed change.
  - ✓ c4_protects_subscription: The reward covers one missed day and is capped at one save per seven days; it does not grant lasting access to a paid benefit or take an advantage from others.
  - ✓ c5_moment: The trigger and placement put the offer on Home when the reader returns after a missed day, rather than interrupting an article in progress.
  - ✓ c6_fits_simula: The flow uses a short sponsored game triggered by detectable app events, and the ad-failure field withholds the save when play cannot be confirmed.
  - ✓ c7_specific: The idea names the s01 Home header and ties its streak to reading stories through to the s32 related list.

## c05 · Product change: Keep your reading streak after a missed day

- **reject**, 9/11 checks passed by every judge; dropped by code: duplicate of c02: same benefit (reading streak save)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The copy on failure ('your streak is still saved for now') is ambiguous about whether any reward is granted before verification. The streak has intrinsic value only if readers care about it, and that is unproven. The 6-day figure in the copy is example text, not a fixed value.
  - ✓ g_policy: The user chooses to play from the card, and the offer copy states the reward (cover 1 missed day). Declining leaves the feed and stories unchanged, and the if_the_ad_fails field gives a path for no game or an unconfirmed game.
  - ✓ g_no_cash: The reward is a one-time streak protection covering one day, with no cash value and nothing withdrawable or transferable.
  - ✓ g_no_chat_content: The trigger is an app open plus the last finished-story date, which are app events, not anything the user typed or said.
  - ✓ g_no_free_removal: The proposal adds a new streak counter and removes or caps nothing users get free today ('removes nothing free: yes').
  - ✓ g_brand_safety: The card is app chrome on the home feed s01, which is rated safe, and it is not in a chat or a message list.
  - ✓ c1_revealed_value: As a product change, the new streak save is for the player's own use, tied to finishing stories in the observed core flow f1 (read the top story to the end). None of it goes to another user or creator.
  - ✓ c2_evidence: The claims about today's app (the home feed s01, the reading flow ending at s32, display ads in s13 and s20) match the model. No streak, paywall or paid plan is worded as 'not seen', which fits the model's open question. The streak card, copy and predictions are the proposal's own additions.
  - ✓ c4_protects_subscription: The reward is a single-day streak cover, capped at 1 per week. It gives away no paid benefit (no paid plan was observed) and takes nothing from other users or creators.
  - ✓ c5_moment: The card appears on app open on the home feed, before any task starts, so it interrupts nothing.
  - ✓ c6_fits_simula: It is a play-to-earn unit where the streak cover is granted after the game is verified, and the trigger is a detectable app event (app open plus last-finished-story date).
  - ✓ c7_specific: It names the home feed s01, the read-a-story-to-the-end flow, the s32 related list, and the app's in-article ad blocks.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the one-day reward before an optional game, and the decline and ad-failure fields provide paths that leave existing feed and story access intact.
  - ✓ g_no_cash: The reward is one day of reading-streak protection, not cash or a transferable or externally exchangeable benefit.
  - ✓ g_no_chat_content: The trigger uses the home-feed opening and a finished-story counter, not anything the user typed or said.
  - ✓ g_no_free_removal: The adds and decline fields leave existing feed and story access unchanged; the streak is a new feature.
  - ✓ g_brand_safety: The placement is a card under the header on s01, which the product model rates content safe.
  - ✓ c1_revealed_value: As a product change, the reward preserves the player's own new reading streak, which is tied to finishing stories in observed core flow f1.
  - ✗ c2_evidence: The what-makes-it-work-here field says s01 is where every visit starts. The model shows visits and core flows beginning there, but does not support the claim about every visit.
  - ✓ c4_protects_subscription: The reward covers only one missed day, and the model does not establish a paid streak benefit or an advantage taken from other users.
  - ✓ c5_moment: The placement is a home-feed card shown when the reader returns after a gap, not an interruption while reading an article.
  - ✗ c6_fits_simula: The ad-failure field says an unconfirmed game leaves the user's streak 'still saved for now.' That grants the streak-protection reward without a verified play.
  - ✓ c7_specific: The proposal names the s01 home feed and ties the new counter to finishing stories, as in core flow f1.
