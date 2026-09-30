# 06 · judge

Judges: judge_1 (claude-sonnet-5-5, high), judge_2 (gpt-6-sol, high), blind (opaque ids, no lens or model names, one candidate per call). Economics mode: `annotate`. 4 candidates: 0 accepted, 3 conditional, 0 waiting on a person, 1 rejected; 0 revised once.

| Candidate | Final | Checks | Rank | Cost mark |
|---|---|---|---|---|
| c01 · Existing opportunity: 1 extra Computer task today | conditional | 10/11 | 0.25 | CONDITIONAL |
| c02 · Product change: A new home screen look for 7 days | conditional | 10/11 | 0 | PASS |
| c03 · Product change: Computer runs your task every morning for 3 days | conditional | 10/11 | 0 | CONDITIONAL |
| c04 · Product change: Your next 3 Computer tasks start first today | reject | 9/11 | 0.25 | PASS |

## c01 · Existing opportunity: 1 extra Computer task today

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The trigger and flow treat a finished Computer task returning the user to s10 as existing behavior, but the model observes no task being completed or what happens afterward. The rationale also asserts that an agent task uses several model calls without product-model support) and another passes it (The claims about the limited preview and the benefit line are backed by s04.e04, s07.e02 and s04.e06. The proposal states that the size of the allowance and any paid plan were not seen, so it does not rest on an open question).
- Cost mark (CONDITIONAL): Costs ~$0.0110 per reward to serve; pays for itself above $11.00 eCPM at 2k context ($26.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 10 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The idea assumes the preview cap is counted in tasks, but the cap could be in time or credits and none was observed. Task-completion behavior was also never seen because no task was run. The claim that the app has free versus paid users is unverified, since a paid plan was never seen.
  - ✓ g_policy: The offer is a sheet the user opts into, and the offer copy states the reward (1 extra Computer task today). Declining leaves the home unchanged, and the 'if the ad fails' field gives a path for no game or an unconfirmed play.
  - ✓ g_no_cash: The reward is one extra Computer task that expires at midnight, with no cash value.
  - ✓ g_no_chat_content: The trigger is an app event (a Computer task finishing on s10), not anything the user typed or said.
  - ✓ g_no_free_removal: The proposal adds an optional bonus and removes or caps nothing users get free today ('removes nothing free: yes').
  - ✓ g_brand_safety: The offer is a bottom sheet over s10, a Computer home rated safe, opened by a finished-task event. It is not placed in the conversation.
  - ✓ c1_revealed_value: The app labels Computer a 'LIMITED PREVIEW' (l1, l2, m2 inferred from s04.e04 and s07.e02), so the app limits the resource the reward gives.
  - ✓ c2_evidence: The claims about the limited preview and the benefit line are backed by s04.e04, s07.e02 and s04.e06. The proposal states that the size of the allowance and any paid plan were not seen, so it does not rest on an open question.
  - ✓ c4_protects_subscription: The reward is one extra task that expires at midnight, capped at 1 per day. It is a sample that ends and takes nothing from other users.
  - ✓ c5_moment: The offer appears when a task has just finished, which is a finished-action moment and does not interrupt work in progress.
  - ✓ c6_fits_simula: It is a play-to-earn rewarded playable, the reward is granted only after the play is confirmed, and the trigger (a task finishing) is an app event.
  - ✓ c7_specific: It names the Computer limited-preview sheet (s04), the intro (s07), the Computer home (s10) and the 'Do anything' composer.
- judge_2: fixable; other concern: The model does not establish whether a bonus task would work with the preview’s unknown limiting mechanism. The 'offered to' and 'subscribers' fields also give different audience descriptions.
  - ✓ g_policy: The offer copy states the one-task reward before play. The flow makes play optional, and the decline and ad-failure fields leave the preview unchanged or allow a retry.
  - ✓ g_no_cash: The reward field grants a Computer task for use in the app, not cash or a transferable equivalent.
  - ✓ g_no_chat_content: The trigger event is a task finishing and a return to Computer home; it does not require what the user typed.
  - ✓ g_no_free_removal: The proposal adds a bonus task and says the existing preview remains unchanged if the user declines.
  - ✓ g_brand_safety: The placement is a sheet over s10 Computer home, which the product model rates content safe, rather than an offer in a conversation.
  - ✓ c1_revealed_value: For this existing_anchor, the reward gives Computer use, which m1 and inferred m2 support as a limited preview.
  - ✗ c2_evidence: The trigger and flow treat a finished Computer task returning the user to s10 as existing behavior, but the model observes no task being completed or what happens afterward. The rationale also asserts that an agent task uses several model calls without product-model support.
  - ✓ c4_protects_subscription: The reward and frequency-cap fields limit the bonus to one task per day, with unused access expiring at midnight.
  - ✓ c5_moment: The proposed trigger is after a task finishes, a finished-action moment rather than an interruption mid-task.
  - ✓ c6_fits_simula: The flow grants the task once play is confirmed, not on a tap or ad start, and uses a task-finished app event as its trigger.
  - ✓ c7_specific: The idea names the Computer limited preview on s04 and s07 and places the offer on s10 Computer home.

## c02 · Product change: A new home screen look for 7 days

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The what-makes-it-work-here and flow fields say users reopen Projects, Artifacts, and past Sessions or pick up a past session. s03 shows those listings, but the product model does not establish reopening behavior) and another passes it (Claims about today's app are supported or worded as 'not seen': no paid plan or limit seen, no theme options seen, and the Library lists Projects, Artifacts and Sessions (s03). The rest is new design or prediction. The claim that the card sits between 'Start something new' and the Sessions list is not clearly supported and is noted separately).
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The card's position is unverified. The s03 element order lists the Sessions header before the 'Start something new' card, so 'below Start something new, above Sessions' may not match the layout. The cited evidence list is empty. A purely cosmetic reward has weak demand and may see low uptake.
  - ✓ g_policy: The user opts in by playing. The offer copy says what is given and for how long ('Midnight theme on your home screens for 7 days'). Declining leaves the app unchanged, and the ad-failure path hides the card or shows a retry note.
  - ✓ g_no_cash: The reward is a cosmetic theme for 7 days, with no monetary value and nothing that can be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event: opening the Library in the fifth or later session. It uses no chat content.
  - ✓ g_no_free_removal: The idea adds an optional theme and removes or caps nothing free ('removes nothing free: yes'). The home screens keep their usual look by default.
  - ✓ g_brand_safety: The offer is a list card in the Library (s03), which is rated safe and is app chrome. It is not in a conversation or over a chat screen.
  - ✓ c1_revealed_value: The theme is a new resource for the player's own use on the Search home (s01) and the Computer home (s10), which are part of the observed core flows f1 and f5. None of it goes to another user or a creator.
  - ✓ c2_evidence: Claims about today's app are supported or worded as 'not seen': no paid plan or limit seen, no theme options seen, and the Library lists Projects, Artifacts and Sessions (s03). The rest is new design or prediction. The claim that the card sits between 'Start something new' and the Sessions list is not clearly supported and is noted separately.
  - ✓ c4_protects_subscription: A cosmetic theme lasting 7 days gives away no paid benefit and takes nothing from other users. It also expires.
  - ✓ c5_moment: The card sits on the Library, a persistent surface the user chooses to open. It does not interrupt a task in progress.
  - ✓ c6_fits_simula: This is a play-to-earn rewarded game. The theme is granted only once the play is confirmed, and the trigger (Library opened in the fifth or later session) is an app event the app can detect.
  - ✓ c7_specific: It names the Library screen, the 'Start something new' card, the Sessions list, and the Search home and Computer home screens.
- judge_2: fixable
  - ✓ g_policy: The Library card’s offer copy states the Midnight theme and its 7-day duration before an optional game. The decline and ad-failure fields preserve existing access and provide a retry path.
  - ✓ g_no_cash: The reward field gives one cosmetic theme for use in the app, not cash or a cash-like item.
  - ✓ g_no_chat_content: The trigger event uses an app session count and a Library opening, not anything the user wrote or said.
  - ✓ g_no_free_removal: The adds and removes-nothing-free fields introduce an optional theme without restricting an existing free feature.
  - ✓ g_brand_safety: The placement is a card on s03 Library, which the product model rates content safe, outside any conversation.
  - ✓ c1_revealed_value: As a product change, the theme is for the player’s own use on the Search and Computer home screens, which appear in the observed core flows.
  - ✗ c2_evidence: The what-makes-it-work-here and flow fields say users reopen Projects, Artifacts, and past Sessions or pick up a past session. s03 shows those listings, but the product model does not establish reopening behavior.
  - ✓ c4_protects_subscription: The reward expires after seven days and does not give away the Computer preview’s benefit or take an advantage from other users.
  - ✓ c5_moment: The placement is a card in the Library, a surface the user chooses to open, rather than an interruption to an active task.
  - ✓ c6_fits_simula: The flow grants the theme once the sponsored game play is confirmed, and the trigger is a detectable Library-opening event.
  - ✓ c7_specific: The proposal locates the card below s03’s Start something new card and applies the theme to the app’s Search and Computer home screens.

## c03 · Product change: Computer runs your task every morning for 3 days

- **conditional**, 10/11 checks passed by every judge; split: c2_evidence
- Condition: The judges split: on c2_evidence, one fails it (The 'what makes it work here' field says past results land in Library Sessions, but s03.e07 shows only a Sessions section; it does not establish where Computer task results go today) and another passes it (The claims about today's app are supported: the 0 of 3 counter (s10.e03), the preview sheet's 'Automate recurring tasks on any schedule' (s04.e08), and the Library Sessions list (s03.e07, with the Do pill at s03.e14). The unseen cap and price are stated as not seen).
- Cost mark (CONDITIONAL): Costs ~$0.0063 per reward to serve; pays for itself above $6.30 eCPM at 2k context ($10.80 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 3 replies of 800 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: Step 5 and the claim that results land in Library Sessions depend on scheduled Computer runs producing sessions, which was not observed (open question about what happens when a first task starts). The cost of about 8,000 tokens per run is also unverified. The task the user picks may draw on chat content, but it isn't used for targeting.
  - ✓ g_policy: The user chooses to play via a card, the offer copy states the reward (one task each morning for 3 days), declining leaves the home unchanged, and the ad-failure path is given.
  - ✓ g_no_cash: The reward is 3 scheduled Computer task runs, which have no cash value and cannot be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event (first open after 7+ days and the setup counter below 3), not anything the user typed. The later task choice is the user's own input and is not used for targeting.
  - ✓ g_no_free_removal: The proposal says it removes nothing, and the runs are added on top of the preview without reducing anything free today.
  - ✓ g_brand_safety: The offer is a card under the checklist on s10 Computer home, which is rated safe and is app chrome, not inside a conversation.
  - ✓ c1_revealed_value: As a product change, the new resource is scheduled runs. These are a slice of l5 'Automate recurring tasks on any schedule' and are used by the player in the Computer flow (f4/f5), with results going to the player's own Library.
  - ✓ c2_evidence: The claims about today's app are supported: the 0 of 3 counter (s10.e03), the preview sheet's 'Automate recurring tasks on any schedule' (s04.e08), and the Library Sessions list (s03.e07, with the Do pill at s03.e14). The unseen cap and price are stated as not seen.
  - ✓ c4_protects_subscription: The reward is a sample that ends: 3 runs over 3 mornings and then nothing more. It gives no lasting or unlimited access to automation and takes nothing from other users.
  - ✓ c5_moment: The card sits on the persistent Computer home at app open after a week away, so it doesn't interrupt a task the user is in the middle of.
  - ✓ c6_fits_simula: It is a play-to-earn unit, and the runs are set up only after the play is confirmed (step 4 follows the confirmed play). The trigger is a detectable app event (inactivity of 7+ days plus the setup counter).
  - ✓ c7_specific: It names the Computer home, the Set up Computer checklist counter, the preview's recurring-task benefit and the Library Sessions, so it only makes sense in this app.
- judge_2: fixable; other concern: The proposal's assumptions about week-away return behavior and serving economics need validation.
  - ✓ g_policy: The offer copy states one task each morning for three days before the user chooses to play. The decline and ad-failure fields preserve the existing experience and provide a failed-verification path.
  - ✓ g_no_cash: The reward is three Computer task runs for the player's use, not cash or a transferable equivalent.
  - ✓ g_no_chat_content: The trigger uses app-open timing, session inactivity, and the setup counter, not message or task content.
  - ✓ g_no_free_removal: The adds and decline fields say the runs are extra and existing preview access remains unchanged.
  - ✓ g_brand_safety: The placement is a card on the safe s10 Computer home, beneath its checklist, not inside a conversation.
  - ✓ c1_revealed_value: As a product change, it gives the player task runs for their own use in the Computer flow shown by f4 and the first-task checklist item s09.e32.
  - ✗ c2_evidence: The 'what makes it work here' field says past results land in Library Sessions, but s03.e07 shows only a Sessions section; it does not establish where Computer task results go today.
  - ✓ c4_protects_subscription: The reward samples the recurring-task benefit in s04.e08 for only three morning runs; it does not grant lasting access.
  - ✓ c5_moment: The placement is a card on the Computer home, a persistent surface the user can choose to engage with rather than an interruption mid-task.
  - ✓ c6_fits_simula: The flow uses a sponsored game and sets up runs only after the play is confirmed; the trigger is a detectable app-open event.
  - ✓ c7_specific: The idea uses the s10 Computer setup counter, the Computer task flow, and the recurring-task benefit named in s04.e08.

## c04 · Product change: Your next 3 Computer tasks start first today

- **reject**, 9/11 checks passed by every judge; rerun explore (human-gated)
- Cost mark (PASS): Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- judge_1: not fixable; other concern: The idea depends on a queue or busy state that was never observed. Sending the tasks 'first' may have no visible effect, so users could be rewarded with nothing. The 'costs no extra model work' claim is also unverified.
  - ✓ g_policy: The card is opt-in, the offer copy states the reward (next 3 tasks start first until midnight), declining leaves tasks unchanged, and 'if the ad fails' covers both no game available and a game that can't be verified.
  - ✓ g_no_cash: The reward is temporary queue priority for 3 tasks, with no cash value and nothing that can be withdrawn or transferred.
  - ✓ g_no_chat_content: The trigger is an app event (returning to Computer home after a task is sent), not anything the user typed.
  - ✓ g_no_free_removal: Nothing free is removed or capped. 'if the user declines' says tasks run as before, and queue priority is a permitted resource anyway.
  - ✓ g_brand_safety: The card is a list card on s10 Computer home, which is rated safe. It is not inside a conversation and sits next to the safe checklist.
  - ✓ c1_revealed_value: As a product change, the new resource (start-first priority) is used by the player for their own Computer tasks, which is the observed core flow f4 (Computer composer). It is not given to a third party.
  - ✗ c2_evidence: The proposal cites no evidence. It states that Computer is 'a preview agent shared by many users' and treats a busy queue as existing, but no mechanic or element shows a queue, busy periods, or a shared load. The 'no busy wait or queue was seen' note admits this.
  - ✗ c4_protects_subscription: A start-first lane puts this user's tasks ahead of standard tasks, taking other users' place in the queue the app orders. The product model shows no plan or purchase that already sells that advantage.
  - ✓ c5_moment: The card sits on the persistent Computer home surface (s10) and appears after a task is sent, so it does not interrupt a task in progress.
  - ✓ c6_fits_simula: The unit is a play-to-earn sponsored game, the reward is granted after the verified play, and the trigger (returning to Computer home after sending a task) is an app event the app can detect.
  - ✓ c7_specific: The idea names Computer home (s10), the Do anything composer, the Set up Computer checklist, and the limited preview, so it is tied to this app.
- judge_2: not fixable; other concern: The proposal acknowledges that busy periods were not observed; its claims about no extra model work and pure ad income also need validation.
  - ✓ g_policy: The offer copy states the three-task reward before play, and the card is opt-in. The decline and ad-failure fields preserve normal task behavior and provide a failure path.
  - ✓ g_no_cash: The reward is temporary priority for Computer tasks, not cash or a transferable or externally exchangeable item.
  - ✓ g_no_chat_content: The trigger uses a sent-task event and a return to Computer home; it does not require the task's contents.
  - ✓ g_no_free_removal: The proposal says it removes nothing free, and the reward does not lock or cap an existing free feature.
  - ✓ g_brand_safety: The placement is a card under the setup checklist on s10 Computer home, a screen rated safe; it is not inside a conversation.
  - ✓ c1_revealed_value: This product change gives the player priority for their own Computer tasks, which they use in the Computer flow represented by f4 and s10.
  - ✗ c2_evidence: The 'what makes it work here' field describes the preview agent as shared by many users. The model supports a limited Computer preview, but does not establish shared task processing.
  - ✗ c4_protects_subscription: The 'adds' field puts rewarded tasks ahead of standard tasks when Computer is busy. That priority takes a share of limited start capacity from other users' tasks, and the model does not show that the app already sells this advantage.
  - ✓ c5_moment: The trigger follows a sent task, and the offer sits on Computer home rather than interrupting task entry.
  - ✓ c6_fits_simula: The flow uses a sponsored game after a detectable app event. The ad-failure field withholds the reward when the game cannot be confirmed; no field grants it for a tap or an unverified start.
  - ✓ c7_specific: The idea names Computer home, its setup checklist, the Do anything composer, and the limited Computer preview.
