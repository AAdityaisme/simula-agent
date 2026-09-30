# Mock: aol

Contract: **FAIL** (25 errors). Model spend this stage: $9.1899.

Batch plan: 8 of 15 batches fit the $25.00 cap with $0.00 already spent, at worst case: $21.47 + $2.70 spare for one retry.

| Batch | Screens | Result | Worst case |
|---|---|---|---|
| 1 | s01 s03 s09 s16 | drawn | $2.69 |
| 2 | s10 s32 s05 s19 | drawn | $2.70 |
| 3 | s33 s13 s20 s35 | drawn | $2.69 |
| 4 | s14 s64 s15 s17 | drawn | $2.67 |
| 5 | s21 s22 s23 s24 | drawn | $2.68 |
| 6 | s25 s26 s27 s28 | drawn | $2.68 |
| 7 | s29 s30 s31 s12 | drawn | $2.68 |
| 8 | s34 s36 s37 s38 | drawn | $2.69 |
| 9 | s39 s40 s41 s42 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.69 |
| 10 | s43 s44 s45 s46 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.68 |
| 11 | s47 s48 s49 s50 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.68 |
| 12 | s51 s52 s53 s54 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.67 |
| 13 | s55 s56 s57 s58 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.67 |
| 14 | s59 s60 s61 s62 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.67 |
| 15 | s08 | not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap | $2.60 |

| Screen | Name | data-el placed / expected | Render |
|---|---|---|---|
| s01 | Home news feed | 40 / 40 | `mock/renders/s01.png` |
| s16 | Home feed (refreshed) | 40 / 40 | `mock/renders/s16.png` |
| s10 | Home feed (scrolled) | 42 / 42 | `mock/renders/s10.png` |
| s32 | Related stories list | 25 / 25 | `mock/renders/s32.png` |
| s05 | Article: DWTS story (top) | 25 / 25 | `mock/renders/s05.png` |
| s19 | Article: DWTS story (ad rotated) | 31 / 31 | `mock/renders/s19.png` |
| s33 | Article: Noem story (ad rotated) | 29 / 29 | `mock/renders/s33.png` |
| s13 | Article: Noem story with display ad | 31 / 31 | `mock/renders/s13.png` |
| s20 | Article: DWTS body with display ad | 26 / 26 | `mock/renders/s20.png` |
| s35 | Article: Noem story TE Connectivity ad | 23 / 23 | `mock/renders/s35.png` |
| s14 | Article: Noem story mascara ad | 26 / 26 | `mock/renders/s14.png` |
| s64 | Article: Noem story (repeat open) | 18 / 18 | `mock/renders/s64.png` |
| s15 | AOL sign-in | 4 / 4 | `mock/renders/s15.png` |
| s03 | Accounts sidebar | 21 / 21 | `mock/renders/s03.png` |
| s17 | Receipts (empty) | 6 / 6 | `mock/renders/s17.png` |
| s09 | Weather location prompt | 7 / 7 | `mock/renders/s09.png` |
| s21 | Article: DWTS display ad detail | 29 / 29 | `mock/renders/s21.png` |
| s22 | Article: DWTS body continued | 27 / 27 | `mock/renders/s22.png` |
| s23 | Article: DWTS (vision capture) | 2 / 2 | `mock/renders/s23.png` |
| s24 | Article: DWTS with next-article bar | 19 / 19 | `mock/renders/s24.png` |
| s25 | Article: DWTS photo section | 17 / 17 | `mock/renders/s25.png` |
| s26 | Article: DWTS quotes | 13 / 13 | `mock/renders/s26.png` |
| s27 | Article: DWTS body continued 2 | 10 / 10 | `mock/renders/s27.png` |
| s28 | Article: DWTS judges section | 11 / 11 | `mock/renders/s28.png` |
| s29 | Article: DWTS newsletter plug | 15 / 15 | `mock/renders/s29.png` |
| s30 | Article: DWTS newsletter plug (scrolled) | 15 / 15 | `mock/renders/s30.png` |
| s31 | Article: DWTS ending | 14 / 14 | `mock/renders/s31.png` |
| s12 | Article: Noem divorce story (top) | 31 / 31 | `mock/renders/s12.png` |
| s34 | Article: Noem story display ad | 21 / 21 | `mock/renders/s34.png` |
| s36 | Article: Noem body continued | 25 / 25 | `mock/renders/s36.png` |
| s37 | Article: Noem body with source links | 26 / 26 | `mock/renders/s37.png` |
| s38 | Article: Noem family section | 31 / 31 | `mock/renders/s38.png` |
| s39 | Article: Noem More from E! Online | 0 / 34 | `mock/renders/s39.png` |
| s40 | Article: Noem statement section | 0 / 38 | `mock/renders/s40.png` |
| s41 | Article: Noem photo section | 0 / 33 | `mock/renders/s41.png` |
| s42 | Article: Noem photo caption | 0 / 28 | `mock/renders/s42.png` |
| s43 | Article: Noem phone quote | 0 / 27 | `mock/renders/s43.png` |
| s44 | Article: Noem DHS section | 0 / 27 | `mock/renders/s44.png` |
| s45 | Article: Noem hearing section | 0 / 22 | `mock/renders/s45.png` |
| s46 | Article: Noem hearing continued | 0 / 18 | `mock/renders/s46.png` |
| s47 | Article: Noem open book quote | 0 / 18 | `mock/renders/s47.png` |
| s48 | Article: Noem Elysian quote | 0 / 22 | `mock/renders/s48.png` |
| s49 | Article: celebrity splits gallery start | 0 / 15 | `mock/renders/s49.png` |
| s50 | Article: celebrity splits gallery | 0 / 12 | `mock/renders/s50.png` |
| s51 | Article: gallery Patridge | 0 / 12 | `mock/renders/s51.png` |
| s52 | Article: gallery Balvin | 0 / 15 | `mock/renders/s52.png` |
| s53 | Article: gallery with expand buttons | 0 / 10 | `mock/renders/s53.png` |
| s54 | Article: gallery Seyfried | 0 / 8 | `mock/renders/s54.png` |
| s55 | Article: gallery Seyfried caption | 0 / 9 | `mock/renders/s55.png` |
| s56 | Article: gallery Dakota Johnson | 0 / 11 | `mock/renders/s56.png` |
| s57 | Article: gallery Johnson caption | 0 / 9 | `mock/renders/s57.png` |
| s58 | Article: gallery Osbourne | 0 / 10 | `mock/renders/s58.png` |
| s59 | Article: gallery Osbourne caption | 0 / 8 | `mock/renders/s59.png` |
| s60 | Article: gallery Joey Bada$$ | 0 / 9 | `mock/renders/s60.png` |
| s61 | Article: gallery Osbourne quote | 0 / 11 | `mock/renders/s61.png` |
| s62 | Article: gallery end | 0 / 9 | `mock/renders/s62.png` |
| s08 | Search | 0 / 12 | `mock/renders/s08.png` |

Edges wired: 17 / 71 in scope.

| Error | Screen | Detail |
|---|---|---|
| undrawn_screen | s39 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s40 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s41 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s42 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s43 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s44 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s45 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s46 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s47 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s48 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s49 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s50 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s51 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s52 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s53 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s54 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s55 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s56 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s57 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s58 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s59 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s60 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s61 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s62 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |
| undrawn_screen | s08 | screen not drawn: $ cap reached: over budget: batches 9-15 don't fit the mock's $ cap at worst case; raise with --usd-cap |

Contract errors are fixed in QA round 1, not by regenerating.
