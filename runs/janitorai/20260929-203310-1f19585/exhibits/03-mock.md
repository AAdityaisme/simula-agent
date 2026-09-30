# Mock: janitorai

Contract: **PASS** (0 errors). Model spend this stage: $8.2790.

Batch plan: 4 of 4 batches fit the $25.00 cap with $0.00 already spent, at worst case: $10.77 + $2.70 spare for one retry.

| Batch | Screens | Result | Worst case |
|---|---|---|---|
| 1 | s01 s06 s16 s08 | drawn | $2.70 |
| 2 | s02 s14 s05 s07 | drawn | $2.69 |
| 3 | s03 s18 s04 s15 | drawn | $2.69 |
| 4 | s17 s10 s11 | drawn | $2.69 |

| Screen | Name | data-el placed / expected | Render |
|---|---|---|---|
| s01 | Home character feed | 50 / 50 | `mock/renders/s01.png` |
| s06 | Side menu drawer | 36 / 36 | `mock/renders/s06.png` |
| s16 | Janitor Plus paywall | 20 / 20 | `mock/renders/s16.png` |
| s08 | Character detail | 37 / 37 | `mock/renders/s08.png` |
| s02 | Search | 30 / 30 | `mock/renders/s02.png` |
| s14 | Search filter sheet | 59 / 59 | `mock/renders/s14.png` |
| s05 | My profile | 23 / 23 | `mock/renders/s05.png` |
| s07 | Creator profile | 4 / 4 | `mock/renders/s07.png` |
| s03 | My Chats | 19 / 19 | `mock/renders/s03.png` |
| s04 | Notifications | 7 / 7 | `mock/renders/s04.png` |
| s15 | Home character feed (alternate) | 52 / 52 | `mock/renders/s15.png` |
| s17 | Janitor Plus paywall (scrolled) | 20 / 20 | `mock/renders/s17.png` |
| s18 | Chat history sheet (1 chat) | 21 / 21 | `mock/renders/s18.png` |
| s10 | Home feed scrolled (page 1) | 50 / 50 | `mock/renders/s10.png` |
| s11 | Home feed page 2 | 60 / 60 | `mock/renders/s11.png` |

Edges wired: 17 / 21 in scope.
