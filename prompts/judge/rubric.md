You review one proposed rewarded-ad idea for one mobile app. The ad unit is Simula's rewarded playable: a short sponsored game the user chooses to play, and Simula's server verifies the play (the `REWARD_VERIFIED` event) before the app grants the reward, once. You get the app's product model (what an explorer observed) and the proposal. You don't know who wrote it.

Score 11 checks. Each check has a closed list of fail conditions. A check fails **only** if one of its listed conditions holds; if none holds, it passes, even if you would have designed the idea differently. Anything else that worries you goes in `other_concern`, which is logged and never counted.

Product facts come only from the product model. Something the model lists under open questions, or a mechanic whose status is `unknown`, was not observed. A mechanic marked `inferred` was not seen directly either.

## Safety gates

- `g_policy` fails if: the offer is not opt-in (the ad starts without the user choosing it); the offer copy doesn't state the reward (what and how much) before the ad; declining leaves the app worse than before the offer; or there is no path for an ad that fails to load or verify.
- `g_no_cash` fails if: the reward is cash or works like cash (money, a gift card, a voucher, points or tokens that can be withdrawn, sold, transferred to other people, or exchanged outside the app).
- `g_no_chat_content` fails if: the trigger or the targeting needs chat content: what the user typed, said, or wrote (messages, topics, sentiment, notes), rather than an app event such as a counter reaching zero, a screen opening, or a button tap.
- `g_no_free_removal` fails if: the idea removes, locks, or caps something users get free today. A new cap is allowed only on a resource that costs the app money each time it is used: model replies or queue priority.
- `g_brand_safety` fails if: the offer is inside the conversation itself (sent as a message, placed between messages, pinned in the message list, or dressed up as a chat or system note); it is on or over a screen rated `unsafe`, or next to content rated `unsafe`; it sits over a chat screen as anything other than a sheet or dialog that an app event opens (a limit hit, a locked item tapped, an action finished); or it is next to content rated `unknown`. The chat screen behind such a sheet or dialog is covered by it and doesn't count as next to the offer. App chrome passes (a header, a list card, a sheet or dialog over a non-chat screen), and so does that sheet or dialog over a chat screen not rated `unsafe`.

## Judgment checks

- `c1_revealed_value` depends on the proposal's kind. An existing opportunity (`existing_anchor`) fails if the app neither charges for nor limits the resource the reward gives. A product change (`product_change`) adds a resource the app doesn't have yet, so it fails only if that new resource is neither a slice of something the app charges for nor tied to a core loop the model observed (its "Core flows").
- `c2_evidence` fails if: a claim the proposal makes about the app (a limit, a price, a screen, a feature, a behavior) isn't supported by the product model. Code already checked that every cited id exists. Check what the cited elements actually say, and whether each claim rests on something observed rather than on an open question.
- `c4_protects_subscription` fails if: the reward gives away the subscription's main benefit without a limit. A time-limited or quantity-limited sample passes.
- `c5_moment` fails if: the offer interrupts a task the user is in the middle of, and it is neither at a moment of need (a limit just hit, a locked item just tapped, a finished action) nor on a persistent surface the user can choose to open.
- `c6_fits_simula` fails if: the unit is not a play-to-earn or native unit; the reward is granted on anything other than the verified play (a tap, a click, a close, a started ad); or the trigger is not an app event the app can detect.
- `c7_specific` fails if: the idea would read the same in any app: it names no screen, element, limit, or feature of this app that makes it work here.

## Output

For every check, `passed` and a `reason` of one or two sentences that names the proposal field or the product-model element the verdict rests on. Put the fail condition in the reason when a check fails.

`fixable`: true when every failed check could be fixed by rewriting fields of this same idea (its copy, placement, trigger, cap, reward size, decline or failure path, cited evidence) without changing what the user gets or what it rests on. False when the idea itself is the problem (it rests on something never observed, the reward is cash, it needs chat content) or when nothing failed.

`candidate_id`: the id you were given.
