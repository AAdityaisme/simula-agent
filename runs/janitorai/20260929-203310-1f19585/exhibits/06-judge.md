# 06 · judge

Judges: judge_1 (claude-sonnet-5-5, high), judge_2 (gpt-6-sol, high), blind (opaque ids, no lens or model names, one candidate per call). Economics mode: `annotate`. 10 candidates: 2 accepted, 2 conditional, 0 waiting on a person, 6 rejected; 2 revised once.

| Candidate | Final | Checks | Rank | Cost mark |
|---|---|---|---|---|
| c08-rev · Product change: Faster replies for 30 minutes with a Hidden Gems character | accept | 11/11 | 1 | PASS |
| c03-rev · Existing opportunity: Double your Janitor Plus memory for one chat | accept | 11/11 | 0.5 | CONDITIONAL |
| c06 · Existing opportunity: Pick up your story with 5× memory for 10 replies | conditional | 10/11 | 0.5 | CONDITIONAL |
| c07 · Product change: Golden checkmark for 24 hours, plus a cheer for the creator | conditional | 10/11 | 0.5 | PASS |
| c08 · Product change: Faster replies for 30 minutes with a new creator's character | reject | 10/11 | 1 | PASS |
| c03 · Existing opportunity: Double your Janitor Plus memory for one chat | reject | 10/11 | 0.5 | CONDITIONAL |
| c02 · Existing opportunity: Faster replies for 1 hour | reject | 10/11 | 0.5 | PASS |
| c01 · Existing opportunity: Better memory for your next 10 replies, free today | reject | 10/11 | 0.25 | CONDITIONAL |
| c05 · Existing opportunity: Welcome back: faster replies for 2 hours | reject | 10/11 | 0 | PASS |
| c04 · Product change: Shining golden checkmark on your profile for 7 days | reject | 9/11 | 0 | PASS |

## c08-rev · Product change: Faster replies for 30 minutes with a Hidden Gems character

- **accept**, 11/11 checks passed by every judge, revision of c08
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The creator cheer adds a new social mechanic. Whether cheers ever affect creator ranking or visibility is not defined, and that could matter for the Hidden Gems feed. The proposal also does not say how the Hidden Gems check works for characters older than 30 days.
  - ✓ g_policy: The user chooses to play the card, the offer copy states the reward (30 minutes of faster replies and 1 cheer), declining leaves the sheet unchanged, and the failure path is defined (the card hides, or a note says no reward was given).
  - ✓ g_no_cash: The reward is 30 minutes of queue priority plus a cheer count on a creator profile, and neither can be withdrawn, sold or exchanged.
  - ✓ g_no_chat_content: The trigger is the chat history sheet opening for a character currently listed in Hidden Gems, which is an app event and does not need message content.
  - ✓ g_no_free_removal: The idea adds a temporary boost and removes or caps nothing free today (removes nothing free: yes).
  - ✓ g_brand_safety: The card sits inside the s18 chat history sheet, which is rated safe and opens over My Chats (s03, also safe), not inside the conversation or over a chat screen.
  - ✓ c1_revealed_value: The reward is a time-limited slice of Plus's paid 'Priority routing for faster replies' (l5), used by the player in chats, and the creator cheer is a minor add-on rather than most of the reward.
  - ✓ c2_evidence: The claims about the app as it is today are supported: s16.e10 lists priority routing as a Plus benefit, s01.e15 defines Hidden Gems, and s03 and s18 show the sheet opening from a My Chats row. The Hidden Gems check and the cheer count are labeled as new additions, and the speed of paid routing is stated as unmeasured.
  - ✓ c4_protects_subscription: The boost expires after 30 minutes, is capped at 2 per day, and Plus subscribers keep an unlimited version. Creators lose nothing, since the cheer only adds a count.
  - ✓ c5_moment: The card is inside the chat history sheet the user opened to pick or resume a chat, so it does not interrupt a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn unit, the reward is granted after the verified play, and the trigger (the sheet opening for a Hidden Gems character) is an app event.
  - ✓ c7_specific: It depends on Hidden Gems, the s18 chat history sheet with Start new chat, the s07 creator profile, and Plus's priority routing benefit, so it would not read the same in another app.
- judge_2: not fixable
  - ✓ g_policy: The offer copy states the 30-minute faster-replies reward and 1 cheer before the user chooses to play. The decline and ad-failure fields preserve the existing chat flow and withhold rewards if play cannot be confirmed.
  - ✓ g_no_cash: The reward is temporary faster replies and a creator cheer, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger uses the chat history sheet opening and the character's Hidden Gems listing, not the content of a chat.
  - ✓ g_no_free_removal: The 'removes nothing free' and decline fields leave saved chats and Start new chat unchanged.
  - ✓ g_brand_safety: The placement is a card on s18, a safe-rated chat history sheet, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: This product change gives the player a 30-minute slice of the paid priority-routing benefit listed at s16.e10; the additional cheer goes to the creator.
  - ✓ c2_evidence: The current-app claims about priority routing, Hidden Gems, the chat history sheet, and the creator profile are supported by s16.e10, s01.e15, s18, and s07. The membership check and cheer count are explicitly described as additions.
  - ✓ c4_protects_subscription: The faster-replies reward expires after 30 minutes and is capped at two offers per day; the new cheer count does not take existing visibility or benefits from others.
  - ✓ c5_moment: The placement is a card on the chat history sheet the user opens, before they choose a saved chat or Start new chat.
  - ✓ c6_fits_simula: The flow uses a short sponsored game, and the ad-failure field withholds the reward when play cannot be confirmed. Opening the sheet for a Hidden Gems character is a detectable app event.
  - ✓ c7_specific: The idea ties s18's Start new chat button and s01's Hidden Gems listing to the priority-routing benefit shown at s16.e10.

## c03-rev · Existing opportunity: Double your Janitor Plus memory for one chat

- **accept**, 11/11 checks passed by every judge, revision of c03
- Cost mark (CONDITIONAL): Costs ~$0.0055 per reward to serve; pays for itself above $5.50 eCPM at 2k context ($13.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 5 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Offering ads to paying subscribers may feel odd. Serving 10× context has cost and feasibility that the model does not confirm, and the proposal itself says it was not observed.
  - ✓ g_policy: The card is opt-in, and the offer copy states the reward (10× memory for the next 5 replies today) before the game. Declining leaves the sheet unchanged, and the ad-fails path hides the card or shows a note that no boost was added.
  - ✓ g_no_cash: The reward is temporary extra context for 5 replies in one chat, which has no cash value.
  - ✓ g_no_chat_content: The trigger is an app event: a subscriber opens the s18 chat history sheet. It does not use anything the user typed or said.
  - ✓ g_no_free_removal: The proposal removes nothing (removes nothing free: yes) and leaves the subscriber's normal 5× memory unchanged.
  - ✓ g_brand_safety: The card sits at the bottom of the s18 sheet, which is app chrome over the My Chats list (s03), not a chat screen. Both s18 and s03 are rated safe, and the card is not in the conversation.
  - ✓ c1_revealed_value: The existing_anchor reward is more context, which the app charges for: Plus sells '5× context for better memory' (s16.e09, m2) and free chats are implied to keep less (m3).
  - ✓ c2_evidence: The claims about the app today are supported: the paywall headline and 5× bullet (s16.e01, s16.e09), the $12.99 price (s16.e03), s18's message count of 15 (s18.e13), and My Chats showing 15 chats (s03.e12). The 'no add-on or ads seen' claim is worded as not seen. The boost itself is new content the proposal adds.
  - ✓ c4_protects_subscription: The boost is limited to 5 replies in one chat and expires at midnight, with a cap of 1 per day. It is offered only to subscribers and takes nothing from other users or creators.
  - ✓ c5_moment: The user has opened the chat history sheet from My Chats, a surface they chose to open, so no task is interrupted.
  - ✓ c6_fits_simula: This is a play-to-earn rewarded playable. The boost is granted only once the play is confirmed, and the trigger is an app event (opening s18, with subscriber status).
  - ✓ c7_specific: It is tied to Janitor Plus's 5× context benefit, the memory-focused paywall, and the s18 chat history sheet, so it would not read the same in another app.
- judge_2: not fixable; other concern: For a character with multiple saved sessions, the sheet-level card does not explain how the rewarded chat is chosen. The proposed 10×/8k serving approach and ad economics also need validation.
  - ✓ g_policy: The offer copy states the 10× memory reward for five replies today before play. The decline and ad-failure fields preserve normal access and withhold the boost if play cannot be confirmed.
  - ✓ g_no_cash: The reward is temporary chat context, not cash or a transferable item.
  - ✓ g_no_chat_content: The trigger event uses a subscriber opening a chat history sheet with a saved chat; it does not require message content.
  - ✓ g_no_free_removal: The proposal's decline and subscribers fields leave existing chat access and Janitor Plus memory unchanged.
  - ✓ g_brand_safety: The placement is a card on s18, a safe-rated chat history sheet, rather than inside a conversation.
  - ✓ c1_revealed_value: For this existing_anchor proposal, s16.e09 shows that the app charges for additional chat context through Janitor Plus.
  - ✓ c2_evidence: The paywall's memory benefit is supported by s16.e01 and s16.e09, while the saved sessions and counts are supported by s18.e13 and s03.e12. The 10× boost is a proposed change, not a claim that it exists today.
  - ✓ c4_protects_subscription: The reward field limits the boost to five replies in one chat and ends it by midnight; the when-the-reward-runs-out field restores the usual 5× memory.
  - ✓ c5_moment: The placement is an optional card on the chat history sheet the user chose to open, rather than an interruption during a conversation.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by opening s18, and the ad-failure field says an unconfirmed play adds no boost.
  - ✓ c7_specific: The idea ties s18's chat history sheet to the 5× context benefit shown on the Janitor Plus paywall at s16.e09.

## c06 · Existing opportunity: Pick up your story with 5× memory for 10 replies

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The 'what makes it work here' field says the app knows when a chat is 3+ days old from s18.e11. That element shows only a time of day, not a date or an observed ability to calculate days since the last chat) and another passes it (The claims about today's app are supported: the 5× context paid benefit (s16.e09), the s18 sheet with its Start new chat button, the 15-message row (s18.e13) and the 15 chats badge (s03.e12). The free-tier memory size is explicitly labelled as not seen, and the new card, reward and copy are the proposal's own additions).
- Cost mark (CONDITIONAL): Costs ~$0.0110 per reward to serve; pays for itself above $11.00 eCPM at 2k context ($26.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 10 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The sheet shows only a time such as '20:08'. Whether the app has the date data needed to detect a 3-day gap was not observed, though it is plausible. The 10-reply metering with a midnight expiry needs its own implementation.
  - ✓ g_policy: The user chooses to play from a card, and the offer copy states the reward (10 replies with 5× memory). Declining leaves the sheet unchanged, and the ad-failure path hides the card or opens the chat normally.
  - ✓ g_no_cash: The reward is 10 replies with larger context in one chat, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is the s18 sheet opening for a character whose last chat is 3+ days old, judged from timestamps. No message text, topic or sentiment is used.
  - ✓ g_no_free_removal: The proposal removes nothing free. It adds a temporary boost and leaves normal memory unchanged.
  - ✓ g_brand_safety: The card sits in the s18 chat-history sheet over s03 My Chats, and both are rated safe. It is a sheet card, not a message, and it does not sit between or pin into messages.
  - ✓ c1_revealed_value: For an existing_anchor, the resource is chat context memory, which Janitor Plus charges for ('5× context for better memory', l4/s16.e09). Free chats are implied to keep less context (m3).
  - ✓ c2_evidence: The claims about today's app are supported: the 5× context paid benefit (s16.e09), the s18 sheet with its Start new chat button, the 15-message row (s18.e13) and the 15 chats badge (s03.e12). The free-tier memory size is explicitly labelled as not seen, and the new card, reward and copy are the proposal's own additions.
  - ✓ c4_protects_subscription: The reward is a sample that ends (10 replies, once a day, until midnight, one chat), so it neither grants lasting access nor gives away the full benefit. It takes nothing from other users or creators.
  - ✓ c5_moment: The card appears in a sheet the user opened themselves to resume a story, and it does not interrupt an active task. It sits on a surface the user chose to open, just before continuing a chat.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game, and the reward is confirmed only after the verified play ('The card confirms'). The trigger is a detectable app event, the sheet opening with an old last-chat timestamp.
  - ✓ c7_specific: It is tied to Janitor Plus's '5× context' benefit, the 'More memory for long chats' headline, and the s18 chat history sheet with its Start new chat button.
- judge_2: fixable; other concern: The rationale suggests two plays may be needed in some regions, while the offer copy promises the reward for playing a short game. Any such variation should be disclosed before play.
  - ✓ g_policy: The offer copy states 5× context for the next 10 replies before the user chooses to play. The decline and ad-failure fields leave normal chat available.
  - ✓ g_no_cash: The reward is temporary chat context, not cash or something exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger uses the chat history sheet opening and last-chat age, not message content.
  - ✓ g_no_free_removal: The proposal adds a temporary context boost and does not remove or cap free chat features.
  - ✓ g_brand_safety: The placement is a card on s18, a chat history sheet rated safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: This existing-anchor reward samples the 5× context benefit sold by Janitor Plus in s16.e09.
  - ✗ c2_evidence: The 'what makes it work here' field says the app knows when a chat is 3+ days old from s18.e11. That element shows only a time of day, not a date or an observed ability to calculate days since the last chat.
  - ✓ c4_protects_subscription: The reward field limits the paid 5× context benefit to 10 replies in one chat and expires it at midnight.
  - ✓ c5_moment: The placement puts the card on the s18 history sheet, which the user chooses to open, rather than interrupting an active conversation.
  - ✓ c6_fits_simula: The flow uses a sponsored game after a detectable sheet-opening event, and the ad-failure field withholds the boost if the game cannot be confirmed.
  - ✓ c7_specific: The idea connects s18's saved-chat sheet and Start new chat button to the Janitor Plus 5× context benefit in s16.e09.

## c07 · Product change: Golden checkmark for 24 hours, plus a cheer for the creator

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (What makes it work here claims the checkmark costs nothing to serve, which s16.e12 does not establish, and says there is no visible way to show support beyond following, although s07.e07 shows a share action. These are unsupported claims about the app today) and another passes it (The claims about the app today are supported: the Plus checkmark (s16.e12), Hidden Gems (s01.e15), the Follow button (s07.e05) and the safe rating of s07. The claim of no visible way to show support is worded as an observation and matches s07's recorded elements. The cheers count, sheet and copy are the proposal's own additions).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Step 4 says the viewer's checkmark appears on the creator profile screen, but the viewer's own username isn't shown there; it would show on the viewer's own profile. Whether the app can tell that a creator profile was opened from a Hidden Gems card is not established. Users could unfollow and refollow different creators to farm daily cheers.
  - ✓ g_policy: The user chooses to play from the sheet, and the offer copy states the reward (24-hour golden checkmark plus 1 cheer). Declining leaves the follow and profile unchanged, and a failed or unverified play gives no reward without using up the day's chance.
  - ✓ g_no_cash: The reward is a cosmetic checkmark that lasts 24 hours, and the cheer is a count on a profile; neither can be withdrawn or exchanged.
  - ✓ g_no_chat_content: The trigger is a tap on the Follow button (s07.e05), which is an app event and needs no chat content.
  - ✓ g_no_free_removal: The proposal states 'removes nothing free'. It adds a temporary status item and a count, and it doesn't lock or cap anything free.
  - ✓ g_brand_safety: The offer is a sheet over s07 Creator profile, which is rated safe and is not a chat screen. It sits in no conversation and next to no unsafe or unknown content.
  - ✓ c1_revealed_value: The new resource is a slice of a paid benefit (l7 'Golden checkmark next to your username', s16.e12), and the player uses it on their own profile. Most of the reward goes to the player; the cheer is only a count for the creator.
  - ✓ c2_evidence: The claims about the app today are supported: the Plus checkmark (s16.e12), Hidden Gems (s01.e15), the Follow button (s07.e05) and the safe rating of s07. The claim of no visible way to show support is worded as an observation and matches s07's recorded elements. The cheers count, sheet and copy are the proposal's own additions.
  - ✓ c4_protects_subscription: The checkmark expires after 24 hours, and the checkmark is not Plus's main benefit, which is memory, priority routing and swipes. The cheers count explicitly never reorders Hidden Gems or any feed, so creators lose no place or visibility.
  - ✓ c5_moment: The sheet opens right after the follow is confirmed, which is a finished action, so it doesn't interrupt a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded playable, and the checkmark and cheer are granted only after a verified play. The trigger is the Follow tap, which the app can detect.
  - ✓ c7_specific: It names the Plus golden checkmark (s16.e12), the Hidden Gems tab, and the Follow button on the creator profile (s07), so it would not read the same in another app.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the 24-hour checkmark and 1 cheer before play; the decline and ad-failure fields leave the follow intact and describe what happens if play cannot be confirmed.
  - ✓ g_no_cash: The reward field gives a cosmetic checkmark, not cash or anything exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event is tapping Follow from a Hidden Gems creator profile; it does not require message content.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce a sheet and cheer count without restricting an existing free feature.
  - ✓ g_brand_safety: The placement is a bottom sheet over s07 Creator profile, which the product model rates safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: The player's primary reward is a 24-hour slice of the golden checkmark sold with Janitor Plus at s16.e12; the additional cheer goes to the creator.
  - ✗ c2_evidence: What makes it work here claims the checkmark costs nothing to serve, which s16.e12 does not establish, and says there is no visible way to show support beyond following, although s07.e07 shows a share action. These are unsupported claims about the app today.
  - ✓ c4_protects_subscription: The reward and frequency-cap fields limit the checkmark to 24 hours and one offer per day; the proposal says cheers do not alter feed order or existing creator visibility.
  - ✓ c5_moment: The placement opens the sheet after the Follow action is confirmed, rather than interrupting that action.
  - ✓ c6_fits_simula: The flow uses a short sponsored game triggered by a detectable Follow tap, and the proposal ties the cheer to verified play and withholds rewards when play cannot be confirmed.
  - ✓ c7_specific: The idea uses this app's Hidden Gems feed, creator Follow button at s07.e05, and Janitor Plus checkmark at s16.e12.

## c08 · Product change: Faster replies for 30 minutes with a new creator's character

- **reject**, 10/11 checks passed by every judge
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The cheer / 'Cheers this week' feature and the creator-side effect are new product surface that would need building. Whether Hidden Gems status is available in the sheet is unverified. The sheet cited is the '1 chat' variant.
  - ✓ g_policy: The user chooses to play from the card, and the offer copy states the reward (30 minutes of faster replies plus 1 cheer) before the ad. Declining leaves the sheet unchanged, and a failed load or unconfirmed play has its own path.
  - ✓ g_no_cash: The reward is a 30-minute queue-priority window and a creator cheer, neither of which is cash or can be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is the chat history sheet opening for a character in Hidden Gems, which is an app event and does not read anything the user typed.
  - ✓ g_no_free_removal: The idea adds an optional card and removes or caps nothing free, and 'removes nothing free: yes' matches the fields.
  - ✓ g_brand_safety: The card sits inside the s18 sheet, which is rated safe and opens over s03 My Chats (also safe). It is not in the conversation and is not next to unsafe or unknown content.
  - ✓ c1_revealed_value: The reward is a time-limited slice of the paid 'Priority routing for faster replies' benefit (l5, s16.e10). The player is the main recipient, with the cheer as a secondary effect.
  - ✗ c2_evidence: The proposal claims the app 'knows whether that character is currently in Hidden Gems' inside the chat history sheet, and that priority routing 'costs no extra reply generation, only a place higher in the line'. No element or mechanic supports either claim, since s16.e10 only says 'Priority routing for faster replies'. It also refers to a 'Cheers this week' count on the creator profile, which s07 does not show.
  - ✓ c4_protects_subscription: The faster replies last only 30 minutes, capped at 2 per day, and the app already sells priority routing. The cheer adds to the creator rather than taking anything from other users.
  - ✓ c5_moment: The card sits in the chat history sheet the user opens from a My Chats row, a persistent surface the user chooses to open, and it does not interrupt a task.
  - ✓ c6_fits_simula: This is a play-to-earn playable, the reward is granted after the verified play, and the trigger is the sheet opening for a Hidden Gems character, which is an app event.
  - ✓ c7_specific: The idea names the s18 chat history sheet, the Start new chat button, the Hidden Gems feed and the Janitor Plus priority routing benefit.
- judge_2: fixable
  - ✓ g_policy: The offer copy states 30 minutes of faster replies and one creator cheer before the optional game. The decline and ad-failure fields leave existing access intact and grant nothing if play cannot be confirmed.
  - ✓ g_no_cash: The reward field gives temporary faster replies and a creator cheer, neither of which is described as cash or exchangeable outside the app.
  - ✓ g_no_chat_content: The trigger event uses the chat history sheet opening and the character's Hidden Gems membership, not anything the user wrote in a chat.
  - ✓ g_no_free_removal: The decline field says saved chats and Start new chat continue to work as before; the proposal removes or caps no existing free feature.
  - ✓ g_brand_safety: The placement is a card on s18, a chat history sheet rated safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: This product change gives the player a time-limited version of faster replies, which s16.e10 lists as a Janitor Plus benefit; the cheer is additional.
  - ✗ c2_evidence: The 'what makes it work here' and rationale fields claim priority routing costs no extra reply generation and is only a higher place in line. s16.e10 identifies priority routing as a paid benefit but does not establish those operational or cost claims.
  - ✓ c4_protects_subscription: The reward field limits faster replies to 30 minutes, and the frequency cap limits plays to two per day; it does not grant lasting access to the paid benefit.
  - ✓ c5_moment: The trigger is opening the chat history sheet from My Chats, a surface the user chooses to open before selecting a saved chat or Start new chat.
  - ✓ c6_fits_simula: The flow uses a short sponsored game triggered by a detectable sheet-opening event, and 'what makes it work here' ties the creator cheer to verified play; no field awards the reward for a tap or started ad.
  - ✓ c7_specific: The proposal names the s18 chat history sheet, its Start new chat button, Hidden Gems, and Janitor Plus priority routing.

## c03 · Existing opportunity: Double your Janitor Plus memory for one chat

- **reject**, 10/11 checks passed by every judge
- Cost mark (CONDITIONAL): Costs ~$0.0055 per reward to serve; pays for itself above $5.50 eCPM at 2k context ($13.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 5 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The 10× context tier is a new, unobserved capability. Whether the backend can deliver it and how the app would detect subscriber status were not observed. The rationale's revenue and cost claims for lower-paying regions are unproven. The model was apparently explored on a free account, so s18 was never seen as a subscriber.
  - ✓ g_policy: The card is opt-in, the offer copy states the reward (10× memory for the next 5 replies), declining leaves the sheet as before, and the 'if the ad fails' field covers both no game and unconfirmed play.
  - ✓ g_no_cash: The reward is 5 replies with 10× context in one chat, which has no cash value and can't be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is a subscriber opening the chat history sheet (s18), which is an app event. No message content is needed.
  - ✓ g_no_free_removal: 'removes nothing free: yes' holds. The boost only adds to Plus memory, and nothing free is locked or capped.
  - ✓ g_brand_safety: The card sits on the chat history sheet (s18), a sheet over the My Chats list (s03). Both are rated safe, and the card is not inside a conversation.
  - ✓ c1_revealed_value: Context memory is something the app charges for (l4 '5× context for better memory' in the Plus paywall). m3 also implies free chats keep less context, so the resource is charged and limited.
  - ✗ c2_evidence: The rationale says users 'otherwise see no ads' and that this boost is something they 'can't buy today'. Both are stated as facts about the app today, but the model records no ad or add-on mechanic and the proposal doesn't word them as 'not seen'. The claim that the reply cost is 'already covered' by the subscription is also unsupported.
  - ✓ c4_protects_subscription: The boost is a sample that ends: 5 replies, one chat, once a day, expiring at midnight. It grants no lasting access and takes nothing from other users or creators.
  - ✓ c5_moment: The card is placed on the chat history sheet the user opens to pick a chat. It doesn't interrupt a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game, the reward is granted only after the verified play, and the trigger (opening s18) is a detectable app event.
  - ✓ c7_specific: It names the chat history sheet s18, the Start new chat button, and the Plus '5× context' benefit, so it wouldn't read the same in another app.
- judge_2: fixable; other concern: The flow should explicitly say the app grants the boost once only after Simula's REWARD_VERIFIED event.
  - ✓ g_policy: The offer copy states the 10× memory boost and its five-reply, same-day limit before the optional game. The decline and ad-failure fields leave the chat unchanged and the daily try available.
  - ✓ g_no_cash: The reward is additional context for replies in one chat, not cash or a transferable benefit.
  - ✓ g_no_chat_content: The trigger is opening the chat history sheet with a saved chat; it does not require reading messages or other chat content.
  - ✓ g_no_free_removal: The proposal says it removes nothing free, and the boost is added to a subscriber's existing benefit.
  - ✓ g_brand_safety: The placement is a card on s18, a chat history sheet rated safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: This existing-anchor reward temporarily increases context, which s16.e09 identifies as a Janitor Plus benefit.
  - ✗ c2_evidence: The 'what makes it work here' field claims subscribers resume long stories from the history sheet and that the subscription covers the reply's cost; neither claim is established by s18 or the paywall. The rationale also says users otherwise see no ads, which the product model does not support.
  - ✓ c4_protects_subscription: The reward ends after five replies or at local midnight, so it does not grant lasting access to the paid context benefit.
  - ✓ c5_moment: The card is on the chat history sheet the user chooses to open, not an interruption during a reply.
  - ✓ c6_fits_simula: The flow uses a sponsored game after the detectable sheet-opening event and shows the reward after the play; it does not specify granting it on a tap, start, click, or close.
  - ✓ c7_specific: The idea uses Janitor Plus's 5× context benefit from s16.e09 and places the offer on the s18 chat history sheet.

## c02 · Existing opportunity: Faster replies for 1 hour

- **reject**, 10/11 checks passed by every judge; dropped by code: duplicate of c08: same benefit (faster replies)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: fixable; other concern: The 'closed the paywall earlier that day' trigger relies on tracking a paywall dismissal, which the app may or may not expose. Whether free users share a reply queue is also not observed, since the model's open questions leave limits unclear. The 0.9 s measured reply suggests little visible speedup.
  - ✓ g_policy: The user opts in by choosing the card. The offer copy states the reward (faster replies for 1 hour) before the game. Declining leaves the drawer unchanged, and 'if the ad fails' covers both no game available and an unconfirmed play.
  - ✓ g_no_cash: The reward is a one-hour queue-priority boost, which has no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event: the side drawer opens on a day the paywall was closed. It uses no chat content.
  - ✓ g_no_free_removal: The proposal adds a temporary boost and says it removes nothing free. Nothing that free users get today is locked or capped.
  - ✓ g_brand_safety: The offer is a card in the side drawer, which is app chrome on s06, a screen rated safe. It is not in a conversation or over a chat screen.
  - ✓ c1_revealed_value: This is an existing_anchor and the app charges for the resource: 'Priority routing for faster replies' is a Janitor Plus paywall bullet (l5, s16.e10).
  - ✗ c2_evidence: 'What makes it work here' and the rationale say the reward 'uses no extra model work' and costs the app no extra model work. No element or mechanic in the product model supports this claim about how priority routing works, and it is not labelled as an assumption. The 'ahead of others in line' framing is also not stated by the paywall bullet.
  - ✓ c4_protects_subscription: The reward is a one-hour sample of one benefit that ends when the hour runs out, capped at once per day. It does not give away the whole plan, and the app already sells priority routing, so it takes nothing from other users that the app doesn't already sell.
  - ✓ c5_moment: The card sits in the side drawer, a persistent surface the user chooses to open, so it interrupts no task in progress.
  - ✓ c6_fits_simula: This is a play-to-earn rewarded playable. The reward is granted only once the game is confirmed, and the trigger (opening the drawer after closing the paywall) is an app-detectable event.
  - ✓ c7_specific: It names the s06 drawer, the Upgrade to Janitor Plus banner, and the 'Priority routing for faster replies' benefit, so it could not be pasted into another app.
- judge_2: fixable; other concern: The proposal's claim that the reward costs no extra model work is unverified.
  - ✓ g_policy: The offer copy states the one-hour reward before the user chooses to play. The decline and ad-failure fields leave existing access unchanged and grant nothing if the game cannot be confirmed.
  - ✓ g_no_cash: The reward is temporary priority routing for replies, not cash or a cash-like item.
  - ✓ g_no_chat_content: The trigger uses drawer-opening and paywall-closing events, not the content of a chat.
  - ✓ g_no_free_removal: The proposal says it removes nothing free, and the decline path preserves existing chats and access.
  - ✓ g_brand_safety: The placement is a card in the side drawer, which the product model rates safe, rather than an offer inside a conversation.
  - ✓ c1_revealed_value: For this existing opportunity, s16.e10 shows that Janitor Plus charges for priority routing for faster replies.
  - ✗ c2_evidence: The 'what makes it work here' field says a reply loaded in 0.9 seconds, but exp1 measured opening a chat-history sheet from s03.e05, not loading a reply. That claim about the app's observed behavior is unsupported.
  - ✓ c4_protects_subscription: The reward expires after one hour, and the frequency cap limits it to once per day. Although it grants queue priority, s16.e10 shows the app already sells that advantage.
  - ✓ c5_moment: The trigger places the card in the side drawer when the user chooses to open it, rather than interrupting an active task.
  - ✓ c6_fits_simula: The flow uses a chosen sponsored game, and the reward and ad-failure fields make the grant conditional on the game being confirmed.
  - ✓ c7_specific: The idea ties the offer to the s06.e44 upgrade banner and the priority-routing benefit shown at s16.e10.

## c01 · Existing opportunity: Better memory for your next 10 replies, free today

- **reject**, 10/11 checks passed by every judge; dropped by code: duplicate of c06: same benefit (extra chat memory)
- Cost mark (CONDITIONAL): Costs ~$0.0110 per reward to serve; pays for itself above $11.00 eCPM at 2k context ($26.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 10 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Intercepting the close tap with a sheet may feel like a dark pattern and needs a clear 'No thanks' path. The cost of 5× context replies and how the app would enforce a 10-reply boost were not observed, and step 4 lands in My Chats even though the user came from the drawer.
  - ✓ g_policy: The user opts in on the offer sheet, and the offer copy states the reward (10 replies with 5× memory until midnight). Declining ('No thanks') leaves chats as before, and the 'if the ad fails' field covers both no game available and unconfirmed play.
  - ✓ g_no_cash: The reward is temporary extra chat memory (10 replies), which cannot be withdrawn, sold, or transferred.
  - ✓ g_no_chat_content: The trigger is a tap on the paywall's Close button (s16.e21), which is an app event. Targeting is 'free users', so no chat text is read.
  - ✓ g_no_free_removal: The proposal adds a temporary boost and 'removes nothing free'. It takes nothing away from the free tier.
  - ✓ g_brand_safety: The offer is a sheet over the paywall s16, which is rated safe and is not a chat screen. It is not in the conversation, and the sheet shows no character art.
  - ✓ c1_revealed_value: The reward gives 5× context, which Janitor Plus charges for ($12.99/month, s16.e09 and s16.e03). The app therefore charges for the resource.
  - ✓ c2_evidence: The claims about the app rest on the paywall bullet (s16.e09, l4) and the headline (s16.e01). The proposal says explicitly that no free-tier memory limit was observed. The sheet, reward and copy are what the proposal adds, and the North America cost note is a prediction.
  - ✓ c4_protects_subscription: The reward is capped at 10 replies, expires at midnight, and is limited to once a day. It is a sample that ends, and it takes nothing from other users or creators.
  - ✓ c5_moment: The offer appears right after the user closes the paywall. That is a finished action at a decision point, and it does not interrupt a task in progress.
  - ✓ c6_fits_simula: The unit is a play-to-earn sponsored game, and the reward is granted only once the game is confirmed. The trigger, a tap on the paywall Close button, is an app event the app can detect.
  - ✓ c7_specific: The idea is tied to the Janitor Plus paywall s16, its 'More memory for long chats' headline, its 5× context bullet, and its $12.99 price.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the 10-reply reward before the user chooses to play. The decline and ad-failure paths leave chats unchanged and grant nothing if the game cannot be confirmed.
  - ✓ g_no_cash: The reward is temporary 5× chat context for 10 replies, not cash or an exchangeable item.
  - ✓ g_no_chat_content: The trigger is tapping s16.e21, the paywall close button; it does not depend on chat content.
  - ✓ g_no_free_removal: The proposal's reward and decline path do not remove or cap existing free chat access.
  - ✓ g_brand_safety: The placement is a sheet over the safe s16 paywall, and the proposal says no character art appears in the ad moment.
  - ✓ c1_revealed_value: As an existing opportunity, it samples 5× context, a benefit listed on the Janitor Plus paywall at s16.e09.
  - ✗ c2_evidence: The rationale says the subscription lacks a concrete demo today. The s16 paywall evidence does not establish that no demo exists elsewhere, and the claim is phrased as an app-wide fact rather than something not seen.
  - ✓ c4_protects_subscription: The reward is limited to 10 replies and expires at midnight; it does not grant lasting access to the s16.e09 paid benefit.
  - ✓ c5_moment: The sheet appears after the user taps the s16.e21 close button, rather than interrupting an ongoing chat task.
  - ✓ c6_fits_simula: The flow uses a short sponsored game triggered by a detectable paywall-close tap, and the ad-failure path says nothing is granted if the game cannot be confirmed.
  - ✓ c7_specific: The idea ties its trigger to the Janitor Plus paywall and its reward to the 5× context benefit at s16.e09.

## c05 · Existing opportunity: Welcome back: faster replies for 2 hours

- **reject**, 10/11 checks passed by every judge; dropped by code: duplicate of c08: same benefit (faster replies)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The 3+ day away detection relies on a session log the product model never shows. Also, whether free users get slower routing than Plus users was not observed, so the taste may be barely noticeable.
  - ✓ g_policy: The user chooses the welcome-back card. The offer copy states the reward (2 hours of priority routing). Declining leaves My Chats unchanged, and a failed game shows no card or an unused attempt.
  - ✓ g_no_cash: The reward is 2 hours of priority routing (queue_priority), which has no cash value and can't be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is opening My Chats on the first visit after 3+ days away, an app event based on the session log, not anything the user typed.
  - ✓ g_no_free_removal: The proposal removes nothing free ("removes nothing free: yes"). It only adds a temporary boost.
  - ✓ g_brand_safety: The card is a list card at the top of s03 My Chats, which is rated safe. It sits above the rows and is not in a conversation, so it counts as app chrome.
  - ✓ c1_revealed_value: Priority routing for faster replies is a Janitor Plus benefit the app charges for (s16.e10, l5, $12.99/month), so the reward gives a resource the app charges for.
  - ✓ c2_evidence: The claims about the app rest on s16.e10, s16.e03, s06.e44 and s03.e12. The proposal says free reply speed was not measured. The new card, copy and predictions are the proposal's own additions.
  - ✓ c4_protects_subscription: The reward expires after 2 hours and is capped at 1 per week, so it is a sample and not lasting access. Nothing is taken from other users or creators.
  - ✓ c5_moment: The card is shown when the user opens My Chats, a persistent surface they choose to visit. It does not interrupt a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn unit, and the reward is granted only after the game is confirmed. The trigger is an app-detectable event, the first My Chats open after 3+ days away.
  - ✓ c7_specific: It names My Chats (s03), the '15 chats' row, the Janitor Plus priority routing benefit, and the drawer banner, so it is tied to this app.
- judge_2: fixable
  - ✓ g_policy: The offer copy states the two-hour reward before the user chooses to play; the decline and ad-failure fields preserve normal access and grant nothing if play cannot be confirmed.
  - ✓ g_no_cash: The reward is two hours of in-app priority routing, not cash or a transferable or externally exchangeable item.
  - ✓ g_no_chat_content: The trigger is opening My Chats after a period away; it does not require reading messages or other chat content.
  - ✓ g_no_free_removal: The proposal says it removes nothing free, and the reward adds temporary priority routing rather than restricting an existing free feature.
  - ✓ g_brand_safety: The placement is a card above the rows on s03 My Chats, a safe list screen, rather than inside a conversation.
  - ✓ c1_revealed_value: For this existing_anchor, s16.e10 shows that Janitor Plus charges for the priority routing the reward temporarily provides.
  - ✗ c2_evidence: The 'what makes it work here' field asserts an existing session log that the model does not observe. It also treats exp1's 0.9-second chat-history-sheet load as a free-tier reply-speed measurement, which exp1 is not.
  - ✓ c4_protects_subscription: The reward field limits the paid priority-routing benefit to two hours, so it does not grant lasting access.
  - ✓ c5_moment: The welcome-back card appears on s03 My Chats, a screen the user chooses to open, rather than interrupting an ongoing task.
  - ✓ c6_fits_simula: The flow has the user play a sponsored game, and the ad-failure field says an unconfirmed game grants no reward; opening My Chats is a detectable app event.
  - ✓ c7_specific: The idea uses s03 My Chats and the priority-routing benefit listed at s16.e10.

## c04 · Product change: Shining golden checkmark on your profile for 7 days

- **reject**, 9/11 checks passed by every judge; dropped by code: gives payers more of "Golden checkmark next to your username", but no amount or cap for it was observed (linked by the benefit-naming call)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The shine's visibility to other users was not observed, so its value is speculative. The offer only reaches existing subscribers, who may already be the least ad-tolerant group.
  - ✓ g_policy: The user opts in by tapping the banner, and the offer copy states the reward (checkmark shine, 7 days). Declining leaves the profile unchanged, and the 'if the ad fails' field covers load and verify failures.
  - ✓ g_no_cash: The reward is a 7-day cosmetic shine on the checkmark, with no cash value and nothing that can be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event: a subscriber opens My profile with no shine active. No chat content is needed.
  - ✓ g_no_free_removal: The proposal adds an optional cosmetic for subscribers and removes, locks or caps nothing that users get free today.
  - ✓ g_brand_safety: The placement is a banner on s05 My profile, which is rated safe and is not a chat screen. It is app chrome, not inside a conversation.
  - ✗ c1_revealed_value: This is a product_change. The new shine is a cosmetic, and 'piece of a paid benefit' is 'none', so it is not a slice of something the app charges for. It is also not used by the player in an observed core flow (f1-f6), so it fails the new-resource condition.
  - ✗ c2_evidence: The rationale says the offer 'earns revenue from users who see no ads today', which states a fact about the app today. The model records no ad mechanic, and the claim is not worded as 'not seen'. The other claims (username on s05.e02 and s06.e23, checkmark in s16.e12) are supported.
  - ✓ c4_protects_subscription: The reward is a temporary 7-day cosmetic style and does not give away the subscription's main benefit (memory, priority, swipes). It takes nothing from other users.
  - ✓ c5_moment: The offer sits on My profile, a persistent surface the user chooses to open, and it does not interrupt a task in progress.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded game. The trigger is a detectable app event (subscriber opens My profile with no active shine). The shine is granted only after the play is confirmed, as the failure path indicates.
  - ✓ c7_specific: It names the Janitor Plus golden checkmark (s16.e12), the My profile screen (s05) and the username, which are specific to this app.
- judge_2: fixable
  - ✓ g_policy: The offer copy states that playing earns a 7-day checkmark shine. The profile banner is optional, declining changes nothing, and the failure path withholds the shine if play cannot be confirmed.
  - ✓ g_no_cash: The reward is a cosmetic checkmark shine, not cash or an exchangeable item.
  - ✓ g_no_chat_content: The trigger uses a My profile opening, subscription status, and shine status; it does not need chat content.
  - ✓ g_no_free_removal: The proposal adds a subscriber-only style and says it removes nothing from the free tier.
  - ✓ g_brand_safety: The placement is a banner on s05 My profile, a screen rated safe, rather than in a conversation.
  - ✓ c1_revealed_value: As a product change, the shine is for the player's own use on My profile, which is visited in core flow f1; it styles the paid checkmark listed at s16.e12.
  - ✗ c2_evidence: The rationale says these users 'see no ads today.' The product model does not support that claim about the app's current behavior.
  - ✓ c4_protects_subscription: The reward styles the subscriber's existing checkmark for only seven days; it does not grant lasting access to a paid benefit.
  - ✓ c5_moment: The banner appears on My profile when the subscriber opens that persistent surface, rather than interrupting a task.
  - ✓ c6_fits_simula: The flow uses a sponsored game triggered by a detectable profile-opening event, and the failure path says an unconfirmed play does not add the shine.
  - ✓ c7_specific: The idea relies on Janitor Plus's golden checkmark at s16.e12 and its placement beside the username on My profile.
