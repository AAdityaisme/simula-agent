# Explore: perplexity (ai.perplexity.app.android 2.100.0)

- Device: `emulator-5554` (mobile-mcp `simula`)
- Budget `transfer`: 18 actions, stop: **relaunch cap**
- States: 13 (4 external, 1 modal, 8 screen)
- Bottom tabs: 0 found, 0 visited
- Relaunches: 3 (tour cap 3, then 2 for the passes after)
  - 1: no recorded way from s06 to s01
  - 2: no recorded way from s08 to s07
  - 3: BACK did not return from the external screen (com.aol.mobile.aolapp)
- Paywall or plans screen captured: no
- Content filter: none found
- Redaction: 3 strings listed in SIMULA_REDACT; 1 element texts redacted at capture
- Checklist answered: root, all_tabs; open: paywall_or_membership, settings, limit
- Denied taps: 7 logged, 0 executed. The deny-list is English only: a confirm button in another language isn't caught, and the store's billing screen getting BACK at once is the guard that works in any language (Play purchases only).

## Settle signal

Element-list call time over 132 calls: median 1.04 s, p90 5.52 s, max 30.97 s; 102/132 under the 2 s settle threshold.

## Core loop (the free experience)

Core action: none; 0 of 3 passes attempted, 0 completed.

- no core action found on the screens seen
- No pass ran, so nothing is known about a limit.

## Replay check

1/4 replayed moves reached the recorded screen (25%; same screen as the explorer decides it everywhere: the fingerprint, else the model and the structure agree). Fingerprint alone: 1/4 (25%).

## Invariants

- hops: 2/4 arrived (1 judged by what the screen shows)
- model arrival verdicts: 1 same, 1 one action, 1 elsewhere; the structure vetoed a model same 0 times, and said same where the model did not 0 times; a model same whose identifying text the element list lacks: 0
- covered controls not tapped: 0
- side-effect actions the model saw: 0
- settle waits: 0 (none), seconds -
- model finished verdicts refused while send still looked busy: 0
- walk picks the screen no longer showed: 0
- model done or back refused while the screen had untried options: 0
- model calls that failed (each traced where it happened): 0

## States

| id | kind | over | depth | untried | ended by | first words |
|---|---|---|---|---|---|---|
| s01 | screen |  | 0 | 3 | no way back to it | Library |
| s02 | screen |  | 1 | 7 | had work left when the tour stopped | Library |
| s03 | screen |  | 1 | 11 | had work left when the tour stopped | Projects |
| s04 | screen |  | 2 | 8 | had work left when the tour stopped | drag handle |
| s05 | external | s01 | 1 |  |  | Allow Perplexity to send you notifications? |
| s06 | modal | s05 | 2 | 10 | had work left when the tour stopped | Notifications are off |
| s07 | screen |  | 0 | 6 | no way back to it | Welcome to
Computer |
| s08 | screen |  | 1 | 8 | had work left when the tour stopped | Library |
| s09 | screen | s10 | 0 | 11 | a launch dialog, dismissed | Set up Computer |
| s10 | screen |  | 1 | 7 | had work left when the tour stopped | Set up Computer |
| s11 | external | s10 | 2 |  |  | Aol |
| s12 | external | s11 | 3 |  |  | Sidebar button. Double tap or slide two fingers fr |
| s13 | external | s12 | 4 |  |  | Chats |
