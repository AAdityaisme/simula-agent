# 05 · propose

5 lenses, 5 live candidates (0 flagged for an unobserved term), 4 dropped, 0 with near-miss ids repaired by code (`resolve:<id>` lines in trace.jsonl).

- Mock coverage: 9 of 9 ideas start on a screen the mock draws (counted before the mock-scope filter); the filter dropped 0.
- Top-up call: not needed (5 distinct after dedupe).

| Lens | Kind | Focus |
|---|---|---|
| Free user at a limit | fixed | A free user who has just hit a limit, a paywall, or a locked item, or would if the app had one. What do they want right now that a rewarded play could give them, partially and for a while? |
| Subscriber | fixed | A paying user. What do they still lack or want more of that an opt-in rewarded play could add without devaluing what they pay for? If the app has no subscription, take its most engaged user. |
| Drifting user | fixed | A user whose visits are thinning out. What would bring them back or protect progress they would lose, through a rewarded play at a natural moment rather than an interruption? |
| Someone else in the app: TEMU in Taboola advertising section | ledger | Someone in this app besides the viewer, seen as "TEMU in Taboola advertising section". Look for ideas where the viewer's rewarded play also helps or involves them, and the viewer gets something they would want for their own use of the app; helping the other person comes on top of that, not instead. |
| Someone else in the app: New Temu users get jackets for $2.36 | ledger | Someone in this app besides the viewer, seen as "New Temu users get jackets for $2.36". Look for ideas where the viewer's rewarded play also helps or involves them, and the viewer gets something they would want for their own use of the app; helping the other person comes on top of that, not instead. |

## Candidates, by reach

### c07 · Product change: 30 minutes of reading with no ads, sponsored by one brand
- product_change, lens `ledger_l1`, trigger on `s01`: The user comes back to the home feed after opening their third article of the day.
- Offer: Reading a lot today? Play a short sponsored game and read with no ads between stories or inside articles for the next 30 minutes.
- Reward: 30 minutes without ads (feature_time, 30 minutes from when the game is confirmed)
- When the reward ends: When the 30 minutes end, a small note says the usual stories and ads are back and the ad-free half hour can be earned again tomorrow, which gives heavy readers a reason to come back daily.
- Cost: Costs ~$0.0582 per reward to serve; pays for itself above $58.20 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 30 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $3.30; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time

### c03 · Product change: 15 minutes of stories without the ad blocks inside
- product_change, lens `subscriber`, trigger on `s24`: The reader reaches the next-story bar at the end of their third story of the day.
- Offer: Play a short game to read the next 15 minutes without the ad blocks inside stories.
- Reward: 15 minutes without in-story ad blocks (feature_time, 15 minutes from when the game is finished)
- When the reward ends: When the 15 minutes end, the ad blocks return in the next story, and the card comes back at a later story end so the reader can play again, up to twice a day.
- Cost: Costs ~$0.0291 per reward to serve; pays for itself above $29.10 eCPM. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes 15 ad-free minutes at 0.2 ads a minute, each worth $9.70 eCPM (the NA Android interstitial rate); if the ads it hides are banners ($0.55 eCPM), it pays for itself above $1.65; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.5 (trigger depth x the proposer's per-user daily cap, 2; not a measured audience). The offer's cap: 2 per day, resets at midnight local time; offered again only after the previous 15 minutes have ended

### c02 · Product change: Save your reading streak after missing a day
- product_change, lens `free_at_limit`, trigger on `s01`: The first home feed open after a missed day, when the reader had a streak of 3 or more days.
- Offer: You missed yesterday. Play a short game to keep your 5-day reading streak, one save this week.
- Reward: 1 reading streak save (streak_protection, covers one missed day)
- When the reward ends: The next time a day is missed within the week, the streak resets with a note that a new save opens in a few days, which nudges the reader to open a story every day.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0 (trigger depth x the proposer's per-user daily cap, 0, less than once a day; not a measured audience). The offer's cap: 1 per week, resets 7 days after it was taken.

### c06 · Product change: Open straight to your favorite section for 7 days
- product_change, lens `drifting`, trigger on `s01`: On the first visit after 3 or more days away, the user taps a section tab other than Top Stories on the home feed.
- Offer: Want the app to open on Sports every time? Play a short game to start on Sports for the next 7 days.
- Reward: 1 opening section (cosmetic, 7 days)
- When the reward ends: When the 7 days end, the app opens on Top Stories again with a card offering another week on Sports, which gives the reader a reason to return and play again.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0 (trigger depth x the proposer's per-user daily cap, 0, less than once a day; not a measured audience). The offer's cap: 1 per week, resets 7 days after it was taken.

### c09 · Product change: Pick which kinds of sponsored stories you see for 7 days
- product_change, lens `ledger_l2`, trigger on `s01`: The reader opens the home feed for the third time in a day.
- Offer: Play a short sponsored game and choose which kinds of sponsored stories show in your feed for the next 7 days.
- Reward: 1 sponsored story choices (cosmetic, 7 days from when the game ends)
- When the reward ends: When the 7 days end, the feed goes back to its usual mix of sponsors with a small card offering to set the choice again for another game, which brings regular readers back each week.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0 (trigger depth x the proposer's per-user daily cap, 0, less than once a day; not a measured audience). The offer's cap: 1 per week per reader, resets 7 days after it was taken.

## Dropped by code

- c01 · Product change: Read stories without ads for 20 minutes: duplicate of c07: same benefit (ad-free reading)
- c08 · Product change: 30 minutes of articles without the advertisement blocks: duplicate of c07: same benefit (ad-free reading)
- c04 · Product change: Keep your reading streak after a missed day: duplicate of c02: same benefit (reading streak save)
- c05 · Product change: Keep your reading streak after a missed day: duplicate of c02: same benefit (reading streak save)
