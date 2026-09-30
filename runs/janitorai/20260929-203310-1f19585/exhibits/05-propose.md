# 05 · propose

4 lenses, 4 live candidates (0 flagged for an unobserved term), 4 dropped, 0 with near-miss ids repaired by code (`resolve:<id>` lines in trace.jsonl).

- Mock coverage: 8 of 8 ideas start on a screen the mock draws (counted before the mock-scope filter); the filter dropped 0.
- Top-up call: not needed (4 distinct after dedupe).

| Lens | Kind | Focus |
|---|---|---|
| Free user at a limit | fixed | A free user who has just hit a limit, a paywall, or a locked item, or would if the app had one. What do they want right now that a rewarded play could give them, partially and for a while? |
| Subscriber | fixed | A paying user. What do they still lack or want more of that an opt-in rewarded play could add without devaluing what they pay for? If the app has no subscription, take its most engaged user. |
| Drifting user | fixed | A user whose visits are thinning out. What would bring them back or protect progress they would lose, through a rewarded play at a natural moment rather than an interruption? |
| Someone else in the app: Hidden Gems show characters from smaller creators with engaging conversations, created in the last 30 days. | ledger | Someone in this app besides the viewer, seen as "Hidden Gems show characters from smaller creators with engaging conversations, created in the last 30 days.". Look for ideas where the viewer's rewarded play also helps or involves them, and the viewer gets something they would want for their own use of the app; helping the other person comes on top of that, not instead. |

## Candidates, by reach

### c08 · Product change: Faster replies for 30 minutes with a new creator's character
- product_change, lens `ledger_l10`, trigger on `s18`: The chat history sheet opens for a character that currently appears in Hidden Gems
- Offer: Play a short game to get faster replies from this character for 30 minutes, and send 1 cheer to its creator.
- Reward: 1 faster replies (queue_priority, 30 minutes)
- When the reward ends: When the 30 minutes end, replies return to normal speed with a note that Janitor Plus gives faster replies in every chat all the time, so the viewer can compare and decide.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1 (trigger depth x the proposer's per-user daily cap, 2; not a measured audience). The offer's cap: 2 per day, resets at midnight local time; one cheer per creator per day

### c03 · Existing opportunity: Double your Janitor Plus memory for one chat
- existing_anchor, lens `subscriber`, trigger on `s18`: A Janitor Plus subscriber opens a character's chat history sheet that has at least one saved chat.
- Offer: Play a short sponsored game to give this chat 10× memory, double your Janitor Plus 5×, for your next 5 replies today.
- Reward: 5 replies with 10× context (inference, next 5 replies in the chosen chat, until midnight local time)
- When the reward ends: After the 5 replies the chat returns to the usual 5× memory with a note that the boost can be taken again tomorrow, bringing the subscriber back for their next long session.
- Cost: Costs ~$0.0055 per reward to serve; pays for itself above $5.50 eCPM at 2k context ($13.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 5 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.5 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time; one chat per boost

### c06 · Existing opportunity: Pick up your story with 5× memory for 10 replies
- existing_anchor, lens `drifting`, trigger on `s18`: The chat history sheet opens for a character whose most recent chat is 3 or more days old
- Offer: Pick up where you left off: play a short game and this chat remembers 5× more of your story for your next 10 replies (today only).
- Reward: 10 replies with 5× context (inference, next 10 replies in this chat, until midnight)
- When the reward ends: After the 10th reply, a note says memory is back to normal and that Janitor Plus keeps 5× memory in every chat, so the user has felt exactly what the plan's headline promises.
- Cost: Costs ~$0.0110 per reward to serve; pays for itself above $11.00 eCPM at 2k context ($26.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 10 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.5 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per user per day, for one chat that has been untouched for 3 or more days; resets at midnight local time

### c07 · Product change: Golden checkmark for 24 hours, plus a cheer for the creator
- product_change, lens `ledger_l10`, trigger on `s07`: User taps Follow on a creator's profile that was opened from a Hidden Gems character card
- Offer: Cheer for this creator: play a short game to wear the golden checkmark next to your username for 24 hours, and add 1 cheer to their profile.
- Reward: 1 golden checkmark (cosmetic, 24 hours)
- When the reward ends: When the 24 hours end, the checkmark disappears with a note that Janitor Plus keeps it every day, and the viewer can earn it again tomorrow by cheering another creator.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.5 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time; one cheer per creator per day

## Dropped by code

- c02 · Existing opportunity: Faster replies for 1 hour: duplicate of c08: same benefit (faster replies)
- c01 · Existing opportunity: Better memory for your next 10 replies, free today: duplicate of c06: same benefit (extra chat memory)
- c05 · Existing opportunity: Welcome back: faster replies for 2 hours: duplicate of c08: same benefit (faster replies)
- c04 · Product change: Shining golden checkmark on your profile for 7 days: gives payers more of "Golden checkmark next to your username", but no amount or cap for it was observed (linked by the benefit-naming call)
