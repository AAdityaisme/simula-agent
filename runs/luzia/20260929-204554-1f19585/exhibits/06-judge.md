# 06 · judge

Judges: judge_1 (claude-sonnet-5-5, high), judge_2 (gpt-6-sol, high), blind (opaque ids, no lens or model names, one candidate per call). Economics mode: `annotate`. 13 candidates: 3 accepted, 2 conditional, 0 waiting on a person, 8 rejected; 3 revised once.

| Candidate | Final | Checks | Rank | Cost mark |
|---|---|---|---|---|
| c01 · Product change: One extra growth step for Toki today | accept | 11/11 | 1 | PASS |
| c08-rev · Product change: Feature your new app on Explore for 24 hours | accept | 11/11 | 0.5 | PASS |
| c06 · Product change: Your mini app shown in a community row for 24 hours | accept | 11/11 | 0 | PASS |
| c03 · Product change: A new outfit for your virtual pet Toki for 3 days | conditional | 10/11 | 1 | PASS |
| c04-rev · Product change: Show your published app in a Player Picks row for 24 hours | conditional | 10/11 | 1 | PASS |
| c07 · Product change: Unlock a gold card frame for your apps | reject | 10/11 | 1.5 | PASS |
| c09 · Product change: Cheer an app's maker and wear a supporter badge | reject | 9/11 | 1.5 | PASS |
| c04 · Product change: Show your new app in a Player Picks row for 24 hours | reject | 10/11 | 1 | PASS |
| c02-rev · Product change: 3 extra starter templates for your next app, for 24 hours | reject | 10/11 | 1 | CONDITIONAL |
| c02 · Product change: Your published app shown in Explore's Spotlight for 24 hours | reject | 8/11 | 1 | PASS |
| c08 · Product change: Feature your remix for 24 hours, credited to the original | reject | 10/11 | 0.5 | PASS |
| c10 · Product change: Your remix shown in Fresh remixes for 24 hours | reject | 9/11 | 0.5 | PASS |
| c05 · Product change: A treat outfit for Toki when you come back | reject | 11/11 | 0 | PASS |

## c01 · Product change: One extra growth step for Toki today

- **accept**, 11/11 checks passed by every judge
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The reward is cosmetic, so the value to the user is thin. The care screens after 'Meet me' were never observed, so it is unclear how a growth step fits into Toki's progression. Detecting that a user has 'met Toki' relies on app state not shown in the model.
  - ✓ g_policy: The strip is an opt-in offer, and the offer copy states the reward (1 extra growth step, once today). Declining leaves Chats home and Toki as before, and the 'if the ad fails' field covers load and verification failure.
  - ✓ g_no_cash: The reward is a cosmetic, in-app Toki growth step with no cash value and no way to transfer or withdraw it.
  - ✓ g_no_chat_content: The trigger is an app event, Chats home opening for a user who has met Toki and has not taken today's treat. It uses no chat content.
  - ✓ g_no_free_removal: The proposal only adds a bonus, and 'removes nothing free: yes' holds. Normal care still grows Toki.
  - ✓ g_brand_safety: The offer is a strip under the Toki card on s01, a screen rated safe, so it counts as app chrome. It is not inside a conversation and not next to unsafe or unknown content.
  - ✓ c1_revealed_value: As a product change, the growth step is something the player uses for their own pet in the observed Toki flow (f5, s05.e04 'Care for me to see me grow!'). None of it goes to another user or creator.
  - ✓ c2_evidence: The Toki card on home (s01.e26, s01.e27) and growth through care (s05.e04) are supported. The absence of a limit or paywall is worded as 'not seen', and the unseen care screens are acknowledged.
  - ✓ c4_protects_subscription: The reward is a cosmetic Toki growth step, not a paid benefit, and it takes nothing from other users or creators. No subscription was observed to protect.
  - ✓ c5_moment: The offer sits on a persistent surface, the Chats home Toki card, and does not interrupt a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game, and the growth step is granted only after the play is confirmed. The trigger, Chats home opening, is an app event the app can detect.
  - ✓ c7_specific: It names the Toki promo card, the Toki intro modal, and Chats home, so it would not read the same in another app.
- judge_2: not fixable; other concern: Only Toki's intro was explored; confirm that an earned growth step can be applied and displayed in the pet experience.
  - ✓ g_policy: The offer copy states the one-step reward before the user chooses to play. The decline and ad-failure fields leave normal care unchanged and withhold the treat if play cannot be confirmed.
  - ✓ g_no_cash: The reward is one cosmetic Toki growth step, not cash or an exchangeable credit.
  - ✓ g_no_chat_content: The trigger uses Chats home opening, whether the user has met Toki, and whether today's bonus was taken; it needs no message content.
  - ✓ g_no_free_removal: The adds and decline fields describe a bonus on top of normal Toki care, with nothing free removed or capped.
  - ✓ g_brand_safety: The placement is under the Toki card on safe Chats home (s01), not inside a conversation or next to unsafe content.
  - ✓ c1_revealed_value: For this product change, the player receives growth for their own Toki, which is part of the observed Toki core flow (f5) and its care-and-growth premise (m2).
  - ✓ c2_evidence: The existing Toki card and care-and-growth premise are supported by s01.e26, s01.e27, and s05.e04. The proposal phrases the absence of a seen paywall or limit as an observation, not a claim about the whole app.
  - ✓ c4_protects_subscription: The reward field limits the bonus to one growth step per day; the model identifies no paid Toki benefit that it grants lasting access to.
  - ✓ c5_moment: The placement is a strip on Chats home that the user can choose to engage with, rather than an interruption during a task.
  - ✓ c6_fits_simula: The flow offers a sponsored game from a detectable home-opening event, and the ad-failure field says an unconfirmed game does not add the treat.
  - ✓ c7_specific: The idea uses this app's Toki card on Chats home (s01.e26) and Toki's growth premise (s05.e04).

## c08-rev · Product change: Feature your new app on Explore for 24 hours

- **accept**, 11/11 checks passed by every judge, revision of c08
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The reward is granted at verification but only takes effect when a creation is published later. The proposal doesn't say how an unused grant is held, or what happens if the user never publishes.
  - ✓ g_policy: The banner is opt-in and its copy states the reward (24 hours in the Fresh creations row). Declining leaves the create screen unchanged, and the ad-fail path is covered in 'if the ad fails'.
  - ✓ g_no_cash: The reward is 24 hours of visibility in a row on Explore. It has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is the create screen (s11) opening after the user taps Create app, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The idea adds a new row and removes or caps nothing. The Trending list and its ranks are untouched ('removes nothing free: yes').
  - ✓ g_brand_safety: The banner sits in the header area of s11, a non-chat screen rated safe. It is app chrome and is not in a conversation or next to unsafe or unknown content.
  - ✓ c1_revealed_value: The new resource is Explore visibility for the player's own new app. That is used by the player in core flow f4 (explore and create mini apps), and it goes to the player rather than to others.
  - ✓ c2_evidence: The claims about the app today are supported: the Create app button (s03.e08), the s11 remix and public-once-published text (s11.e03, s11.e08), the creator byline (s13.e07), and Trending ranks (s14). The 'no limit seen' wording matches the model's open questions, and the Fresh creations row is the proposal's own addition.
  - ✓ c4_protects_subscription: The model records no paid benefit at stake. The reward expires after 24 hours and is capped at one per day. It adds a new row and leaves Trending placements untouched, so nothing is taken from other creators.
  - ✓ c5_moment: The banner appears when the user opens the create screen, at the start of creating and just before publishing. It is closable, does not block the task, and the reward is relevant to that moment.
  - ✓ c6_fits_simula: It is a sponsored game play-to-earn unit. The reward is tied to the verified play, and the trigger (s11 opening) is an app event the app can detect.
  - ✓ c7_specific: It names this app's Explore tab, the Create app button (s03.e08), the s11 create screen, and its remix templates and public creations.
- judge_2: not fixable; other concern: Specify how long an earned feature remains available if the user does not publish the next app, and how public creations qualify for promotion.
  - ✓ g_policy: The offer copy states the 24-hour feature before play, and the user chooses whether to play. The decline and ad-failure fields leave the create screen unchanged or withhold the feature.
  - ✓ g_no_cash: The reward is a 24-hour placement in the proposed Fresh creations row, not cash or an externally exchangeable benefit.
  - ✓ g_no_chat_content: The trigger event is the create screen opening after a tap on Create app; it does not require reading chat content.
  - ✓ g_no_free_removal: The adds and removes nothing free fields describe a new row and do not lock or cap an existing free feature.
  - ✓ g_brand_safety: The placement is a banner under the header of s11, a screen rated safe, rather than an offer in a conversation.
  - ✓ c1_revealed_value: As a product change, the reward gives the player a temporary feature for their own creation in Explore, part of the observed mini-app creation and exploration flow f4.
  - ✓ c2_evidence: s03.e08 supports the Create app entry point, and s11.e03, s11.e08, and s11.e10 support the described creation screen. The proposal says a creation or publishing limit was not seen, rather than claiming none exists.
  - ✓ c4_protects_subscription: The reward ends after 24 hours in a new labeled row. The proposal says the existing Trending list and its ranks remain untouched, so it does not take an existing placement from other creators.
  - ✓ c5_moment: The placement is a closable banner on the create screen, which the user chooses to open by tapping Create app on s03.
  - ✓ c6_fits_simula: The flow uses a sponsored game, the ad-failure field withholds the feature when play is not confirmed, and opening s11 is a detectable app event.
  - ✓ c7_specific: The idea relies on this app's Create app entry point (s03.e08), creation screen (s11), and Explore catalog.

## c06 · Product change: Your mini app shown in a community row for 24 hours

- **accept**, 11/11 checks passed by every judge
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The 'cited evidence' field is empty even though the text relies on s14 and s11 elements. The returning-creator row could still reduce attention on other apps, and the 'last session' and 'published app' triggers need tracking that was not observed.
  - ✓ g_policy: The user opts in through a dismissible card, and the offer copy states the reward (24 hours in the row plus a 7-day badge). Declining leaves Explore unchanged, and the 'if the ad fails' field covers both no game available and an unconfirmed game.
  - ✓ g_no_cash: The reward is a cosmetic spot in a row and a badge, with nothing withdrawable or transferable.
  - ✓ g_no_chat_content: The trigger is an app event: opening the Explore catalog with a published app and a last session 3 or more days ago. No chat content is needed.
  - ✓ g_no_free_removal: Nothing free is removed or capped; the Trending list, likes, saves and creating apps all stay as they are (the 'removes nothing free' field).
  - ✓ g_brand_safety: The offer is a card on s03, which is rated safe and is not a chat screen. It sits above the featured app, which is app chrome next to safe content.
  - ✓ c1_revealed_value: This is a product change. The reward is visibility for the player's own published app, used in the observed core flow f4 (explore and create mini apps), and it benefits the creator who plays, not another user.
  - ✓ c2_evidence: The claims about today's app are supported: ranked Explore with likes, saves and #1/#2 badges (s14.e15, s14.e18, s14.e25), and creations being public once published (s11.e08). The publishing-limit claim is worded as 'not seen', which matches the open question. The new row, badge and copy are the proposal's own additions.
  - ✓ c4_protects_subscription: The reward expires (24 hours in the row, 7-day badge) and the ranked Trending list is untouched. The separate row does not displace anyone's ranked place.
  - ✓ c5_moment: The card appears on the Explore catalog, a screen the user chose to open, so it interrupts no task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn unit and the reward is granted only once the game is confirmed (flow step 4). The trigger, a returning user with a published app, is a detectable app event.
  - ✓ c7_specific: It names the Create tab's Explore catalog, the Trending list, the like, save and rank badges, and the featured app, so it depends on this app's mini-app platform.
- judge_2: not fixable; other concern: A new promotional row could draw attention away from existing catalog cards even though it does not change their rankings.
  - ✓ g_policy: The offer copy states the 24-hour app placement and 7-day badge before an optional game. The decline and ad-failure fields preserve access to Explore and provide a retry if verification fails.
  - ✓ g_no_cash: The reward is placement for the player's mini app and a badge, not cash or a transferable equivalent.
  - ✓ g_no_chat_content: The trigger uses an Explore opening, publication eligibility, and time since the last session; it does not require chat content.
  - ✓ g_no_free_removal: The 'removes nothing free' and decline fields leave existing catalog and creation functions available.
  - ✓ g_brand_safety: The placement is a dismissible card on the safe Create–Explore screen (s03), not in or over a conversation.
  - ✓ c1_revealed_value: This product change gives the player a temporary placement for their own mini app in the Explore catalog, part of the observed explore-and-create flow f4.
  - ✓ c2_evidence: The cited s14 elements show counts and rank badges, and s11.e08 says creations are public once published. The proposal phrases the absence of a paid plan or publishing limit as 'not seen,' rather than claiming neither exists.
  - ✓ c4_protects_subscription: The reward expires after 24 hours for placement and seven days for the badge. The proposed separate row leaves the existing ranked Trending list unchanged.
  - ✓ c5_moment: The offer appears on the Explore catalog when an eligible user opens it, rather than interrupting a task in progress.
  - ✓ c6_fits_simula: The flow uses a short sponsored game and grants the placement only once the game is confirmed; opening Explore is a detectable app event.
  - ✓ c7_specific: The idea names the Create tab's Explore catalog, published mini apps, and its observed likes, saves, and rank badges.

## c03 · Product change: A new outfit for your virtual pet Toki for 3 days

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The rationale says users who keep Toki on already open the home screen to care for it. The model observes the Toki promo and intro (s01.e26, s05.e04), but not that existing user behavior or a pet-care flow) and another passes it (The claims about the Toki card, its return-visit framing and the pet being switchable on and off match s01.e26, s01.e27, s05.e04, s05.e09 and m2. The absence of a paid plan or limit is worded as not seen, and the rest is the proposal's own new content or a prediction).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: No evidence was cited, and the pet's care screen was never observed, so whether an outfit shows on the home card is an assumption. Any value depends on Toki engagement, which is unmeasured.
  - ✓ g_policy: The user taps a button on the Toki card to open the offer sheet, so it is opt-in. The offer copy states the reward (one outfit for 3 days), declining leaves the home screen unchanged, and the if_ad_fails path is defined.
  - ✓ g_no_cash: The reward is a cosmetic pet outfit lasting 3 days, which cannot be withdrawn, sold or transferred.
  - ✓ g_no_chat_content: The trigger is an app event: the first home screen open of the day with the pet turned on and its card showing. No chat content is needed.
  - ✓ g_no_free_removal: It adds a daily bonus and removes or caps nothing free, since the Toki card and pet stay as they are.
  - ✓ g_brand_safety: The button sits on a card on s01, which is rated safe and is not a chat screen. The sheet opens over that safe home screen.
  - ✓ c1_revealed_value: This is a product_change. The outfit is a new cosmetic the player keeps for their own pet, and Toki is an observed core flow (f5, m2). None of it goes to another user or creator.
  - ✓ c2_evidence: The claims about the Toki card, its return-visit framing and the pet being switchable on and off match s01.e26, s01.e27, s05.e04, s05.e09 and m2. The absence of a paid plan or limit is worded as not seen, and the rest is the proposal's own new content or a prediction.
  - ✓ c4_protects_subscription: A cosmetic outfit that expires after 3 days gives away no paid benefit, and it takes nothing from other users or creators.
  - ✓ c5_moment: The offer is a button on the persistent Toki card that the user chooses to tap. It interrupts no task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded playable. The outfit is granted only once the game is confirmed, and the trigger is a detectable app event.
  - ✓ c7_specific: It names the Toki virtual pet card on the s01 Chats home screen and depends on that feature, so it would not read the same in another app.
- judge_2: fixable; other concern: The proposed outfit display on the home card and the claim that it costs nothing to serve are untested product assumptions.
  - ✓ g_policy: The home-card button and offer sheet make play optional, and the offer copy states one outfit for three days. The decline and ad-failure fields leave the pet unchanged and withhold the reward if play is not confirmed.
  - ✓ g_no_cash: The reward field gives one in-app cosmetic outfit, not cash or a cash-equivalent.
  - ✓ g_no_chat_content: The trigger event uses a home-screen opening and pet state, not anything the user says or writes in a chat.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce an outfit without removing or capping an existing free feature.
  - ✓ g_brand_safety: The placement is a button on the Toki card and a sheet over s01 Chats home, which is rated safe; it is not inside a conversation.
  - ✓ c1_revealed_value: As a product change, the outfit is for the player's own pet on the home card used in observed core flow f5, supported by s01.e26 and s01.e27.
  - ✗ c2_evidence: The rationale says users who keep Toki on already open the home screen to care for it. The model observes the Toki promo and intro (s01.e26, s05.e04), but not that existing user behavior or a pet-care flow.
  - ✓ c4_protects_subscription: The reward and expiry fields limit the cosmetic outfit to three days; they grant no lasting paid access or advantage taken from other users.
  - ✓ c5_moment: The placement is a button on the persistent s01 home card that the user can choose to open, rather than an interruption mid-task.
  - ✓ c6_fits_simula: The flow uses a short sponsored game after a detectable home-screen event and grants the outfit only once the game is confirmed.
  - ✓ c7_specific: The trigger, placement, and reward specifically use Toki and its observed home promo card (s01.e26–e27).

## c04-rev · Product change: Show your published app in a Player Picks row for 24 hours

- **conditional**, 10/11 checks passed by every judge, revision of c04; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The 'what makes it work here' field calls 580 a like count, but s14.e29 records only the text '580,' unlike the elements explicitly labeled as like counts. That claim about the app today is unsupported) and another passes it (The claims about the app as it is today (prompt or remix creation, public once published, the Luzia's Pick badge, the Trending counts and rank badges) match s11.e03, s11.e08, s03.e19 and s14.e15/e17/e18. What was not seen is worded as not seen).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The proposal cites no evidence ids. Whether the app can detect that a user has a published app depends on My Apps (s12), which was out of scope. The row also gives a creator visibility for playing a game and could favor heavy players over other creators, though it is capped at 1 per day.
  - ✓ g_policy: The user opens the sheet by choice, the offer copy states the reward (a Player Picks spot for 24 hours), declining leaves Explore unchanged, and the ad-fail path is covered.
  - ✓ g_no_cash: The reward is a cosmetic, 24-hour spot in a row and cannot be withdrawn, sold, or moved.
  - ✓ g_no_chat_content: The trigger is an app event: the user has a published app and opens Explore. No chat content is used.
  - ✓ g_no_free_removal: The idea adds a new row and removes, locks, or caps nothing users get free today.
  - ✓ g_brand_safety: The card and sheet sit on s03 Explore, which is rated safe and is not a chat screen. They are app chrome.
  - ✓ c1_revealed_value: The new resource is the creator's own spot on Explore, used in the observed create-and-explore flow (f4). It is not a slice of a paid benefit, but it goes to the player rather than to another user.
  - ✓ c2_evidence: The claims about the app as it is today (prompt or remix creation, public once published, the Luzia's Pick badge, the Trending counts and rank badges) match s11.e03, s11.e08, s03.e19 and s14.e15/e17/e18. What was not seen is worded as not seen.
  - ✓ c4_protects_subscription: The spot is time-limited to 24 hours and capped at 3 shows per app. It is a separate row that leaves Trending order and the editor's pick unchanged, and it gives away no paid benefit.
  - ✓ c5_moment: The offer is a card on the Explore screen the user browses, not an interruption of a task in progress. It is a persistent surface the user can choose to open.
  - ✓ c6_fits_simula: The unit is a play-to-earn rewarded game, and the spot is granted only after the confirmed play. The trigger, a published app plus opening Explore, is an app event the app can detect.
  - ✓ c7_specific: The idea names Explore, the Create tab, the Trending list, and the publish-to-public behavior of this app.
- judge_2: fixable; other concern: The subscribers field says every user sees the offer, while the trigger and placement restrict it to users with a published app; clarify the eligibility wording.
  - ✓ g_policy: The offer copy states the 24-hour Player Picks reward before the game. The flow is opt-in, and the decline and ad-failure fields leave the user's app unchanged without using up the try.
  - ✓ g_no_cash: The reward is a 24-hour spot in an in-app row, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger is opening Explore with a published app; it does not require chat content.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields describe a new row, with no existing free feature removed, locked, or capped.
  - ✓ g_brand_safety: The placement is an Explore card opening a sheet on s03, which the product model rates safe; it is not in a conversation.
  - ✓ c1_revealed_value: The product-change reward lets the player show their own published mini app while using the Explore-and-create flow observed in f4.
  - ✗ c2_evidence: The 'what makes it work here' field calls 580 a like count, but s14.e29 records only the text '580,' unlike the elements explicitly labeled as like counts. That claim about the app today is unsupported.
  - ✓ c4_protects_subscription: The reward expires after 24 hours, and the proposal says the separate row does not change Trending order or the editor's pick. It does not establish that another creator loses an existing placement.
  - ✓ c5_moment: The trigger and placement put a card on Explore, a persistent surface the user chooses to open, rather than interrupting a task.
  - ✓ c6_fits_simula: The flow awards the spot once the sponsored game is confirmed, and opening Explore is a detectable app event.
  - ✓ c7_specific: The idea uses this app's Create tab, published mini apps, and Explore catalog, supported by s03 and s11.

## c07 · Product change: Unlock a gold card frame for your apps

- **reject**, 10/11 checks passed by every judge; rerun explore (human-gated)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The backing and weekly most-backed list are new features that could be gamed, and about half of the reward goes to another user. The model also does not show how a user's published app cards look, so where the frame appears is an assumption.
  - ✓ g_policy: The offer card states the reward (7-day gold frame plus 1 backing) before the play, the user opts in, the decline path leaves Explore unchanged, and there is a no-game and unconfirmed-play path.
  - ✓ g_no_cash: The reward is a cosmetic frame and a backing count. Neither can be withdrawn, sold, or exchanged outside the app.
  - ✓ g_no_chat_content: The trigger is an app event (returning to Explore after liking an app), not chat content.
  - ✓ g_no_free_removal: The idea only adds a card, a count, and a cosmetic frame. It removes or caps nothing users get free today, and Trending order and the #1 badge are unchanged.
  - ✓ g_brand_safety: The card sits in the list on s14, which is rated safe, and is not in a conversation or next to unsafe or unknown content.
  - ✓ c1_revealed_value: The frame is for the player's own published apps, which ties to the observed create-and-explore flow f4. The reward is mainly for the player, with a single backing going to the creator.
  - ✗ c2_evidence: The trigger, the offer copy, and the flow all depend on the user liking an app, but the model shows only like counts (s13.e09, s14.e15) and no like button or like mechanic. That leaves the like action unsupported as a claim about the app today.
  - ✓ c4_protects_subscription: The reward is a 7-day cosmetic that expires. The backing does not change Trending order or rank, so it takes no one's existing place, and no paid benefit is involved.
  - ✓ c5_moment: The card sits in the Explore list on a browsing surface after the user has liked an app. It does not interrupt a task in progress.
  - ✓ c6_fits_simula: This is a play-to-earn unit. The reward is granted after the verified play, and the trigger is an app-detectable event.
  - ✓ c7_specific: It names the Explore list on s14, the creator byline, like counts, the #1 rank badge, and user-published apps, all specific to this app.
- judge_2: not fixable; other concern: A weekly most-backed list could invite manipulation; the proposed per-user backing cap may not address multiple accounts.
  - ✓ g_policy: The offer copy states the 7-day frame and 1 backing before the user chooses to play. The decline and ad-failure fields leave the Explore list unchanged or grant no reward.
  - ✓ g_no_cash: The reward is an in-app cosmetic frame and a backing count; the proposal does not make either withdrawable, sellable, or exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses a catalog action and a return to Explore, not anything the user typed or said in chat.
  - ✓ g_no_free_removal: The proposal's 'removes nothing free' field and decline path preserve existing likes, saves, and app opening; the expiring frame is new.
  - ✓ g_brand_safety: The placement is a card on s14's safe Explore screen, not inside or over a conversation.
  - ✓ c1_revealed_value: As a product change, it gives the player a frame for their own published apps, used in the mini-app creation and exploration flow f4.
  - ✗ c2_evidence: The trigger event and flow assume a user can like an app during this visit. s13.e09 and s14.e15 show like counts, but no cited element or mechanic establishes an in-app liking action.
  - ✓ c4_protects_subscription: The reward field limits the frame to 7 days, and the proposal says the new backing list will not change the existing Trending order or #1 rank.
  - ✓ c5_moment: The trigger places the offer after an action and a return to Explore, rather than in the middle of that action.
  - ✓ c6_fits_simula: The flow uses a sponsored game, and the ad-failure field says an unconfirmed play grants no frame. The proposed catalog trigger is an app event the app could detect.
  - ✓ c7_specific: The idea names the Explore list, Trending section, published mini-app cards, and app like counts shown on s13 and s14.

## c09 · Product change: Cheer an app's maker and wear a supporter badge

- **reject**, 9/11 checks passed by every judge; split: c1_revealed_value; rerun explore (human-gated)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The only maker seen is 'Luzia' itself, so the value of a cheer to a real creator is unproven. The viewer-side badge has no clear use in any observed flow.
  - ✓ g_policy: The user opts in through the sheet. The offer copy states the reward (a cheer for the maker and a 7-day supporter badge). Declining just closes the sheet, and the ad-failure path is specified.
  - ✓ g_no_cash: The reward is a cosmetic 7-day badge and a cheer count, and neither can be withdrawn, sold, or transferred.
  - ✓ g_no_chat_content: The trigger is a tap on like on the app detail page, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: Nothing free is removed, locked, or capped. The proposal only adds a badge and a cheer line.
  - ✓ g_brand_safety: The offer is a sheet over s13, a non-chat screen rated safe, so it is not in a conversation and not next to unsafe or unknown content.
  - ✗ c1_revealed_value: The new resource is a cosmetic badge for a profile and app cards, and it is not a slice of anything the app charges for. It is also not something the player uses in an observed core flow, since no profile appears in the model. The cheer, which is the main pitch, goes to the maker rather than the player.
  - ✗ c2_evidence: The trigger assumes a tappable like button on s13, but the model lists only a like count (s13.e09) alongside Open app, share, remix and report. The model also shows no user profile, and the cheer line assumes a real maker to thank, yet the cited byline (s13.e07) reads 'By Luzia'.
  - ✓ c4_protects_subscription: The badge is cosmetic and expires after 7 days, so it gives away no paid benefit. The proposal leaves the Trending order and rank badges untouched, so it takes nothing from other creators.
  - ✓ c5_moment: The sheet opens right after the like is registered, which is a finished action and so a moment of need rather than an interruption.
  - ✓ c6_fits_simula: The unit is a play-to-earn sponsored game. The reward is granted only after the game is confirmed, and the trigger is an app-detectable like tap.
  - ✓ c7_specific: The idea names the app detail page (s13), its like and save counts, and the byline. It also names the Explore Trending order, so it is tied to this app's mini-app catalog.
- judge_2: not fixable; other concern: The observed s13.e07 byline is “By Luzia,” so the maker receiving a cheer on that example app is an AI, not necessarily a human creator.
  - ✓ g_policy: The offer copy states the cheer and one 7-day badge before play. The decline and ad-failure fields preserve the like and provide paths when the game cannot load or be confirmed.
  - ✓ g_no_cash: The reward field gives a cosmetic supporter badge, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event is a tap on an app detail page; it does not use chat content.
  - ✓ g_no_free_removal: The adds and decline fields leave existing likes and app-detail functionality in place.
  - ✓ g_brand_safety: The placement is a sheet over s13 App detail, which the product model rates safe, rather than an offer in a conversation.
  - ✓ c1_revealed_value: This product change gives the player a badge for their own app cards, tying their reward to the mini-app browsing and creation flow f4. The maker also receives a cheer, but the reward is not mostly for the maker.
  - ✗ c2_evidence: The trigger event, placement, and flow assume the user can tap like and have a like registered on s13. Elements s13.e09 and s13.e11 show counts, but the model does not show a like control or an observed liking action.
  - ✓ c4_protects_subscription: The badge in the reward field expires after seven days, and the proposal says the new cheer line leaves Explore ordering and rank badges unchanged.
  - ✓ c5_moment: The placement opens the sheet after the proposed like action is registered, a finished action rather than a mid-task interruption.
  - ✓ c6_fits_simula: The flow uses a chosen sponsored game, and the ad-failure field withholds the cheer when play cannot be confirmed. A button tap is an app-detectable trigger, even though this particular like control was not observed.
  - ✓ c7_specific: The idea uses s13 App detail's maker byline and like and save counts, and distinguishes its new cheer line from the Explore ranking shown at s14.e18.

## c04 · Product change: Show your new app in a Player Picks row for 24 hours

- **reject**, 10/11 checks passed by every judge; rerun explore (human-gated)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The Player Picks row may in practice move attention away from other creators' apps, and the reward mostly benefits creators. Whether creators return to Explore right after publishing was not observed.
  - ✓ g_policy: The offer is a card that opens a sheet the creator chooses to play. The offer copy states the reward (24 hours in Player Picks), declining changes nothing, and the 'if the ad fails' field covers both no game available and an unverified game.
  - ✓ g_no_cash: The reward is 24 hours of cosmetic visibility in a row, which cannot be withdrawn, sold or transferred.
  - ✓ g_no_chat_content: The trigger is an app event (publishing a mini app, then opening Explore) and needs no chat content.
  - ✓ g_no_free_removal: Nothing free is removed, locked or capped; the proposal keeps Trending order, the editor's pick and the counts unchanged.
  - ✓ g_brand_safety: The trigger screen s03 is rated safe and is not a chat screen. The offer is a card and sheet on that screen, so it counts as app chrome.
  - ✓ c1_revealed_value: This is a product_change whose new resource, visibility for the creator's own app, is used in the observed create-and-explore flow (f4). The benefit goes to the creator, who is the player.
  - ✗ c2_evidence: The proposal cites no evidence. It states unsupported facts about the app today: 'The creator lands on Explore after publishing, as today', new apps being 'hard to find', and the published app being 'listed' on Explore. No mechanic or element shows the publish-to-Explore behavior or that listing.
  - ✓ c4_protects_subscription: No paid benefit is given away. The reward expires after 24 hours, and the row is separate, so other apps keep their Trending rank.
  - ✓ c5_moment: The offer appears right after the creator finishes publishing, which is a finished action, and it is not shown in the middle of a task.
  - ✓ c6_fits_simula: The unit is a play-to-earn game, the spot is granted only after the play is confirmed, and publishing then opening Explore is an app event the app can detect.
  - ✓ c7_specific: It is built on this app's Create tab, its public mini-app publishing, and the ranked Explore screen with like and save counts.
- judge_2: fixable; other concern: The offer sheet would show a creator’s app preview alongside a sponsored offer; moderation of newly published previews is worth considering.
  - ✓ g_policy: The offer copy states that playing earns placement for the creator’s app in Player Picks for 24 hours. The flow makes play optional, and the decline and ad-failure fields leave the app unchanged without granting an unverified reward.
  - ✓ g_no_cash: The reward field grants a temporary in-app placement, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses publication and a return to Explore, not the contents of a chat.
  - ✓ g_no_free_removal: The adds and decline fields describe a new row and preserve the published app’s existing treatment; they do not remove or cap a free feature.
  - ✓ g_brand_safety: The placement is a card and sheet on s03, which the product model rates content safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: As a product change, the Player Picks spot gives the player visibility for their own app in the Explore-and-create flow observed in f4.
  - ✗ c2_evidence: The what-makes-it-work field asserts that new apps start at zero likes, and flow step 1 says creators land on Explore after publishing “as today.” Neither current behavior is established by the product model.
  - ✓ c4_protects_subscription: The reward expires after 24 hours, and the adds field places it in a new row rather than taking another app’s existing position in Trending.
  - ✓ c5_moment: The trigger follows publication, a finished action, and presents an optional card on the Explore screen.
  - ✓ c6_fits_simula: The flow uses a short sponsored game and adds the spot only once play is confirmed; publication and opening Explore are detectable app events.
  - ✓ c7_specific: The proposal names the Create–Explore screen, published mini apps, and the existing likes, saves, and ranking shown in s11 and s14.

## c02-rev · Product change: 3 extra starter templates for your next app, for 24 hours

- **reject**, 10/11 checks passed by every judge, revision of c02; rerun explore (human-gated)
- Cost mark (CONDITIONAL): Serving cost not counted: no price observed. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 3 unlocked item(s); one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The unlocked templates are new content that does not exist today, so how the 3 extra templates are chosen or produced is unspecified. The 'usual templates' wording is also loose.
  - ✓ g_policy: The user chooses to play, the offer copy states the reward (3 templates for 24 hours), declining just closes the card, and there is a failure path for no game or an unconfirmed play.
  - ✓ g_no_cash: The reward is 3 starter templates for 24 hours, which is content and has no cash value.
  - ✓ g_no_chat_content: The trigger is the user opening the Explore tab and not having taken today's offer, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The usual templates and everything on Explore stay as they are (see 'if the user declines'), so nothing free is removed or capped.
  - ✓ g_brand_safety: The offer is a list card on s03, which is rated safe, so it is app chrome and not in or over a chat.
  - ✓ c1_revealed_value: This is a product_change. The new resource, extra templates, is for the player's own use in the observed core flow f4 (explore and create mini apps), and none of it goes to other users.
  - ✗ c2_evidence: The claim that building from the templates 'costs the same as building from a free prompt today' asserts how creation is priced now. The model lists whether creating an app is limited or paid as an open question, so no element supports it.
  - ✓ c4_protects_subscription: The templates expire after 24 hours, no paid benefit is given away, and no other creator's visibility or ranking changes.
  - ✓ c5_moment: The card sits in the Explore list, a browsing surface the user has chosen to open, and it does not interrupt a task in progress.
  - ✓ c6_fits_simula: The unit is a play-to-earn rewarded game, the reward is unlocked after the verified play, and the trigger (opening Explore) is an app event the app can detect.
  - ✓ c7_specific: The idea is tied to the Explore tab, the Create app button, and the Pixel Art Pad and Gratitude Journal templates on s11.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the 3-template, 24-hour reward before the user chooses to play. The decline and ad-failure fields leave the existing experience intact and withhold the reward if play cannot be confirmed.
  - ✓ g_no_cash: The reward field gives access to starter templates in the app, not cash or something exchangeable outside it.
  - ✓ g_no_chat_content: The trigger event uses opening the Explore tab and whether today's offer was taken; it does not require chat content.
  - ✓ g_no_free_removal: The adds and decline fields keep the usual templates available and add only temporary extras.
  - ✓ g_brand_safety: The placement is a card on the s03 Explore screen, which the product model rates safe, rather than inside a conversation.
  - ✓ c1_revealed_value: This product change gives the player templates for their own app creation, a use within the observed f4 explore-and-create flow.
  - ✗ c2_evidence: The what-makes-it-work-here field calls today's app-building prompt free. The model shows an idea field at s11.e04 but leaves whether creating an app is limited or paid as an open question, so it does not support that claim.
  - ✓ c4_protects_subscription: The reward and when-the-reward-runs-out fields limit access to the extra templates to 24 hours. The proposal also leaves other creators' Explore placement unchanged.
  - ✓ c5_moment: The placement is a card on the Explore list that a user can choose while browsing, not an interruption to an ongoing task.
  - ✓ c6_fits_simula: The flow offers a sponsored game, and the ad-failure field says unconfirmed play does not unlock templates. Opening Explore is a detectable app event.
  - ✓ c7_specific: The proposal builds on the s11 starter-template cards and the s03 Create app button.

## c02 · Product change: Your published app shown in Explore's Spotlight for 24 hours

- **reject**, 8/11 checks passed by every judge; split: c1_revealed_value, c4_protects_subscription; rerun explore (human-gated)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The Spotlight row at the top of Explore pushes down the featured app and Trending, which slightly reduces other creators' visibility. Its effect on the featured pick is unstated. The offer is worded as 'everyone' but only shows to users who already have a published app.
  - ✓ g_policy: The user opts in by tapping the card, and the offer copy states the reward (Spotlight row for 24 hours). Declining leaves Explore unchanged, and the ad-fail path is given.
  - ✓ g_no_cash: The reward is a cosmetic 24-hour Spotlight placement with no cash value and nothing that can be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event: a user with a published app opens the Explore tab. No chat content is used.
  - ✓ g_no_free_removal: The idea adds a separate Spotlight row and removes, locks, or caps nothing that users get free today.
  - ✓ g_brand_safety: The offer is a card on s03 Explore, a screen rated safe. It is app chrome, not in a conversation and not next to unsafe or unknown content.
  - ✓ c1_revealed_value: Visibility for the player's own published app is a resource the player uses for themselves in core flow f4 (explore and create mini apps). The player is the creator, so the reward does not mostly go to someone else.
  - ✗ c2_evidence: The proposal states that a new creator's app 'can't reach Trending' and that new creators are 'stuck behind apps with thousands of likes'. The model records only that apps are ranked with like and save counts; it does not show how Trending is populated or that new apps cannot appear.
  - ✓ c4_protects_subscription: No subscription benefit is given away, and the placement expires after 24 hours. The row is separate and does not reorder Trending or the editor's pick.
  - ✓ c5_moment: The card appears on the Explore tab, a persistent surface the user chooses to open. It does not interrupt a task in progress.
  - ✓ c6_fits_simula: This is a play-to-earn unit. The Spotlight is granted only after the game play is confirmed, and the trigger is a detectable app event (opening Explore with a published app).
  - ✓ c7_specific: The idea depends on this app's Explore catalog, its like and save ranking, and the fact that creations are public once published.
- judge_2: not fixable; other concern: The flow should explicitly say the placement is granted once only after REWARD_VERIFIED. A one-placement-at-a-time rule for the Spotlight row is also unspecified.
  - ✓ g_policy: The offer copy states the reward before an optional game; the decline and ad-failure fields leave Explore unchanged or explain that no placement was added.
  - ✓ g_no_cash: The reward is a 24-hour Spotlight placement, not cash or an externally exchangeable asset.
  - ✓ g_no_chat_content: The trigger uses a published-app status and an Explore-tab opening, not chat content.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce a Spotlight row without locking or capping a free feature.
  - ✓ g_brand_safety: The placement is a catalog card on s03, a screen rated safe, rather than an offer in a conversation.
  - ✗ c1_revealed_value: The reward is visibility to other Explore visitors, not a resource the player uses for themselves in the observed explore-and-create flow f4; no paid slice of Spotlight is documented.
  - ✗ c2_evidence: The what-makes-it-work-here field says a new creator's app has no likes and cannot reach Trending. The cited counts and rank badges on s14 do not establish either claim.
  - ✗ c4_protects_subscription: Putting the new Spotlight row above the featured app in s03 takes visibility from creators already shown there, even if Trending and the editor's pick keep their internal ordering; no existing sale of that advantage is documented.
  - ✓ c5_moment: The trigger and placement put the offer on the Explore catalog, a persistent surface users choose to open.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by opening Explore and describes earning the placement by playing it; it does not specify granting the reward for a tap, close, or unverified start.
  - ✓ c7_specific: The idea names the Create tab, Explore catalog, published mini apps, and the featured and Trending placements shown on s03 and s14.

## c08 · Product change: Feature your remix for 24 hours, credited to the original

- **reject**, 10/11 checks passed by every judge
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The publish step and the Fresh remixes row are new and unobserved. The banner also appears before the user has published anything, so the reward is delayed. The one-per-day cap may clash with 'one feature per remix'.
  - ✓ g_policy: The banner is opt-in and the offer copy states the reward (24 hours in the Fresh remixes row). Declining leaves remixing unchanged, and 'if the ad fails' covers both no game available and an unconfirmed play.
  - ✓ g_no_cash: The reward is 24 hours of visibility in a row on Explore, which cannot be withdrawn, sold or exchanged.
  - ✓ g_no_chat_content: The trigger is a tap on the remix button, an app event, and no chat content is used.
  - ✓ g_no_free_removal: The idea adds a new row and removes, locks or caps nothing users get free today (Trending is untouched).
  - ✓ g_brand_safety: The banner sits under the header of s11, a screen rated safe, so it counts as app chrome. It is not in a chat.
  - ✓ c1_revealed_value: The new resource is visibility for the player's own remix, used in core flow f4 (explore and create mini apps). It goes to the player, not mostly to the original creator.
  - ✗ c2_evidence: The flow says tapping remix on s13 opens the create screen s11 'as today', and that publishing works 'exactly as before'. The model shows no remix-to-s11 transition (f4 goes s13 -> s03 -> s11) and no publish flow. The remix destination and the publish behavior are therefore unsupported claims about the app as it is today.
  - ✓ c4_protects_subscription: The reward is a 24-hour spot in a new row, so it expires. Trending and its ranks are untouched, so it takes no existing place from other creators, and no paid benefit is given away.
  - ✓ c5_moment: A closable banner on the create screen does not interrupt a task, and it appears when the user has just started remixing.
  - ✓ c6_fits_simula: The reward is a single grant that follows the verified play (the feature applies once the remix is published), and a remix tap is a detectable app event. The unit is a play-to-earn sponsored game.
  - ✓ c7_specific: The idea depends on this app's Explore catalog, the remix button, the creator byline and the public creations in the mini-app platform.
- judge_2: fixable
  - ✓ g_policy: The offer copy states that one remix can be featured for 24 hours. Playing is optional, declining leaves remixing unchanged, and the proposal provides paths for an unavailable or unconfirmed game.
  - ✓ g_no_cash: The reward is placement of the user's remix in a new Explore row, not cash or a cash-like asset.
  - ✓ g_no_chat_content: The trigger is a tap on the remix button at s13.e15, not the content of a chat.
  - ✓ g_no_free_removal: The proposed Fresh remixes row adds a placement; the proposal says existing remixing, publishing, and Trending ranks remain unchanged.
  - ✓ g_brand_safety: The placement is a banner on the safe, non-chat Create screen s11, not inside a conversation or next to unsafe content.
  - ✓ c1_revealed_value: This product change gives the player a temporary feature for their own remix in the mini-app creation flow f4. Credit to the original creator is secondary to featuring the player's remix.
  - ✗ c2_evidence: Flow step 2 says the remix tap opens the Create screen “as today.” The model shows a remix button at s13.e15 and a remix invitation at s11.e03, but does not observe what happens after tapping that button.
  - ✓ c4_protects_subscription: The reward expires after 24 hours, and the proposed row does not displace the existing Trending list or its ranks.
  - ✓ c5_moment: The banner appears when the user opens the Create screen after choosing to remix, rather than interrupting an action in progress.
  - ✓ c6_fits_simula: The proposal uses a sponsored game triggered by a detectable button tap, and its failure path withholds the feature when play is not confirmed.
  - ✓ c7_specific: The idea depends on this app's remix button at s13.e15, public mini-app creations at s11.e08, and Explore catalog at s03.

## c10 · Product change: Your remix shown in Fresh remixes for 24 hours

- **reject**, 9/11 checks passed by every judge; dropped by code: duplicate of c08: same benefit (Fresh remixes placement)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The reward is 'applies to next remix within 7 days' but the offer copy doesn't state that condition. The spot is also cosmetic and its value depends on whether Explore gets enough traffic, which was not observed.
  - ✓ g_policy: The offer copy states the reward (a 24-hour spot in Fresh remixes plus credit for the original maker). The user chooses to play, declining just closes the sheet, and the 'if the ad fails' field covers both no game ready and unconfirmed play.
  - ✓ g_no_cash: The reward is a cosmetic 24-hour visibility spot in a row. It cannot be withdrawn or transferred and is not cash-like.
  - ✓ g_no_chat_content: The trigger is a tap on the remix button, which is an app event. No chat content is needed.
  - ✓ g_no_free_removal: The proposal adds a new row and a credit line and removes or caps nothing free. The Trending order stays unchanged.
  - ✓ g_brand_safety: The offer is a sheet over s11 Create app prompt, which is a non-chat screen rated safe. It is not placed in any conversation.
  - ✓ c1_revealed_value: The new resource is visibility for the player's own remix, which serves the player's own use in core flow f4 (explore and create mini apps). The credit to the original maker is a secondary effect, so most of the reward does not go to someone else.
  - ✗ c2_evidence: The proposal states that tapping remix on the detail page (s13.e15) opens the create screen with the remix started. The model only shows a remix button on s13; flow f4 goes s13 back to s03 and then to s11, so where remix leads was never observed. The link between the two screens is an unsupported claim about how the app works today.
  - ✓ c4_protects_subscription: No paid benefit is given away and the spot expires after 24 hours. The row is new, so it takes no existing ranked position from other creators, and the original maker gains a credit.
  - ✗ c5_moment: The sheet appears right after the user taps remix, while they are starting to create. That is not a limit hit, a locked item, or a finished action, and it is not a persistent surface the user chose to open, so it interrupts the task.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game and the spot is granted only after the verified play. The trigger, a remix tap or the create screen opening, is an app event the app can detect.
  - ✓ c7_specific: It names this app's remix button, the Create prompt screen, public creations, the Explore catalog and the Trending ranking, so it would not read the same in another app.
- judge_2: fixable; other concern: The sheet shows an original app's preview and name; user-generated previews should be moderated before appearing alongside an ad.
  - ✓ g_policy: The offer copy states the 24-hour Fresh remixes reward before the optional game. The decline and ad-failure fields preserve normal remix creation or allow a retry.
  - ✓ g_no_cash: The reward field gives a temporary Explore row spot, not cash or a cash-like item.
  - ✓ g_no_chat_content: The trigger event is a tap on the remix button shown at s13.e15; it does not require chat content.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields describe a new row without removing or capping an existing free feature.
  - ✓ g_brand_safety: The placement is a sheet over s11, which the product model rates content safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: The reward gives the player visibility for their own remix in the Explore and create-mini-app flow observed in f4; the original maker's credit is secondary.
  - ✗ c2_evidence: The trigger event and flow claim that tapping s13.e15 opens s11 with the remix already started. The cited elements establish a remix button and a Create prompt, but do not establish that transition or prefilled state.
  - ✓ c4_protects_subscription: The reward expires after 24 hours in a new Fresh remixes row. The proposal says it leaves the existing Trending order and rank badges unchanged.
  - ✗ c5_moment: The placement automatically opens a sheet after the create screen opens with the remix started, interrupting creation. This is neither a limit hit, a locked-item tap, or a finished action, nor a persistent surface the user chooses to open.
  - ✓ c6_fits_simula: The flow uses a short sponsored game, and the ad-failure field says the spot is not added if the game cannot be confirmed.
  - ✓ c7_specific: The idea ties the reward to the s13 remix button, the s11 Create screen, and the app's Explore catalog.

## c05 · Product change: A treat outfit for Toki when you come back

- **reject**, 11/11 checks passed by every judge; dropped by code: duplicate of c03: same benefit (pet outfit)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The cited evidence field is empty, though the text references s01.e26 and s05.e04 inline. Toki's day-to-day care was not observed, so the value of an outfit to the user is untested. Whether the 'last session' signal is reliable is also unverified.
  - ✓ g_policy: The user opts in by tapping Play, the offer copy states the reward (Toki party outfit for 7 days), declining leaves the home screen unchanged, and the 'if the ad fails' field covers both no game available and an unconfirmed game.
  - ✓ g_no_cash: The reward is a 7-day cosmetic outfit for Toki, which cannot be withdrawn, sold, or transferred.
  - ✓ g_no_chat_content: The trigger is opening the app with a last session 3 or more days ago, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The proposal adds a card and a cosmetic. It removes, locks, or caps nothing users get free today ('removes nothing free: yes').
  - ✓ g_brand_safety: The card sits on s01 Chats home, which is rated safe, as a dismissible list card next to the Toki promo card. It is not in the conversation and not near unsafe or unknown content.
  - ✓ c1_revealed_value: As a product change, the outfit is a new item the player uses for their own Toki pet. Toki is an observed core flow (f5) and an observed mechanic (m2), and none of the reward goes to another user.
  - ✓ c2_evidence: The claims about today's app are Toki as a pet that grows when cared for and is promoted on home (m2, s01.e26, s05.e04), and no paid plan, limit, or currency seen. All of these are supported, and the 'not seen' wording is appropriate. The new card, outfit, and copy are what the proposal itself adds.
  - ✓ c4_protects_subscription: The reward is a cosmetic that expires after 7 days. It takes nothing from other users and gives away no paid benefit.
  - ✓ c5_moment: The offer appears as a card on the home screen when the user opens the app after an absence. It interrupts no task and sits on a persistent surface.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded playable. The outfit is granted once the game is confirmed, and the trigger (app open plus last-session time) is an app event the app can detect.
  - ✓ c7_specific: It names the Chats home screen (s01), the Toki promo card, and Toki's growth mechanic, so it would not read the same in another app.
- judge_2: not fixable; other concern: The proposal shows the outfit on the new card and reward screen but does not say whether it remains visible in Toki's existing intro or later pet experience.
  - ✓ g_policy: The offer copy states one Toki outfit for seven days before play, and the flow starts the game only after the user taps Play. The decline and ad-failure fields leave the existing app available and provide a retry path.
  - ✓ g_no_cash: The reward field gives a temporary cosmetic outfit for Toki, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses an app open and time since the last session, not anything the user said or wrote.
  - ✓ g_no_free_removal: The proposal says it removes nothing free; the seven-day expiry applies only to the new outfit.
  - ✓ g_brand_safety: The placement is a dismissible card on s01 Chats home, which is rated safe, beside the existing Toki promo rather than inside a conversation.
  - ✓ c1_revealed_value: This product change gives the player a cosmetic for their own Toki, a feature encountered in the observed f5 Toki core flow; it need not be a paid benefit.
  - ✓ c2_evidence: The claims about today's Toki promotion and growth are supported by s01.e26 and s05.e04. The proposal describes a paid plan and limit as not seen, rather than asserting that neither exists.
  - ✓ c4_protects_subscription: The reward field limits the new outfit to seven days, and it does not take visibility or another benefit from users or creators.
  - ✓ c5_moment: The placement is a dismissible card on s01 Chats home after the user returns, not an interruption in the middle of a task.
  - ✓ c6_fits_simula: The flow is play-to-earn, grants the outfit only once the game is confirmed, and uses app-open and last-session events as its trigger.
  - ✓ c7_specific: The idea depends on s01's Toki promo and the pet introduced in s05, rather than a generic app surface.
