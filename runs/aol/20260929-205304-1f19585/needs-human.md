## 2026-09-29T21:48:42 · mock

**Needed:** partial output

**Why:** 25 screens not drawn (Article: Noem More from E! Online, Article: Noem statement section, Article: Noem photo section and 22 more): over the mock's budget

**Evidence:**
- `mock/done.json`

**Continue with:** `simula mock aol --run 20260929-205304-1f19585 --profile real --budget transfer --usd-cap 34.19`

## 2026-09-29T22:21:23 · qa

**Needed:** partial output

**Why:** the mock left screens undrawn: s39, s40, s41, s42, s43, s44, s45, s46, s47, s48, s49, s50, s51, s52, s53, s54, s55, s56, s57, s58, s59, s60, s61, s62, s08 (over the $ budget); core flows through an undrawn screen: f2, f6; the review stopped early: the model's answer was cut off

**Evidence:**
- `qa/done.json`

**Continue with:** `simula mock aol --run 20260929-205304-1f19585 --profile real --budget transfer --usd-cap 34.19 && simula qa aol --run 20260929-205304-1f19585 --profile real --budget transfer --usd-cap 18.44 --no-cache`

