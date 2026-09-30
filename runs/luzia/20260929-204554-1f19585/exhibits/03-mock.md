# Mock: luzia

Contract: **PASS** (0 errors). Model spend this stage: $5.3726.

Batch plan: 4 of 4 batches fit the $25.00 cap with $0.00 already spent, at worst case: $10.64 + $2.68 spare for one retry.

| Batch | Screens | Result | Worst case |
|---|---|---|---|
| 1 | s01 s05 s06 s07 | drawn | $2.68 |
| 2 | s02 s08 s09 s11 | drawn | $2.67 |
| 3 | s13 s14 s04 s15 | drawn | $2.68 |
| 4 | s03 | drawn | $2.60 |

| Screen | Name | data-el placed / expected | Render |
|---|---|---|---|
| s01 | Chats home | 32 / 32 | `mock/renders/s01.png` |
| s06 | Teacher chat thread | 17 / 17 | `mock/renders/s06.png` |
| s05 | Toki intro modal | 8 / 8 | `mock/renders/s05.png` |
| s07 | Custom Bestie intro | 5 / 5 | `mock/renders/s07.png` |
| s02 | Services (Routines empty) | 15 / 15 | `mock/renders/s02.png` |
| s08 | Routine setup chat | 12 / 12 | `mock/renders/s08.png` |
| s09 | Services (routines list) | 15 / 15 | `mock/renders/s09.png` |
| s11 | Create app prompt | 12 / 12 | `mock/renders/s11.png` |
| s13 | App detail | 11 / 11 | `mock/renders/s13.png` |
| s14 | Create – Explore scrolled | 31 / 31 | `mock/renders/s14.png` |
| s04 | New chat persona picker | 11 / 11 | `mock/renders/s04.png` |
| s15 | Teacher new chat | 11 / 11 | `mock/renders/s15.png` |
| s03 | Create – Explore apps | 18 / 18 | `mock/renders/s03.png` |

Edges wired: 26 / 29 in scope.
