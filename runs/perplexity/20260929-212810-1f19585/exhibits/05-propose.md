# 05 · propose

3 lenses, 4 live candidates (0 flagged for an unobserved term), 0 dropped, 0 with near-miss ids repaired by code (`resolve:<id>` lines in trace.jsonl).

- Mock coverage: 4 of 4 ideas start on a screen the mock draws (counted before the mock-scope filter); the filter dropped 0.
- Top-up call: fired (3 distinct after dedupe, under 4; 4 after the top-up).

| Lens | Kind | Focus |
|---|---|---|
| Free user at a limit | fixed | A free user who has just hit a limit, a paywall, or a locked item, or would if the app had one. What do they want right now that a rewarded play could give them, partially and for a while? |
| Subscriber | fixed | A paying user. What do they still lack or want more of that an opt-in rewarded play could add without devaluing what they pay for? If the app has no subscription, take its most engaged user. |
| Drifting user | fixed | A user whose visits are thinning out. What would bring them back or protect progress they would lose, through a rewarded play at a natural moment rather than an interruption? |

## Candidates, by reach

### c01 · Existing opportunity: 1 extra Computer task today
- existing_anchor, lens `free_at_limit`, trigger on `s10`: A Computer task the user started finishes and they return to the Computer home, at most once per day.
- Offer: Play a short sponsored game to get 1 extra Computer task today. It stays available until midnight.
- Reward: 1 tasks (inference, until midnight local time)
- When the reward ends: At midnight the unused extra task expires, and the next finished task offers a new one, so users come back to hand Computer more work each day.
- Cost: Costs ~$0.0110 per reward to serve; pays for itself above $11.00 eCPM at 2k context ($26.00 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 10 replies of 300 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.25 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time

### c04 · Product change: Your next 3 Computer tasks start first today
- product_change, lens `topup`, trigger on `s10`: The user returns to Computer home after sending at least one Computer task in the current session.
- Offer: Play a short game and your next 3 Computer tasks start first when Computer is busy, until midnight tonight.
- Reward: 3 tasks (queue_priority, until midnight local time)
- When the reward ends: After 3 tasks, or at midnight, tasks go back to their normal start time and the card can return tomorrow, which brings users back to Computer daily.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.25 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time

### c02 · Product change: A new home screen look for 7 days
- product_change, lens `subscriber`, trigger on `s03`: The user opens the Library in their fifth or later session in the app.
- Offer: Play a short game to use the Midnight theme on your home screens for 7 days.
- Reward: 1 theme (cosmetic, 7 days)
- When the reward ends: When the 7 days end, the home screens switch back to the usual look, and the Library card returns so the user can play again for another week.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0 (trigger depth x the proposer's per-user daily cap, 0, less than once a day; not a measured audience). The offer's cap: 1 per user per week, resets 7 days after it was taken; the card stays hidden while a theme is active.

### c03 · Product change: Computer runs your task every morning for 3 days
- product_change, lens `drifting`, trigger on `s10`: First app open after 7 or more days with no session, while the Computer setup counter shows fewer than 3 steps done
- Offer: Welcome back. Play a short game and Computer will run one task for you every morning for the next 3 days, with each result waiting in your Library.
- Reward: 3 tasks (inference, one run each morning for 3 days)
- When the reward ends: After the third morning, the Library shows the last result with a note that Computer can keep running it on any schedule, which gives the user a reason to come back and set up recurring tasks themselves.
- Cost: Costs ~$0.0063 per reward to serve; pays for itself above $6.30 eCPM at 2k context ($10.80 at 8k). A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 3 replies of 800 tokens out, at $0.25 / $2.00 per million tokens in / out (central prices); the verdict uses the 8k figure; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0 (trigger depth x the proposer's per-user daily cap, 0, less than once a day; not a measured audience). The offer's cap: 1 per week per user, resets 7 days after it was taken; shown only after 7 or more days away
