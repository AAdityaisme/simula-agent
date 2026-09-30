# Mock: perplexity

Contract: **PASS** (0 errors). Model spend this stage: $2.0445.

Batch plan: 2 of 2 batches fit the $25.00 cap with $0.00 already spent, at worst case: $5.34 + $2.67 spare for one retry.

| Batch | Screens | Result | Worst case |
|---|---|---|---|
| 1 | s01 s04 s07 s10 | drawn | $2.67 |
| 2 | s09 s02 s03 s08 | drawn | $2.67 |

| Screen | Name | data-el placed / expected | Render |
|---|---|---|---|
| s01 | Search home | 14 / 14 | `mock/renders/s01.png` |
| s04 | Computer limited preview sheet | 9 / 9 | `mock/renders/s04.png` |
| s07 | Welcome to Computer intro | 7 / 7 | `mock/renders/s07.png` |
| s10 | Computer home | 11 / 11 | `mock/renders/s10.png` |
| s09 | Computer setup checklist expanded | 17 / 17 | `mock/renders/s09.png` |
| s02 | Search composer focused | 12 / 12 | `mock/renders/s02.png` |
| s03 | Library | 12 / 12 | `mock/renders/s03.png` |
| s08 | Computer composer focused | 9 / 9 | `mock/renders/s08.png` |

Edges wired: 5 / 7 in scope.
