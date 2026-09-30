# Explore: janitorai (com.janitor.ai 2.5.0)

- Device: `emulator-5554` (mobile-mcp `simula`)
- Budget `deep`: 41 actions, stop: **relaunch cap**
- States: 20 (1 external, 15 screen, 4 sheet)
- Bottom tabs: 5 found, 5 visited
- Relaunches: 3 (tour cap 3, then 2 for the passes after)
  - 1: no recorded way from s12 to s02
  - 2: no recorded way from s14 to s13
  - 3: no recorded way from s06 to s13
- Paywall or plans screen captured: yes, s16, prices: '$12.99'
- Content filter: Limited Only
  - check 1: verified by screenshot (`explore/filter/check-01.png`)
  - check 2: verified by screenshot (`explore/filter/check-02.png`)
  - check 3: verified by screenshot (`explore/filter/check-03.png`)
  - check 4: verified by screenshot (`explore/filter/check-04.png`)
- Redaction: 3 strings listed in SIMULA_REDACT; 8 element texts redacted at capture
- Checklist answered: root, all_tabs, paywall_or_membership; open: settings, limit
- Denied taps: 42 logged, 0 executed. The deny-list is English only: a confirm button in another language isn't caught, and the store's billing screen getting BACK at once is the guard that works in any language (Play purchases only).

## Settle signal

Element-list call time over 251 calls: median 1.32 s, p90 3.63 s, max 9.15 s; 181/251 under the 2 s settle threshold.

## Core loop (the free experience)

Core action: open and read items from the list on s03 (e.g. '☆ Rodrick Heffley!, 20:08, 1 chat'); 1 of 8 passes attempted, 1 completed.

- pass 1: load started 0.9 s, finished 0.9 s, 586 chars
- Stopped by the app: sheet opened (s18) on pass 1.

## Replay check

16/17 replayed moves reached the recorded screen (94%; same screen as the explorer decides it everywhere: the fingerprint, else the model and the structure agree). Fingerprint alone: 9/17 (53%).

## Invariants

- hops: 10/12 arrived (1 judged by what the screen shows)
- model arrival verdicts: 1 same, 2 one action, 1 elsewhere; the structure vetoed a model same 0 times, and said same where the model did not 1 times; a model same whose identifying text the element list lacks: 0
- covered controls not tapped: 1
- side-effect actions the model saw: 0
- settle waits: 1 (1 settled), seconds 4.3
- model finished verdicts refused while send still looked busy: 0
- walk picks the screen no longer showed: 0
- model done or back refused while the screen had untried options: 0
- model calls that failed (each traced where it happened): 0

## States

| id | kind | over | depth | untried | ended by | first words |
|---|---|---|---|---|---|---|
| s01 | screen |  | 0 | 14 | every option tried or its cap spent | Build, Share, Explore |
| s02 | screen |  | 1 | 10 | had work left when the tour stopped | Search |
| s03 | screen |  | 1 | 7 | had work left when the tour stopped | My Chats |
| s04 | screen |  | 1 | 4 | had work left when the tour stopped | Notifications |
| s05 | screen |  | 1 | 10 | had work left when the tour stopped | @[redacted] |
| s06 | screen |  | 1 | 24 | had work left when the tour stopped | Hidden Gems |
| s07 | screen |  | 1 | 7 | had work left when the tour stopped |  |
| s08 | screen |  | 1 | 23 | had work left when the tour stopped | 背徳の貴婦人 |
| s09 | screen |  | 1 | 24 | had work left when the tour stopped | Build, Share, Explore |
| s10 | screen |  | 1 | 22 | had work left when the tour stopped | Build, Share, Explore |
| s11 | screen |  | 2 | 33 | had work left when the tour stopped | Build, Share, Explore |
| s12 | external | s11 | 3 |  |  | Tue, Sep 29 |
| s13 | screen |  | 0 | 17 | no way back to it | Build, Share, Explore |
| s14 | sheet | s02 | 2 | 31 | had work left when the tour stopped | 👨 Male |
| s15 | screen |  | 0 | 20 | had work left when the tour stopped | Build, Share, Explore |
| s16 | screen |  | 2 | 8 | had work left when the tour stopped | More memory for
long chats. |
| s17 | screen |  | 3 | 8 | had work left when the tour stopped | More memory for
long chats. |
| s18 | sheet | s03 | 2 | 9 | had work left when the tour stopped | Bottom sheet handle |
| s19 | sheet | s03 | 2 | 19 | had work left when the tour stopped | Bottom sheet handle |
| s20 | sheet | s03 | 2 | 11 | had work left when the tour stopped | Bottom sheet handle |
