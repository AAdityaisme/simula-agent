# Explore: luzia (co.thewordlab.luzia 5.44.0)

- Device: `emulator-5554` (mobile-mcp `simula`)
- Budget `transfer`: 51 actions, stop: **action cap (40)**
- States: 15 (1 modal, 14 screen)
- Bottom tabs: 3 found, 3 visited
- Relaunches: 0 (tour cap 3, then 2 for the passes after)
- Paywall or plans screen captured: no
- Content filter: none found
- Redaction: 3 strings listed in SIMULA_REDACT; 0 element texts redacted at capture
- Checklist answered: root, all_tabs; open: paywall_or_membership, settings, limit
- Denied taps: 16 logged, 0 executed. The deny-list is English only: a confirm button in another language isn't caught, and the store's billing screen getting BACK at once is the guard that works in any language (Play purchases only).

## Settle signal

Element-list call time over 193 calls: median 0.18 s, p90 0.77 s, max 5.78 s; 189/193 under the 2 s settle threshold.

## Core loop (the free experience)

Core action: send messages in a conversation and read the replies ('Teacher'; text box + send on s06); 3 of 3 passes attempted, 3 completed.

- pass 1: reply started 0.8 s, finished 3.9 s, 95 chars
- pass 2: reply started 0.8 s, finished 4.0 s, 366 chars
- pass 3: reply started 0.8 s, finished 20.3 s, 339 chars
- All 3 passes ran and met no limit.

## Replay check

18/19 replayed moves reached the recorded screen (95%; same screen as the explorer decides it everywhere: the fingerprint, else the model and the structure agree). Fingerprint alone: 16/19 (84%).

## Invariants

- hops: 19/23 arrived (5 judged by what the screen shows)
- model arrival verdicts: 8 same, 4 one action, 0 elsewhere; the structure vetoed a model same 0 times, and said same where the model did not 1 times; a model same whose identifying text the element list lacks: 0
- covered controls not tapped: 7
- side-effect actions the model saw: 5
- settle waits: 3 (3 settled), seconds 10.3, 10.5, 23.6
- model finished verdicts refused while send still looked busy: 0
- walk picks the screen no longer showed: 0
- model done or back refused while the screen had untried options: 0
- model calls that failed (each traced where it happened): 0

## States

| id | kind | over | depth | untried | ended by | first words |
|---|---|---|---|---|---|---|
| s01 | screen |  | 0 | 18 | every option tried or its cap spent | Chats |
| s02 | screen |  | 1 | 0 | every option tried or its cap spent | Services |
| s03 | screen |  | 1 | 3 | had work left when the tour stopped | My Apps |
| s04 | screen |  | 1 | 20 | had work left when the tour stopped | New chat |
| s05 | modal | s01 | 1 | 13 | had work left when the tour stopped | Tok-Tok! I'm Toki and I need your help! |
| s06 | screen |  | 1 | 16 | had work left when the tour stopped | Hello, I'm Teacher. Ask me for advice, answers, or |
| s07 | screen |  | 1 | 5 | had work left when the tour stopped | Create your own Bestie |
| s08 | screen |  | 2 | 9 | had work left when the tour stopped | Today |
| s09 | screen |  | 2 | 8 | had work left when the tour stopped | Services |
| s10 | screen |  | 2 | 11 | had work left when the tour stopped | Updates |
| s11 | screen |  | 2 | 9 | had work left when the tour stopped | Create |
| s12 | screen |  | 2 | 5 | had work left when the tour stopped | My Apps |
| s13 | screen |  | 2 | 8 | had work left when the tour stopped | Open app |
| s14 | screen |  | 2 | 16 | had work left when the tour stopped | My Apps |
| s15 | screen |  | 1 | 9 | had work left when the tour stopped | Ask Teacher |
