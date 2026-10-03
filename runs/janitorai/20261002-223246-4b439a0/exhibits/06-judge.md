# 06 · judge

Judges: judge_1 (claude-sonnet-5-5, high), judge_2 (gpt-6-sol, high), blind (opaque ids, no lens or model names, one candidate per call). Economics mode: `annotate`. 10 candidates: 2 accepted, 2 conditional, 0 waiting on a person, 6 rejected; 0 revision(s) of 0 idea(s).

| Candidate | Final | Checks | Rank | Cost mark |
|---|---|---|---|---|
| c01 · Existing opportunity: Faster replies for one hour | accept | 12/12 | 0.5 | PASS |
| c05 · Existing opportunity: 5× context for better memory on your next 5 replies | accept | 12/12 | 0 | CONDITIONAL |
| c04 · Product change: A gold ring around your profile picture for 24 hours | conditional | 9/12 | 1 | PASS |
| c08 · Product change: Wear the golden checkmark for 24 hours by backing a creator | conditional | 11/12 | 0.5 | PASS |
| c06 · Existing opportunity: Priority routing for faster replies for one hour | reject | 12/12 | 0.5 | PASS |
| c07 · Existing opportunity: Faster replies with this smaller creator's character for 24 hours | reject | 11/12 | 0.5 | PASS |
| c03 · Existing opportunity: Double the memory for your next 10 messages | reject | 10/12 | 0.5 | CONDITIONAL |
| c09 · Existing opportunity: Faster replies for your next 20 replies today | reject | 11/12 | 0.25 | PASS |
| c02 · Existing opportunity: Golden checkmark next to your username for 24 hours | reject | 11/12 | 0 | PASS |
| c10 · Existing opportunity: Golden checkmark next to your username for 24 hours | reject | 11/12 | 0 | PASS |

## c01 · Existing opportunity: Faster replies for one hour

- **accept**, 12/12 checks passed by every judge
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l4 "Priority routing for faster replies", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: exp2 notes the explorer's plan was not recorded, so 'a free user feels this in every reply' is an inference from timing. Whether free replies are slower than Plus replies is also unobserved. Neither affects any check.
  - ✓ g_policy: The user opts in by choosing to play. The offer copy states the reward (faster replies for 1 hour) before the game. Declining closes the sheet and paywall as today, and the ad-fail path is defined.
  - ✓ g_no_cash: The reward is one hour of faster reply routing, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is a tap on the paywall Close button (s14.e21), which is an app event and needs no chat content.
  - ✓ g_no_free_removal: Nothing free is removed, locked or capped. The idea only adds a temporary boost, and the 'removes nothing free' field says so.
  - ✓ g_brand_safety: The sheet opens over the Janitor Plus paywall (s14), which is rated safe and is not a chat screen. It is not placed in a conversation or next to unknown or unsafe content.
  - ✓ c1_revealed_value: Existing anchor: the app charges for priority routing (s14.e10 in the Plus plan at $12.99/month), so the resource the reward gives is monetized.
  - ✓ c2_evidence: The claims about the paywall bullet, the $12.99 price, the Close button and the roughly 92 s replies (exp1) are supported. 'No message limit seen' matches exp2. The rest is the proposal's own new copy or prediction.
  - ✓ c3_spares_payers: The subscribers field says Plus members never see the offer. Free users see a sample of a Plus benefit, which passes.
  - ✓ c4_protects_subscription: The boost expires after 1 hour and is capped at 2 per day, so it gives no lasting access. Priority routing is already a sold advantage.
  - ✓ c5_moment: The offer appears after the user closes the paywall they opened. That is a persistent surface the user chose, not an interruption of a task.
  - ✓ c6_fits_simula: It is a play-to-earn unit. The reward is granted after the verified game, with an explicit not-confirmed path. The trigger (tapping Close) is a detectable app event.
  - ✓ c7_specific: It names the Janitor Plus paywall, the 'Priority routing for faster replies' benefit, its $12.99 price and the long reply times of this app.
- judge_2: not fixable; other concern: The reward field's “1 faster replies” is ambiguous beside copy promising faster replies for an hour; clarify whether the boost covers one reply or all replies during that hour.
  - ✓ g_policy: The offer copy states faster replies for one hour before the optional game. The decline and ad-failure paths close the sheet without changing chat access or granting an unverified reward.
  - ✓ g_no_cash: The reward is temporary priority routing within the app, not cash or a transferable benefit.
  - ✓ g_no_chat_content: The trigger is tapping Close on the Janitor Plus paywall (s14.e21); it does not use message content.
  - ✓ g_no_free_removal: The proposal removes no free feature or chat access; its two-per-day cap applies only to the new rewarded offer.
  - ✓ g_brand_safety: The offer is a sheet over the Janitor Plus paywall (s14), which the product model rates safe, rather than an insertion into a conversation.
  - ✓ c1_revealed_value: The existing-anchor reward samples priority routing, a benefit sold by Janitor Plus (s14.e10).
  - ✓ c2_evidence: The paywall price, priority-routing benefit, and Close control are shown on s14; exp1 supports the measured reply time, and exp2 supports the statement that no message limit was seen.
  - ✓ c3_spares_payers: The proposal's subscribers field says Janitor Plus members never see the offer.
  - ✓ c4_protects_subscription: The reward field limits priority routing to one hour rather than granting lasting access to the Plus benefit.
  - ✓ c5_moment: The sheet appears after the user taps Close on the paywall, a finished action, rather than during an ongoing chat task.
  - ✓ c6_fits_simula: The flow uses a chosen sponsored game triggered by a detectable Close tap, and the ad-failure path says an unconfirmed game gives no reward.
  - ✓ c7_specific: The idea depends on the Janitor Plus paywall's priority-routing benefit (s14.e10) and its Close control (s14.e21).

## c05 · Existing opportunity: 5× context for better memory on your next 5 replies

- **accept**, 12/12 checks passed by every judge
- Cost mark (CONDITIONAL): Costs ~$0.0105 per reward to serve; pays for itself above $10.50 eCPM at 2k context ($18.00 at 8k). It is a sample of the paid benefit l3 "5× context for better memory", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 5 replies of 800 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The offer gives extra memory to free users right when they decline the paywall, and an unconfirmed play can be retried on the same visit despite the 1-per-7-days cap. Both are minor tensions. The model cost is acknowledged in the rationale.
  - ✓ g_policy: The user chooses to play from the sheet, whose offer copy states the reward (5 replies with 5× context, until midnight). Declining returns to the side menu as before, and the ad-failure path is stated.
  - ✓ g_no_cash: The reward is 5 replies with 5× context, which is a temporary in-app capability and has no cash value.
  - ✓ g_no_chat_content: The trigger is tapping close on the paywall after 7 or more days away, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The proposal states it removes nothing free and adds no cap. It only offers a temporary sample of a paid benefit.
  - ✓ g_brand_safety: The offer is a sheet over the Janitor Plus paywall s14, which is rated safe, and it is not in or near any chat.
  - ✓ c1_revealed_value: The existing_anchor reward gives context memory, which the app charges for: m2 and ledger l3 list 5× context as a $12.99/month Plus benefit.
  - ✓ c2_evidence: The claims about the app are supported: s14.e09 and s14.e03 for the Plus benefit and price, s20.e43 for 17-message sessions, and exp1 for roughly 3,200-character replies. The proposal states that free context limits were not observed. The new sheet, copy and reward are its own additions.
  - ✓ c3_spares_payers: The proposal says Janitor Plus members never see the offer, and the offer is shown only to free users. The paid plan is not placed at risk.
  - ✓ c4_protects_subscription: The reward is a sample that ends: 5 replies, expiring at midnight local time. It does not grant lasting access and takes nothing from other users or creators.
  - ✓ c5_moment: The offer appears right after the user closes the paywall, which is a finished action on a screen the user opened themselves. It does not interrupt a chat or other task.
  - ✓ c6_fits_simula: The unit is a play-to-earn sponsored game. The reward is added only once the game is confirmed, and the trigger (paywall close after 7 or more days away) is detectable by the app.
  - ✓ c7_specific: The idea relies on this app's Janitor Plus paywall s14, its '5× context for better memory' line, the $12.99 price and the Miro chat list.
- judge_2: not fixable; other concern: The rationale itself flags that the cost of five larger-context replies may exceed ad revenue in some markets. Keep the post-reward note out of the unsafe chat conversation.
  - ✓ g_policy: The offer copy states the 5-reply, 5×-context reward before the user chooses to play. The decline and ad-failure fields preserve the user's prior state and provide failure paths.
  - ✓ g_no_cash: The reward field grants temporary in-app context for replies, not cash or a transferable equivalent.
  - ✓ g_no_chat_content: The trigger event uses time since the previous visit and a paywall-close tap, not the content of a chat.
  - ✓ g_no_free_removal: The proposal adds a temporary paid-benefit sample and, as its removes-nothing-free field states, does not take away an existing free feature.
  - ✓ g_brand_safety: The placement is a sheet over the safe Janitor Plus paywall (s14), not an offer in or beside the unsafe conversation screen.
  - ✓ c1_revealed_value: This existing opportunity samples 5× context, a benefit of the paid Janitor Plus plan shown at s14.e09.
  - ✓ c2_evidence: The paywall's memory benefit and price are supported by s14, while s20 shows a 17-message Miro session and exp1 supports the approximate reply length. The proposal identifies the unobserved free context limit as unobserved rather than claiming one exists.
  - ✓ c3_spares_payers: The offered-to and subscribers fields restrict the offer to free users; Janitor Plus members, whose plan includes 5× context under m2, never see it.
  - ✓ c4_protects_subscription: The reward is limited to five replies and expires at midnight, rather than granting lasting access to the paid benefit.
  - ✓ c5_moment: The trigger event opens the sheet after the user taps to close the paywall, a finished action rather than an interruption to an ongoing chat.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by a detectable paywall-close tap. The ad-failure field says an unconfirmed game grants no reward, consistent with granting it only after verified play.
  - ✓ c7_specific: The idea uses the Janitor Plus paywall's specific 5×-context benefit (s14.e09) and its close action.

## c04 · Product change: A gold ring around your profile picture for 24 hours

- **conditional**, 9/12 checks passed by every judge; split: c1_revealed_value, c2_evidence, c3_spares_payers
- Condition: The judges split: on c1_revealed_value, one fails it (For a product_change, the new resource (the gold ring) must be a slice of something the app charges for or something the player uses in a core flow. The proposal itself says 'piece of a paid benefit: none' and that the ring is 'not sold'. The ring is a cosmetic shown to other users, and no observed core flow (f1-f6) uses it) and another passes it (The product change gives the player a ring on their own profile, s05, which is used in the observed character-creation flow f5. The reward does not go to another user or creator); on c2_evidence, one fails it (The rationale claims subscribers otherwise see no ads. The model does not establish that: exp2 observed no ad, but the account's plan was not recorded) and another passes it (The golden checkmark claim is supported by s14.e12 and s14.e25. The profile content and public profile link are supported by s05, and the ring itself is the proposal's new feature. The claim that subscribers 'otherwise see no ads' is unverified (see other_concern)); on c3_spares_payers, one fails it (Paying users see the offer, and the offer copy and what-makes-it-work-here field pitch the ring as an extension of the golden checkmark sold by Plus in s14.e12. That is more of what their plan sells) and another passes it (Subscribers see a ring that the plan does not include, and it sits on the profile screen, which is not a paid area. Nothing in the model shows that Plus removes ads).
- Cost mark (PASS): Costs nothing extra to serve. The check can't tell whether it gives a place ahead of other users, which apps sell as a boost: no reward kind types visibility. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The rationale says subscribers 'otherwise see no ads', but the model does not show that Plus removes ads (the plan of the exploring account was not recorded). The idea also assumes the app can detect subscriber status and that other users can see the ring. The ring only benefits payers, and the reward goes largely to how other users see the player.
  - ✓ g_policy: The subscriber chooses to play from the card, and the offer copy states the reward (a gold ring for 24 hours). Declining just closes the card, and a failure path exists (the card hides if no game is available, and a note appears if the play can't be confirmed).
  - ✓ g_no_cash: The reward is a cosmetic profile ring that expires after 24 hours and has no cash value.
  - ✓ g_no_chat_content: The trigger is a subscriber opening their own profile (s05), which is an app event, and it needs no chat content.
  - ✓ g_no_free_removal: Nothing free is removed or capped. The offer is additive and shown only to subscribers.
  - ✓ g_brand_safety: The card is app chrome on s05 Profile, which is rated safe, and it is not in a conversation or next to unsafe or unknown content.
  - ✗ c1_revealed_value: For a product_change, the new resource (the gold ring) must be a slice of something the app charges for or something the player uses in a core flow. The proposal itself says 'piece of a paid benefit: none' and that the ring is 'not sold'. The ring is a cosmetic shown to other users, and no observed core flow (f1-f6) uses it.
  - ✓ c2_evidence: The golden checkmark claim is supported by s14.e12 and s14.e25. The profile content and public profile link are supported by s05, and the ring itself is the proposal's new feature. The claim that subscribers 'otherwise see no ads' is unverified (see other_concern).
  - ✓ c3_spares_payers: Subscribers see a ring that the plan does not include, and it sits on the profile screen, which is not a paid area. Nothing in the model shows that Plus removes ads.
  - ✓ c4_protects_subscription: The ring lasts only 24 hours, gives away no main plan benefit, and takes nothing from other users or creators.
  - ✓ c5_moment: The offer is a card on the user's own profile, which is a persistent surface the user chooses to open, and it interrupts no task.
  - ✓ c6_fits_simula: It is a play-to-earn unit with the reward granted after the verified play, triggered by the app event of opening the profile while subscribed with no ring active.
  - ✓ c7_specific: It names the s05 Profile screen and Janitor Plus's golden checkmark benefit, so it is tied to this app.
- judge_2: fixable; other concern: The proposal says others can see the ring, but its flow only specifies display on the subscriber's own profile; public-profile display needs implementation detail.
  - ✓ g_policy: The offer copy states one gold ring for 24 hours before the user chooses to play. The decline and ad-failure fields leave the profile unchanged and provide a retry path.
  - ✓ g_no_cash: The reward field gives a cosmetic profile ring, not cash or a transferable item.
  - ✓ g_no_chat_content: The trigger event uses a profile opening and ring status, not chat content.
  - ✓ g_no_free_removal: The removes-nothing-free and subscribers fields leave free users' existing access unchanged.
  - ✓ g_brand_safety: The placement is a card on the user's own profile, s05, which is rated safe; it is not in a conversation.
  - ✓ c1_revealed_value: The product change gives the player a ring on their own profile, s05, which is used in the observed character-creation flow f5. The reward does not go to another user or creator.
  - ✗ c2_evidence: The rationale claims subscribers otherwise see no ads. The model does not establish that: exp2 observed no ad, but the account's plan was not recorded.
  - ✗ c3_spares_payers: Paying users see the offer, and the offer copy and what-makes-it-work-here field pitch the ring as an extension of the golden checkmark sold by Plus in s14.e12. That is more of what their plan sells.
  - ✓ c4_protects_subscription: The reward field limits the ring to 24 hours, and the proposal does not take ordered placement or another limited benefit from users or creators.
  - ✓ c5_moment: The trigger and placement put the card on the user's own profile, a surface they choose to open, rather than interrupting an in-progress task.
  - ✓ c6_fits_simula: The flow uses a sponsored game after a detectable profile-opening event, and the ad-failure field says an unconfirmed game does not add the ring.
  - ✓ c7_specific: The idea names the s05 profile avatar and the golden checkmark benefit on the s14 Janitor Plus paywall.

## c08 · Product change: Wear the golden checkmark for 24 hours by backing a creator

- **conditional**, 11/12 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The rationale claims users cannot get the badge without paying, and the when-the-reward-runs-out field says Plus keeps it next to their name 'for good.' The paywall shows a Plus badge benefit on a cancellable monthly plan (s14.e03–e12), but does not support either absolute claim about the current app) and another passes it (The golden checkmark as a Plus benefit is supported by s14.e12 and s14.e25. The like button (s08.e16), the creator handle (s08.e12), and the Hidden Gems feed that lists Parkour Reborn (s01) are observed. The creator-notice, cost, and publishing-impact statements are the proposal's own additions or predictions).
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l6 "Golden checkmark next to your username", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The 'gold verified badge' could make other users think the wearer is a verified or paying member. The creator notice is a new notification type, and the app's only observed notification is for comments (l11). Tying the offer to a Hidden Gems origin needs the app to detect that the page was opened from that feed.
  - ✓ g_policy: The user opts in on a sheet that states the reward (golden checkmark for 24 hours) before the game. Declining only closes the sheet and the like stays saved, and the ad-fail path is covered.
  - ✓ g_no_cash: The reward is a cosmetic badge for 24 hours, with no cash value and nothing withdrawable or transferable.
  - ✓ g_no_chat_content: The trigger is a like tap on the s08 character page, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The idea adds a temporary perk and removes or caps nothing free ('removes nothing free: yes').
  - ✓ g_brand_safety: The offer is a sheet over s08, a non-chat screen rated safe. It is not in the conversation and is not next to unsafe or unknown content.
  - ✓ c1_revealed_value: As a product change, the new resource is a slice of something the app charges for: the golden checkmark is a Janitor Plus benefit (s14.e12, l6). The player gets the badge, and the creator gets only a notice.
  - ✓ c2_evidence: The golden checkmark as a Plus benefit is supported by s14.e12 and s14.e25. The like button (s08.e16), the creator handle (s08.e12), and the Hidden Gems feed that lists Parkour Reborn (s01) are observed. The creator-notice, cost, and publishing-impact statements are the proposal's own additions or predictions.
  - ✓ c3_spares_payers: Plus members already have the checkmark and never see the sheet ('subscribers' field). The reward is a slice of the plan, but only non-payers are shown it.
  - ✓ c4_protects_subscription: The badge expires after 24 hours and is only a cosmetic sample of one Plus perk, not the main benefit. It takes nothing from other users or creators, since the creator only gets a notice.
  - ✓ c5_moment: The sheet appears after the like action finishes, which is a finished-action moment and not an interruption of a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn unit that grants the reward once the game is confirmed. The trigger, a like tap on s08, is an app event the app can detect.
  - ✓ c7_specific: It is built on the Janitor Plus golden checkmark, the Hidden Gems character page s08, the like button, and the creator handle, so it would not read the same in another app.
- judge_2: fixable; other concern: Requiring a like before the offer could encourage badge-seeking likes rather than genuine appreciation.
  - ✓ g_policy: The offer copy states the 24-hour checkmark before the user chooses to play. The decline and ad-failure fields preserve the saved like and provide a path when the game is unavailable or cannot be confirmed.
  - ✓ g_no_cash: The reward field gives a temporary cosmetic checkmark, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event is a tap on the like button at s08.e16; it does not require anything the user typed or said.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce temporary badge access without removing, locking, or capping an existing free feature.
  - ✓ g_brand_safety: The placement is a sheet over s08, which the product model rates safe, rather than an offer in a conversation.
  - ✓ c1_revealed_value: For this product change, the player's 24-hour checkmark is a limited slice of the paid benefit listed at s14.e12. The creator's notice is secondary.
  - ✗ c2_evidence: The rationale claims users cannot get the badge without paying, and the when-the-reward-runs-out field says Plus keeps it next to their name 'for good.' The paywall shows a Plus badge benefit on a cancellable monthly plan (s14.e03–e12), but does not support either absolute claim about the current app.
  - ✓ c3_spares_payers: The offered-to and subscribers fields restrict the sheet to free users; Plus members, whose plan includes the checkmark at s14.e12, do not see it.
  - ✓ c4_protects_subscription: The reward field expires the checkmark after 24 hours, and the proposal gives the creator a notice rather than taking another creator's placement or visibility.
  - ✓ c5_moment: The placement opens after the like at s08.e16 is saved, so the offer follows a completed action.
  - ✓ c6_fits_simula: The flow calls for a short sponsored game and grants the reward once play is confirmed; the trigger is the detectable like-button tap at s08.e16.
  - ✓ c7_specific: The idea ties the Plus checkmark at s14.e12 to liking a character reached through this app's Hidden Gems feed.

## c06 · Existing opportunity: Priority routing for faster replies for one hour

- **reject**, 12/12 checks passed by every judge; dropped by code: duplicate of c01: same benefit (faster replies)
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l4 "Priority routing for faster replies", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The 'back after 5+ days' trigger and the 3-day window depend on the app tracking last-active time, which the product model does not show. Whether free users actually get slower routing is unobserved, since the measured account's plan was not recorded.
  - ✓ g_policy: The user opts in by tapping the card, and the offer copy states the reward and its size (60 minutes of priority routing). Declining ('Not now') leaves the app unchanged, and the ad-fails field gives a path for no game or an unverified play.
  - ✓ g_no_cash: The reward is 60 minutes of queue priority, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is opening the side menu after 5 or more days away, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The proposal adds an optional boost and says it removes nothing free, so nothing users get free today is locked or capped.
  - ✓ g_brand_safety: The card sits in the side menu drawer (s06), which is rated safe and counts as app chrome. It is not in the conversation and is not over a chat or unsafe screen.
  - ✓ c1_revealed_value: Priority routing is a charged resource, listed as a Plus benefit in l4 / s14.e10 on the paywall, so the existing_anchor reward targets something the app charges for.
  - ✓ c2_evidence: The claims about the app are supported: the paywall lists priority routing (s14.e10 / m2), the side menu has the upgrade banner (s06.e50), and exp1 shows replies finishing in about 92 s. The proposal also admits the measured plan was not recorded, and its other statements are predictions or its own new elements.
  - ✓ c3_spares_payers: The card is shown only to free users and subscribers never see it, so paying users are not shown a slice of their own plan.
  - ✓ c4_protects_subscription: The reward expires after 60 minutes, so it is a time-limited sample and not lasting access. It also does not give away the plan's main benefit. Priority routing is already sold in Plus, so queue priority is not taken from others.
  - ✓ c5_moment: The offer sits on a persistent surface the user chooses to open (the side menu) and does not interrupt any task.
  - ✓ c6_fits_simula: It is a play-to-earn unit with the reward tied to the verified play (the ad-fails field covers an unconfirmed game), and the trigger is a detectable app event (side menu opened after an absence).
  - ✓ c7_specific: It names the side-menu 'Upgrade to Janitor Plus' banner (s06.e50), the Plus priority-routing line (s14.e10) and the My Chats landing screen, so it is specific to this app.
- judge_2: not fixable
  - ✓ g_policy: The offer copy states the 60-minute reward before the user chooses to play. The decline and ad-failure fields preserve normal access and withhold the reward if play cannot be confirmed.
  - ✓ g_no_cash: The reward is 60 minutes of in-app priority routing, not cash or an externally exchangeable benefit.
  - ✓ g_no_chat_content: The trigger uses a side-menu opening and time since the user's last visit, not what anyone wrote in a chat.
  - ✓ g_no_free_removal: The proposal removes nothing free; its reward samples priority routing, a Janitor Plus benefit shown at s14.e10.
  - ✓ g_brand_safety: The placement is a card in the safe side-menu drawer s06, under its upgrade banner, not in a conversation or beside unsafe content.
  - ✓ c1_revealed_value: This existing-anchor reward gives temporary access to priority routing, which the Janitor Plus paywall lists as a paid benefit at s14.e10.
  - ✓ c2_evidence: The paywall and upgrade-banner claims are supported by s14.e10 and s06.e50. The roughly 92-second measured reply completion time and the unrecorded account plan match exp1 and exp2.
  - ✓ c3_spares_payers: The subscribers field says Janitor Plus members, who already receive priority routing under m2, never see the offer.
  - ✓ c4_protects_subscription: The reward expires after 60 minutes rather than granting lasting access. Any priority advantage is one the app already sells through Janitor Plus at s14.e10.
  - ✓ c5_moment: The offer appears on the side menu, a persistent surface the user chooses to open, rather than interrupting an active chat.
  - ✓ c6_fits_simula: The flow uses a short sponsored game triggered by an app-detectable menu opening; the ad-failure field says unconfirmed play does not add the reward.
  - ✓ c7_specific: The idea uses this app's Janitor Plus priority-routing benefit and places its card under the side-menu upgrade banner s06.e50.

## c07 · Existing opportunity: Faster replies with this smaller creator's character for 24 hours

- **reject**, 11/12 checks passed by every judge; dropped by code: duplicate of c01: same benefit (faster replies)
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l4 "Priority routing for faster replies", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The proposal says Hidden Gems 'ranks' characters by engaging conversations, but s01.e15 only says the feed shows such characters. The claim that faster replies bring the creator more chats is a prediction, and the claim that the reward costs the app nothing is unverified. The proposal's copy also says Plus keeps faster replies on for 'every character, every day', which is stronger than the paywall line.
  - ✓ g_policy: The user opts in by tapping the card. The offer copy states the reward (faster replies, 24 hours, this character), declining leaves the page unchanged, and the ad-fail path is covered.
  - ✓ g_no_cash: The reward is 24 hours of queue priority in chats with one character. It has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is opening a character page from Hidden Gems, which is an app event. No chat content is used.
  - ✓ g_no_free_removal: The proposal removes nothing free (removes nothing free: yes). Queue priority is a resource that costs the app compute, and the reward only adds to it.
  - ✓ g_brand_safety: The card sits on s08, which is rated safe and is not a chat screen. It is a page card, not a message, and nothing unsafe or unknown is next to it.
  - ✓ c1_revealed_value: Priority routing for faster replies is sold on the Plus paywall (s14.e10 / l4), so the app charges for the resource the reward gives.
  - ✓ c2_evidence: The claims about the app are supported by s14.e10, s08.e10, s08.e12, s01.e15 and the roughly 92 s measured reply time in exp1. The proposal also flags that the plan of the explored account wasn't recorded. The remaining claims are predictions or new copy.
  - ✓ c3_spares_payers: Plus members never see the card (the subscribers field says so), so paying users are not shown a slice of their own plan.
  - ✓ c4_protects_subscription: The reward is a sample that ends after 24 hours, covers one character, and is capped at once a day. It does not remove the memory and swipes benefits that make up the rest of Plus. Priority routing is something the app already sells, so the advantage it gives is not taken for free from other users.
  - ✓ c5_moment: The card appears on a character detail page before chatting starts. This is a browsing moment, not the middle of a task, so no interruption fail condition applies.
  - ✓ c6_fits_simula: This is a play-to-earn rewarded playable. The reward is granted only after the game is confirmed (flow step 4), and the trigger is opening the s08 screen, which the app can detect.
  - ✓ c7_specific: The idea names the s08 character page, Hidden Gems, the smaller-creator framing and the Plus priority-routing perk, so it is specific to this app.
- judge_2: fixable; other concern: The rationale's claim that faster routing costs the app nothing extra is not established by the product model.
  - ✓ g_policy: The offer copy states faster replies with this character for 24 hours before the user chooses to play. The decline and ad-failure fields preserve normal use and provide a retry path.
  - ✓ g_no_cash: The reward is faster replies in chats with one character, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger is opening a character page from Hidden Gems; it does not require reading what the user wrote in a chat.
  - ✓ g_no_free_removal: The proposal says it removes nothing free, and declining leaves the character page and chats working as before.
  - ✓ g_brand_safety: The placement is a card on s08, a character-detail screen rated safe, rather than inside a conversation.
  - ✓ c1_revealed_value: For this existing-anchor idea, the reward is a limited slice of priority routing for faster replies, a Janitor Plus benefit shown at s14.e10.
  - ✗ c2_evidence: The 'what makes it work here' field says Hidden Gems ranks characters by engaging conversations. s01.e15 says Hidden Gems shows recent characters from smaller creators with engaging conversations, but does not establish ranking by engagement.
  - ✓ c3_spares_payers: The 'offered to' and 'subscribers' fields restrict the card to free users, so Janitor Plus members do not see an offer for a benefit their plan includes.
  - ✓ c4_protects_subscription: The reward limits faster replies to one character for 24 hours; it does not grant lasting access to the Janitor Plus benefit.
  - ✓ c5_moment: The placement is a card on the character page the user chose to open, before starting a chat, rather than an interruption mid-conversation.
  - ✓ c6_fits_simula: The trigger is a detectable character-page opening, and the flow grants the reward once the sponsored game is confirmed, not on a tap or an unverified start.
  - ✓ c7_specific: The idea uses the Hidden Gems character page at s08 and the Janitor Plus priority-routing benefit at s14.e10.

## c03 · Existing opportunity: Double the memory for your next 10 messages

- **reject**, 10/12 checks passed by every judge; split: c2_evidence
- Cost mark (CONDITIONAL): Costs ~$0.0210 per reward to serve; pays for itself above $21.00 eCPM at 2k context ($36.00 at 8k). It may give away something the app could sell (more of the paid benefit l3 "5× context for better memory" to users who pay for it); that lost sale isn't counted. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 10 replies of 800 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The card sits where free users see the upgrade banner, but the model does not record whether subscribers also see that banner. The explorer's plan was not recorded. The reward is a context multiplier the app may not support technically (10× is not an existing tier), and the 'brings subscribers back daily' goal may only reinforce a benefit they already pay for.
  - ✓ g_policy: The subscriber chooses to play, the offer copy states the reward (10× memory for the next 10 messages in one chat), declining leaves the 5× plan unchanged, and the 'if the ad fails' field covers no game and unconfirmed plays.
  - ✓ g_no_cash: The reward is a temporary context-memory boost, which cannot be withdrawn, sold or transferred.
  - ✓ g_no_chat_content: The trigger is a subscriber opening the side menu, which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The proposal removes nothing free; 'removes nothing free: yes' and the card is only an addition for subscribers.
  - ✓ g_brand_safety: The offer card sits on s06, the side menu drawer, which is rated safe and counts as app chrome. It is not in the conversation, and the follow-up note on My Chats is not the offer itself.
  - ✓ c1_revealed_value: This is an existing_anchor, and the app charges for memory: s14.e09 sells '5× context for better memory' at $12.99/month.
  - ✓ c2_evidence: The cited claims are supported: s14.e09, s14.e01, s20 message counts up to 17, the exp1 reply length of about 3,200 characters, and the 'no higher tier seen' wording. Doubt about whether the upgrade banner shows only for free users goes in other_concern.
  - ✗ c3_spares_payers: The offer is shown only to paying users and is pitched as more of what their plan sells, a 10× boost on top of the plan's 5× memory (l3). This fails the 'paying users see the offer and it is pitched as more of what their plan sells' condition.
  - ✓ c4_protects_subscription: The boost is limited to 10 messages in one chat and expires by midnight, so it is a sample that ends and does not take anything from other users or creators.
  - ✓ c5_moment: The offer sits in the side menu, a persistent surface the user opens by choice, and it does not interrupt a task.
  - ✓ c6_fits_simula: It is a play-to-earn unit, the reward is granted on the verified play (an unconfirmed game adds no boost), and the trigger is a detectable app event: subscriber status plus opening the side menu.
  - ✓ c7_specific: It names the Janitor Plus '5× context' benefit, the s06 side menu upgrade banner slot, and Miro's chat counts, all of which tie it to this app.
- judge_2: not fixable; other concern: The feasibility and cost of a 10× context boost are not established by the product model.
  - ✓ g_policy: The offer copy states the 10× memory boost and its 10-message duration before the user chooses to play. The decline and ad-failure fields preserve the existing plan benefit and provide a retry path.
  - ✓ g_no_cash: The reward is temporary in-app context memory, not cash or a transferable or externally exchangeable item.
  - ✓ g_no_chat_content: The trigger is a subscriber opening the side menu, not anything typed or said in a chat.
  - ✓ g_no_free_removal: The proposal says it removes nothing free, and its decline path leaves the existing 5× Plus memory unchanged.
  - ✓ g_brand_safety: The offer is a card in the side menu, which s06 rates safe, rather than an offer inside or over a conversation.
  - ✓ c1_revealed_value: The reward gives more context memory, a benefit sold by Janitor Plus in s14.e09.
  - ✗ c2_evidence: The 'what makes it work here' field claims long stories quickly outgrow even 5× memory and that a subscriber cannot get more today. Neither s14.e09 nor the cited chat counts in s20 establishes memory exhaustion or that no other option exists.
  - ✗ c3_spares_payers: The 'offered to' field targets Plus subscribers, and the offer copy pitches them more memory—the benefit their plan sells in s14.e09.
  - ✓ c4_protects_subscription: The reward ends after 10 messages or at midnight local time, so it does not grant lasting access to the paid memory benefit.
  - ✓ c5_moment: The trigger occurs when the subscriber chooses to open the side menu, a persistent surface, rather than interrupting a chat task.
  - ✓ c6_fits_simula: The flow uses a sponsored game after a detectable side-menu opening, and the ad-failure field says no boost is added if the game cannot be confirmed.
  - ✓ c7_specific: The idea uses the s06 upgrade-banner slot and the 5× context benefit shown on the Janitor Plus paywall.

## c09 · Existing opportunity: Faster replies for your next 20 replies today

- **reject**, 11/12 checks passed by every judge; dropped by code: duplicate of c01: same benefit (faster replies)
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l4 "Priority routing for faster replies", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The 92 s reply time is mostly generation time for ~3,200 characters, and priority routing may not shorten it much. The sheet appears when a user just declined to pay, which may feel like a nag. Neither point counts as a failed check.
  - ✓ g_policy: The user chooses to play the game. The offer copy states the reward (faster replies on the next 20 replies until midnight). The decline path leaves chats unchanged, and the ad-fail path is defined.
  - ✓ g_no_cash: The reward is queue priority for 20 replies or until midnight, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is the app event of closing the paywall (s14.e21). No chat content is needed for the trigger or the targeting.
  - ✓ g_no_free_removal: The idea adds a temporary boost and removes or caps nothing that free users get today.
  - ✓ g_brand_safety: The offer is a sheet over the Janitor Plus paywall (s14, rated safe), not over a chat screen. The My Chats banner is only a confirmation, and nothing is placed in the conversation.
  - ✓ c1_revealed_value: This is an existing anchor. The app charges for 'Priority routing for faster replies' (l4, s14.e10), and the reward gives a slice of that.
  - ✗ c2_evidence: The proposal states as fact that waiting is 'the cost every free user feels' and that free users feel faster writing 'in every chat'. exp2 says the measured account's plan was not recorded. It also claims the boost 'costs no extra AI work', and no mechanic in the model shows how routing works or what it costs.
  - ✓ c3_spares_payers: The offer is aimed at free users after they close the paywall. Plus members already have priority routing (l4) and never see it.
  - ✓ c4_protects_subscription: The reward expires after 20 replies or at midnight, and the daily cap is 1. It is a sample of one Plus benefit and leaves 5× context and the other benefits paid. The app already sells priority routing.
  - ✓ c5_moment: The offer appears right after the user closes the paywall, a persistent surface the user chose to open, so it does not interrupt a task in progress.
  - ✓ c6_fits_simula: This is a play-to-earn playable, and the reward is tied to the confirmed play, with a note if the game can't be confirmed. The trigger, closing the paywall, is an app event the app can detect.
  - ✓ c7_specific: The idea names the Janitor Plus paywall, its close button, the 'Priority routing for faster replies' benefit and My Chats, so it only works in this app.
- judge_2: fixable; other concern: The assertion that priority routing costs no extra AI work is not established by the product model.
  - ✓ g_policy: The offer copy states the 20-reply reward before the optional game. The decline and ad-failure fields leave chats unchanged and withhold the reward if play cannot be confirmed.
  - ✓ g_no_cash: The reward is faster in-app replies, not cash or a transferable or externally exchangeable item.
  - ✓ g_no_chat_content: The trigger event is closing the Janitor Plus paywall, not anything the user wrote or said in a chat.
  - ✓ g_no_free_removal: The proposal says it removes nothing free and adds a temporary priority-routing reward; it does not cap an existing free feature.
  - ✓ g_brand_safety: The offer is a sheet over the safe s14 paywall, not inside a conversation. The confirmation banner is on s03 My Chats, which is rated mixed rather than unsafe or unknown.
  - ✓ c1_revealed_value: For this existing opportunity, s14.e10 shows that Janitor Plus sells priority routing for faster replies, the resource the reward samples.
  - ✗ c2_evidence: The 'what makes it work here' field says free-tier reply time 'under load' was measured. Exp1 and exp2 do not record the account's plan or a load condition, so they do not support that claim about the app today.
  - ✓ c3_spares_payers: The 'offered to' and 'subscribers' fields exclude Janitor Plus members, so paying users do not see an offer for a benefit their plan includes.
  - ✓ c4_protects_subscription: The reward ends after 20 replies or at midnight, and s14.e10 shows the app already sells the routing advantage.
  - ✓ c5_moment: The trigger event follows the user's completed action of closing the paywall, rather than interrupting a chat in progress.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by a detectable paywall-close event. The ad-failure field says no faster replies are added if the game cannot be confirmed.
  - ✓ c7_specific: The idea uses the s14 Janitor Plus paywall and its specific priority-routing benefit.

## c02 · Existing opportunity: Golden checkmark next to your username for 24 hours

- **reject**, 11/12 checks passed by every judge; dropped by code: duplicate of c08: same benefit (golden checkmark)
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l6 "Golden checkmark next to your username", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The checkmark's value rests on visibility to other users, which was not observed. The 'closed paywall at least once' trigger needs the app to track that. The 24-hour expiry note and the profile display are new UI the app would have to build.
  - ✓ g_policy: The user chooses to tap the card, and the offer copy states the reward (golden checkmark, 24 hours). The decline and failed-game paths are both specified.
  - ✓ g_no_cash: The reward is a cosmetic checkmark that lasts 24 hours and has no cash value.
  - ✓ g_no_chat_content: The trigger is an app event: a free user who has closed the paywall opens the side menu. It uses no chat content.
  - ✓ g_no_free_removal: Nothing free is removed or capped. 'Removes nothing free: yes', and the offer only adds a temporary badge.
  - ✓ g_brand_safety: The card sits in the side-menu drawer (s06), which is rated safe. It is app chrome, not in a conversation and not over a chat screen.
  - ✓ c1_revealed_value: The golden checkmark is a charged Plus benefit (m2, ledger l6, s14.e12), so the app charges for the resource the reward gives.
  - ✗ c2_evidence: The rationale says 'Free users ... still see Plus members' golden checkmarks'. No element shows where other users' checkmarks appear, and the proposal itself admits that 'where the checkmark shows beyond the profile and menu was not seen'.
  - ✓ c3_spares_payers: Plus members never see the card ('subscribers' field). The side menu is not a Plus-only surface.
  - ✓ c4_protects_subscription: The reward is a cosmetic sample that expires after 24 hours, capped at 1 per week. It does not take anything from other users or creators.
  - ✓ c5_moment: The offer is on the side menu, a persistent surface the user chooses to open, next to the upgrade banner. It does not interrupt a task.
  - ✓ c6_fits_simula: It is a play-to-earn unit and the checkmark is granted only after the game is confirmed. Opening the side menu is a detectable app event.
  - ✓ c7_specific: It names the s06 'Upgrade to Janitor Plus' banner, the Plus golden checkmark benefit, and the $12.99 plan, so it would not read the same in another app.
- judge_2: fixable; other concern: The copy's 'keeps it for good' could imply the badge remains after a user cancels Janitor Plus; 'while subscribed' would be clearer.
  - ✓ g_policy: The offer copy states the golden checkmark and its 24-hour duration before play. The decline and ad-failure fields leave the menu unchanged and withhold the reward if the game is not confirmed.
  - ✓ g_no_cash: The reward field gives a cosmetic golden checkmark, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger uses paywall closure and opening the side menu, not anything typed in a chat.
  - ✓ g_no_free_removal: The proposal adds a temporary checkmark and says it removes nothing free.
  - ✓ g_brand_safety: The placement is a card in the side menu, which the product model rates safe, rather than inside a conversation.
  - ✓ c1_revealed_value: For this existing opportunity, s14.e12 lists the golden checkmark as a Janitor Plus benefit, so the app charges for the rewarded resource.
  - ✗ c2_evidence: The rationale says free users still see Plus members’ golden checkmarks, and 'what makes it work here' implies the checkmark appears in the profile and menu today. The model shows the badge on the paywall (s14.e12 and s14.e25), but does not establish where members’ badges appear in the app; these claims about current behavior lack support.
  - ✓ c3_spares_payers: The 'offered to' and 'subscribers' fields restrict the card to free users; Janitor Plus members never see it.
  - ✓ c4_protects_subscription: The reward expires after 24 hours, and the frequency cap allows at most one try per week, so it does not grant lasting access to the paid checkmark.
  - ✓ c5_moment: The card sits in the side menu, a persistent surface the user chooses to open, rather than interrupting a chat task.
  - ✓ c6_fits_simula: The flow uses a short sponsored game, the trigger is a detectable menu-opening event, and the failure field says an unconfirmed game gives no checkmark.
  - ✓ c7_specific: The idea ties the reward to the Janitor Plus golden-checkmark benefit and places the offer beneath the side-menu upgrade banner (s06.e50).

## c10 · Existing opportunity: Golden checkmark next to your username for 24 hours

- **reject**, 11/12 checks passed by every judge; dropped by code: duplicate of c08: same benefit (golden checkmark)
- Cost mark (PASS): Costs nothing extra to serve. It is a sample of the paid benefit l6 "Golden checkmark next to your username", which stays on sale. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The claim that other users see the checkmark, and that it acts as a status mark in the community, is an assumption. The model doesn't show where usernames appear to others. The rationale states it more firmly than the 'assumed' caveat in the what-makes-it-work field.
  - ✓ g_policy: The card is opt-in, the offer copy states the reward (golden checkmark for 24 hours), declining leaves the side menu unchanged, and the if_the_ad_fails field covers both no game available and a game that can't be confirmed.
  - ✓ g_no_cash: The reward is a cosmetic golden checkmark that expires after 24 hours and has no cash value.
  - ✓ g_no_chat_content: The trigger is an app event: opening the side menu after the paywall was closed without subscribing. No chat content is used.
  - ✓ g_no_free_removal: Nothing free is removed or capped. The proposal adds a card and the side menu works as before.
  - ✓ g_brand_safety: The card sits in the side menu drawer (s06), which is rated safe, is not a chat screen, and is app chrome next to the upgrade banner.
  - ✓ c1_revealed_value: The golden checkmark is a charged Janitor Plus benefit (l6, s14.e12), so the app already charges for the resource the reward gives.
  - ✓ c2_evidence: Cited claims are supported: the paywall lists the golden checkmark (s14.e12), s06 has the username and upgrade banner, and creators are notified of comments (l11). Where the badge is shown is explicitly labeled as an assumption.
  - ✓ c3_spares_payers: Plus members already have the checkmark and never see the card, and the side menu is not a place only paying users reach.
  - ✓ c4_protects_subscription: The checkmark lasts 24 hours and is capped at once per 3 days, so it is a sample that ends. Nothing is taken from other users.
  - ✓ c5_moment: The card sits on the side menu, a persistent surface the user chooses to open, and it does not interrupt a task.
  - ✓ c6_fits_simula: A short sponsored game is a play-to-earn unit, the checkmark is granted only once the play is confirmed, and the trigger is a detectable app event.
  - ✓ c7_specific: It names the Janitor Plus paywall benefit l6, the s06 upgrade banner and the username, so it is tied to this app.
- judge_2: fixable; other concern: The proposal explicitly assumes where the badge appears, but its predicted value depends heavily on other users seeing it. Its claim of zero serving cost is also unverified.
  - ✓ g_policy: The offer copy states the 24-hour checkmark reward before the user chooses to play. The decline and ad-failure fields leave the app unchanged and withhold the reward if play cannot be confirmed.
  - ✓ g_no_cash: The reward field grants a temporary cosmetic checkmark, not cash or an exchangeable item.
  - ✓ g_no_chat_content: The trigger event uses opening the side menu and previously closing the paywall, not anything written in a chat.
  - ✓ g_no_free_removal: The proposal's removes-nothing-free field and temporary reward do not remove or cap an existing free feature.
  - ✓ g_brand_safety: The placement is a card in the side-menu drawer s06, which the model rates safe, rather than an offer in a conversation.
  - ✓ c1_revealed_value: For this existing_anchor proposal, s14.e12 lists the golden checkmark as a Janitor Plus paid benefit.
  - ✗ c2_evidence: The rationale says users who comment on others’ characters are seen by the community. The comments button s07.e03 and notification text l11 do not establish that commenters are publicly identified.
  - ✓ c3_spares_payers: The offered-to and subscribers fields restrict the card to free users; Janitor Plus members never see it.
  - ✓ c4_protects_subscription: The reward ends after 24 hours, and the frequency cap permits at most one grant every three days. It does not take a feed position or another limited benefit from anyone.
  - ✓ c5_moment: The trigger and placement put the card in the side menu, a surface the user chooses to open.
  - ✓ c6_fits_simula: The flow uses a short sponsored game after a detectable menu-open event, and the ad-failure field says an unconfirmed game does not add the checkmark.
  - ✓ c7_specific: The proposal names Janitor Plus, its golden-checkmark benefit s14.e12, and the side-menu upgrade banner s06.e50.
