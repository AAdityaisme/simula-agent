# Explore: aol (com.aol.mobile.aolapp 7.81.0)

- Device: `emulator-5554` (mobile-mcp `simula`)
- Budget `transfer`: 98 actions, stop: **action cap (40)**
- States: 64 (1 external, 2 modal, 61 screen)
- Bottom tabs: 2 found, 2 visited
- Relaunches: 4 (tour cap 3, then 2 for the passes after)
  - 1: no recorded way from s02 to s01
  - 2: no recorded way from s15 to s02
  - 3: no recorded way from s15 to s16
  - 4: no recorded way from s15 to s01
- Paywall or plans screen captured: yes, s05, prices: 'New Temu users get jackets for $2.36 TEMU in Taboola advertising section · Sponsored: learn about this recommendation (opens dialog)'; 'New Temu users get jackets for $2.36'
- Content filter: none found
- Redaction: 3 strings listed in SIMULA_REDACT; 0 element texts redacted at capture
- Checklist answered: root, all_tabs, paywall_or_membership, limit; open: settings
- Denied taps: 23 logged, 0 executed. The deny-list is English only: a confirm button in another language isn't caught, and the store's billing screen getting BACK at once is the guard that works in any language (Play purchases only).

## Settle signal

Element-list call time over 363 calls: median 0.90 s, p90 5.47 s, max 34.76 s; 274/363 under the 2 s settle threshold.

## Core loop (the free experience)

Core action: open and read items from the list on s01 (e.g. 'Amber Glenn sobs after giving partner Pa'); 3 of 3 passes attempted, 3 completed.

- pass 1: load started 2.2 s, finished 2.2 s, 630 chars
- pass 2: load started 3.3 s, finished 3.3 s, 471 chars
- pass 3: load started 2.1 s, finished 2.1 s, 823 chars
- Stopped by the app: paywall (s05) on pass 3.

## Replay check

29/33 replayed moves reached the recorded screen (88%; same screen as the explorer decides it everywhere: the fingerprint, else the model and the structure agree). Fingerprint alone: 22/33 (67%).

## Invariants

- hops: 16/20 arrived (3 judged by what the screen shows)
- model arrival verdicts: 3 same, 2 one action, 3 elsewhere; the structure vetoed a model same 0 times, and said same where the model did not 3 times; a model same whose identifying text the element list lacks: 0
- covered controls not tapped: 7
- side-effect actions the model saw: 0
- settle waits: 3 (3 settled), seconds 26.8, 7.0, 42.5
- model finished verdicts refused while send still looked busy: 0
- walk picks the screen no longer showed: 0
- model done or back refused while the screen had untried options: 0
- model calls that failed (each traced where it happened): 0

## States

| id | kind | over | depth | untried | ended by | first words |
|---|---|---|---|---|---|---|
| s01 | screen |  | 0 | 12 | every option tried or its cap spent | Sidebar button. Double tap or slide two fingers fr |
| s02 | screen |  | 1 | 0 | no way back to it |  |
| s03 | modal | s01 | 1 | 15 | had work left when the tour stopped | See the latest news |
| s04 | screen |  | 1 | 2 | had work left when the tour stopped | Saved articles |
| s05 | screen |  | 1 | 11 | had work left when the tour stopped | Entertainment Weekly |
| s06 | screen |  | 2 | 13 | had work left when the tour stopped | Marina Watts |
| s07 | screen |  | 3 | 12 | had work left when the tour stopped | TEMU |
| s08 | screen |  | 1 | 11 | had work left when the tour stopped | SUGGESTED SEARCHES |
| s09 | modal | s01 | 1 | 12 | had work left when the tour stopped | Allow Location permission to get the most accurate |
| s10 | screen |  | 1 | 34 | had work left when the tour stopped | Sidebar button. Double tap or slide two fingers fr |
| s11 | screen |  | 1 | 32 | had work left when the tour stopped | Sidebar button. Double tap or slide two fingers fr |
| s12 | screen |  | 2 | 12 | had work left when the tour stopped | E! |
| s13 | screen |  | 3 | 14 | had work left when the tour stopped | Sasha Wayman |
| s14 | screen |  | 4 | 16 | had work left when the tour stopped | TEMU |
| s15 | screen |  | 1 | 0 | had work left when the tour stopped |  |
| s16 | screen |  | 0 | 24 | no way back to it | Sidebar button. Double tap or slide two fingers fr |
| s17 | screen |  | 2 | 5 | had work left when the tour stopped | There isn't any spending activity yet |
| s18 | external | s15 | 2 |  |  | help.aol.com |
| s19 | screen |  | 2 | 14 | had work left when the tour stopped | Marina Watts |
| s20 | screen |  | 3 | 11 | had work left when the tour stopped | Amber Glenn and Pasha Pashkov on ‘Dancing With the |
| s21 | screen |  | 4 | 13 | had work left when the tour stopped | Amber Glenn and Pasha Pashkov on ‘Dancing With the |
| s22 | screen |  | 5 | 10 | had work left when the tour stopped | “The hardest part of the Foxtrot for me is that it |
| s23 | screen |  | 6 | 6 | had work left when the tour stopped |  |
| s24 | screen |  | 7 | 11 | had work left when the tour stopped | A rehearsal clip of the dancing duo shown during T |
| s25 | screen |  | 8 | 11 | had work left when the tour stopped | Glenn found their song choice,“Hold the Line” by T |
| s26 | screen |  | 9 | 11 | had work left when the tour stopped | Glenn found their song choice,“Hold the Line” by T |
| s27 | screen |  | 10 | 9 | had work left when the tour stopped | Glenn found their song choice,“Hold the Line” by T |
| s28 | screen |  | 11 | 9 | had work left when the tour stopped | Amber Glenn on ‘Dancing With the Stars’Credit: abc |
| s29 | screen |  | 12 | 10 | had work left when the tour stopped | Amber Glenn on ‘Dancing With the Stars’Credit: abc |
| s30 | screen |  | 13 | 12 | had work left when the tour stopped | Amber Glenn on ‘Dancing With the Stars’Credit: abc |
| s31 | screen |  | 14 | 9 | had work left when the tour stopped | The paramedics came to check out the dancing pro,  |
| s32 | screen |  | 15 | 22 | had work left when the tour stopped | TV Insider |
| s33 | screen |  | 3 | 15 | had work left when the tour stopped | Sasha Wayman |
| s34 | screen |  | 4 | 14 | had work left when the tour stopped | Yahoo Search / Secure Business It Networks Solutio |
| s35 | screen |  | 5 | 14 | had work left when the tour stopped | Originally appeared on |
| s36 | screen |  | 6 | 16 | had work left when the tour stopped | Originally appeared on |
| s37 | screen |  | 7 | 16 | had work left when the tour stopped | Kristi Noem |
| s38 | screen |  | 8 | 14 | had work left when the tour stopped | Kristi, 54, cited irreconcilable differences as th |
| s39 | screen |  | 9 | 14 | had work left when the tour stopped | Kristi, 54, cited irreconcilable differences as th |
| s40 | screen |  | 10 | 15 | had work left when the tour stopped | Kristi, 54, cited irreconcilable differences as th |
| s41 | screen |  | 11 | 13 | had work left when the tour stopped | E! News has reached out to attorneys for Kristi an |
| s42 | screen |  | 12 | 11 | had work left when the tour stopped | At the time, a rep for Kristi—who shares daughters |
| s43 | screen |  | 13 | 12 | had work left when the tour stopped | More from E! Online |
| s44 | screen |  | 14 | 12 | had work left when the tour stopped | Dylan Sprouse Reveals Name of His and Barbara Palv |
| s45 | screen |  | 15 | 12 | had work left when the tour stopped | in a statement on March 31, “and they ask for priv |
| s46 | screen |  | 16 | 9 | had work left when the tour stopped | Kristi Noem/Facebook |
| s47 | screen |  | 17 | 11 | had work left when the tour stopped | Kristi Noem/Facebook |
| s48 | screen |  | 18 | 12 | had work left when the tour stopped | reported that Bryon hung up the phone after making |
| s49 | screen |  | 19 | 12 | had work left when the tour stopped | After all, the former South Dakota governor was qu |
| s50 | screen |  | 20 | 10 | had work left when the tour stopped | Corey has also denied the rumors. |
| s51 | screen |  | 21 | 10 | had work left when the tour stopped | Photo by Heather Diehl/Getty Images |
| s52 | screen |  | 22 | 9 | had work left when the tour stopped | As for her life with Bryon, Kristi has previously  |
| s53 | screen |  | 23 | 7 | had work left when the tour stopped | She added, “I think that is what this job requires |
| s54 | screen |  | 24 | 6 | had work left when the tour stopped | Audrina Patridge & Michael Ray |
| s55 | screen |  | 25 | 5 | had work left when the tour stopped | The Hillsalumconfirmed her romancewith the "Whiske |
| s56 | screen |  | 26 | 7 | had work left when the tour stopped | The Hillsalumconfirmed her romancewith the "Whiske |
| s57 | screen |  | 27 | 5 | had work left when the tour stopped | The "Mi Gente" singer (real nameJosé Álvaro Osorio |
| s58 | screen |  | 28 | 8 | had work left when the tour stopped | The "Mi Gente" singer (real nameJosé Álvaro Osorio |
| s59 | screen |  | 29 | 6 | had work left when the tour stopped | “We have beenseparated for a while, and it’s prove |
| s60 | screen |  | 30 | 8 | had work left when the tour stopped | “We have beenseparated for a while, and it’s prove |
| s61 | screen |  | 31 | 8 | had work left when the tour stopped | <p>Dakota Johnson &amp; Role Model</p> |
| s62 | screen |  | 32 | 6 | had work left when the tour stopped | On Aug. 19, multiple outlets reported that theFift |
| s63 | screen |  | 1 | 11 | had work left when the tour stopped | Entertainment Weekly |
| s64 | screen |  | 1 | 13 | had work left when the tour stopped | E! |
