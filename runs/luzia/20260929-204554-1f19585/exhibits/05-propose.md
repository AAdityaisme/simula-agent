# 05 · propose

5 lenses, 8 live candidates (0 flagged for an unobserved term), 2 dropped, 0 with near-miss ids repaired by code (`resolve:<id>` lines in trace.jsonl).

- Mock coverage: 10 of 10 ideas start on a screen the mock draws (counted before the mock-scope filter); the filter dropped 0.
- Top-up call: not needed (8 distinct after dedupe).

| Lens | Kind | Focus |
|---|---|---|
| Free user at a limit | fixed | A free user who has just hit a limit, a paywall, or a locked item, or would if the app had one. What do they want right now that a rewarded play could give them, partially and for a while? |
| Subscriber | fixed | A paying user. What do they still lack or want more of that an opt-in rewarded play could add without devaluing what they pay for? If the app has no subscription, take its most engaged user. |
| Drifting user | fixed | A user whose visits are thinning out. What would bring them back or protect progress they would lose, through a rewarded play at a natural moment rather than an interruption? |
| Someone else in the app: By Luzia | ledger | Someone in this app besides the viewer, seen as "By Luzia". Look for ideas where the viewer's rewarded play also helps or involves them, and the viewer gets something they would want for their own use of the app; helping the other person comes on top of that, not instead. |
| Someone else in the app: Luzia is AI and can make mistakes. Creations are public once published. | ledger | Someone in this app besides the viewer, seen as "Luzia is AI and can make mistakes. Creations are public once published.". Look for ideas where the viewer's rewarded play also helps or involves them, and the viewer gets something they would want for their own use of the app; helping the other person comes on top of that, not instead. |

## Candidates, by reach

### c07 · Product change: Unlock a gold card frame for your apps
- product_change, lens `ledger_v1`, trigger on `s14`: The user returns to the Explore list after liking at least one app during this visit.
- Offer: Play a short game to unlock a gold frame for your own app cards for 7 days, and add 1 backing to the creator of the app you just liked.
- Reward: 1 gold card frame for your published apps (cosmetic, 7 days)
- When the reward ends: When the 7 days end, the frame goes back to plain with a note that backing another app you like brings it back, which pulls the user back into Explore.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1.5 (trigger depth x the proposer's per-user daily cap, 3; not a measured audience). The offer's cap: 3 per day, resets at midnight local time; each app can be backed by the same user at most once a day.

### c09 · Product change: Cheer an app's maker and wear a supporter badge
- product_change, lens `ledger_v2`, trigger on `s13`: The user taps like on an app's detail page.
- Offer: Play a short game to send this app's maker a cheer and get a supporter badge on your profile for 7 days.
- Reward: 1 supporter badge (cosmetic, 7 days)
- When the reward ends: When the 7 days end, the badge fades with a note that liking and cheering another app brings it back, which draws the user back to browse Explore.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1.5 (trigger depth x the proposer's per-user daily cap, 3; not a measured audience). The offer's cap: 3 per day, resets at midnight local time; at most 1 cheer per app per day from the same user.

### c01 · Product change: One extra growth step for Toki today
- product_change, lens `free_at_limit`, trigger on `s01`: Chats home opens for a user who has met Toki and has not taken today's bonus treat.
- Offer: Play a short game to give Toki a bonus treat: 1 extra growth step, once today.
- Reward: 1 growth step (cosmetic, permanent, one per day)
- When the reward ends: Once today's treat is used, the strip shows when tomorrow's treat unlocks, giving the user a reason to come back and care for Toki again.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time

### c02 · Product change: Your published app shown in Explore's Spotlight for 24 hours
- product_change, lens `free_at_limit`, trigger on `s03`: A user who has at least one published mini app opens the Explore tab.
- Offer: Play a short game to show your app in the Spotlight row for 24 hours.
- Reward: 1 Spotlight placement (cosmetic, 24 hours)
- When the reward ends: When the 24 hours end, the app leaves the Spotlight row with a note showing the likes and saves it gained, so the creator comes back to feature it again or build another app.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per user per day, resets at midnight local time; each app at most once per week

### c03 · Product change: A new outfit for your virtual pet Toki for 3 days
- product_change, lens `subscriber`, trigger on `s01`: User opens the home screen for the first time that day while the virtual pet is turned on and the pet card is showing.
- Offer: Play a short game to dress Toki in a new outfit for 3 days. 1 per day.
- Reward: 1 pet outfit (cosmetic, 3 days)
- When the reward ends: When the 3 days end, Toki goes back to its usual look and the button offers a new outfit the next day, giving engaged users a reason to return daily.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day per user, resets at midnight local time.

### c04 · Product change: Show your new app in a Player Picks row for 24 hours
- product_change, lens `subscriber`, trigger on `s03`: User publishes a mini app and then returns to the Explore screen.
- Offer: Play a short game to show your new app in the Player Picks row on Explore for 24 hours.
- Reward: 1 spot in Player Picks (cosmetic, 24 hours)
- When the reward ends: When the 24 hours end, the app leaves the Player Picks row and keeps whatever likes and saves it earned, so the creator comes back to make or remix another app to show next.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 1 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day per user, resets at midnight local time; each app can be shown at most 3 times in total.

### c08 · Product change: Feature your remix for 24 hours, credited to the original
- product_change, lens `ledger_v1`, trigger on `s11`: The user taps remix on an app's detail page and the create screen opens.
- Offer: Play a short game to feature your remix in the Fresh remixes row for 24 hours after you publish it, with credit to the original creator.
- Reward: 24 hours in the Fresh remixes row on Explore (queue_priority, 24 hours, starting when the remix is published)
- When the reward ends: When the 24 hours end, the remix leaves the row with a note that the next remix can be featured tomorrow, which brings the user back to make another.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost (the peak-capacity cost is real but unknown); one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0.5 (trigger depth x the proposer's per-user daily cap, 1; not a measured audience). The offer's cap: 1 per day, resets at midnight local time; one feature per remix.

### c06 · Product change: Your mini app shown in a community row for 24 hours
- product_change, lens `drifting`, trigger on `s03`: A user who has published at least one mini app opens the Create tab's Explore catalog, and their last session was 3 or more days ago.
- Offer: Welcome back! Play a short game to show your app in the 'From returning creators' row for 24 hours and get a 7-day creator badge.
- Reward: 1 spot in the returning creators row (cosmetic, 24 hours in the row, badge for 7 days)
- When the reward ends: When the 24 hours end, the app leaves the row with a note showing any new likes and saves it picked up, which pulls the creator back to check on it and make or remix another app.
- Cost: Costs nothing extra to serve, so any completed view pays for it. A rewarded view earns $9.20-16.49 eCPM in North America on Android, $1.90 in LATAM. Assumes no marginal serving cost; one view per reward; a publisher-net eCPM (share 1).
- Reach scenario: 0 (trigger depth x the proposer's per-user daily cap, 0, less than once a day; not a measured audience). The offer's cap: 1 per week per user, and only on a return after 3 or more days away; resets 7 days after it was taken.

## Dropped by code

- c10 · Product change: Your remix shown in Fresh remixes for 24 hours: duplicate of c08: same benefit (Fresh remixes placement)
- c05 · Product change: A treat outfit for Toki when you come back: duplicate of c03: same benefit (pet outfit)
