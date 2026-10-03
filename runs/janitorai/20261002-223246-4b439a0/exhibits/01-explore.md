# Explore: janitorai (com.janitor.ai 2.5.0)

- Device: `emulator-5554` (mobile-mcp `simula`)
- Budget `deep`: 101 actions, stop: **action cap (80)**
- States: 25 (1 external, 19 screen, 5 sheet)
- Bottom tabs: 5 found, 5 visited
- Relaunches: 1 (tour cap 3, then 2 for the passes after)
  - 1: a launch from com.google.android.apps.nexuslauncher did not find the app where it was left
- Returns to the app as it was left, by a launch (not relaunches): 0
- Paywall or plans screen captured: yes, s14, prices: '$12.99'
- Content filter: Limited Only
  - check 1: verified by screenshot (`explore/filter/check-01.png`)
  - check 2: verified by screenshot (`explore/filter/check-02.png`)
  - check 3: verified by screenshot (`explore/filter/check-03.png`)
  - check 4: verified by screenshot (`explore/filter/check-04.png`)
- Redaction: 3 strings listed in SIMULA_REDACT; 12 element texts redacted at capture
- Checklist answered: root, all_tabs, paywall_or_membership, settings; open: limit
- Denied taps: 54 logged, 0 executed. The deny-list is English only: a confirm button in another language isn't caught, and the store's billing screen getting BACK at once is the guard that works in any language (Play purchases only).

## Settle signal

Element-list call time over 393 calls: median 0.64 s, p90 2.30 s, max 2.64 s; 294/393 under the 2 s settle threshold.

## Core loop (the free experience)

Core action: send messages in a conversation and read the replies ('Miro'; text box + send on s25); 6 of 8 passes attempted, 6 completed.

- pass 1: reply started 2.2 s, finished 78.9 s, 3055 chars
- pass 2: reply started 2.5 s, finished 96.4 s, 3520 chars
- pass 3: reply started 2.4 s, finished 92.0 s, 3283 chars
- pass 4: reply started 2.3 s, finished 95.1 s, 3495 chars
- pass 5: reply started 2.3 s, finished 78.3 s, 2800 chars
- pass 6: reply started 2.3 s, finished 92.3 s, 3079 chars
- stopped: out of time
- Not stopped by the app, but only 6 of 8 passes completed (last: stopped: out of time): no limit showed on those, which doesn't show the app has none.

## Replay check

19/21 replayed moves reached the recorded screen (90%; same screen as the explorer decides it everywhere: the fingerprint, else the model and the structure agree). Fingerprint alone: 16/21 (76%).

## Invariants

- hops: 29/37 arrived (0 judged by what the screen shows)
- model arrival verdicts: 5 same, 6 one action, 1 elsewhere; the structure vetoed a model same 0 times, and said same where the model did not 0 times; a model same whose identifying text the element list lacks: 0
- covered controls not tapped: 1
- side-effect actions the model saw: 0
- settle waits: 6 (2 finished, 4 settled), seconds 82.3, 99.8, 94.8, 98.6, 81.7, 95.2
- model finished verdicts refused while send still looked busy: 0
- walk picks the screen no longer showed: 0
- model done or back refused while the screen had untried options: 0
- model calls that failed (each traced where it happened): 0

## States

| id | kind | over | depth | untried | ended by | first words |
|---|---|---|---|---|---|---|
| s01 | screen |  | 0 | 22 | every option tried or its cap spent | Build, Share, Explore |
| s02 | screen |  | 1 | 26 | every option tried or its cap spent | Search |
| s03 | screen |  | 1 | 2 | every option tried or its cap spent | My Chats |
| s04 | screen |  | 1 | 0 | every option tried or its cap spent | Notifications |
| s05 | screen |  | 1 | 8 | had work left when the tour stopped | @[redacted] |
| s06 | screen |  | 1 | 19 | had work left when the tour stopped | Hidden Gems |
| s07 | screen |  | 1 | 27 | had work left when the tour stopped | Kitsunami |
| s08 | screen |  | 1 | 31 | had work left when the tour stopped | Parkour Reborn RPG |
| s09 | screen |  | 1 | 33 | had work left when the tour stopped | Search |
| s10 | screen |  | 1 | 31 | had work left when the tour stopped | Build, Share, Explore |
| s11 | screen |  | 2 | 26 | had work left when the tour stopped | Build, Share, Explore |
| s12 | external | s11 | 3 |  |  | OOC |
| s13 | sheet | s02 | 2 | 14 | had work left when the tour stopped | Bottom sheet handle |
| s14 | screen |  | 2 | 8 | had work left when the tour stopped | More memory for
long chats. |
| s15 | screen |  | 3 | 8 | had work left when the tour stopped | More memory for
long chats. |
| s16 | screen |  | 2 | 31 | had work left when the tour stopped | Search |
| s17 | screen |  | 2 | 9 | had work left when the tour stopped | Settings |
| s18 | sheet | s03 | 2 | 17 | had work left when the tour stopped | Bottom sheet handle |
| s19 | sheet | s03 | 2 | 7 | had work left when the tour stopped | Bottom sheet handle |
| s20 | sheet | s03 | 2 | 17 | had work left when the tour stopped | Bottom sheet handle |
| s21 | sheet | s03 | 2 | 11 | had work left when the tour stopped | Bottom sheet handle |
| s22 | screen |  | 2 | 16 | had work left when the tour stopped | Navigate up |
| s23 | screen |  | 2 | 7 | had work left when the tour stopped | Media Library |
| s24 | screen |  | 2 | 12 | had work left when the tour stopped | Navigate up |
| s25 | screen |  | 3 | 13 | had work left when the tour stopped | Miro |
