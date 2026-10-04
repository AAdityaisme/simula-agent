# Luzia Try-It Ad: spike notes

Files: `tryit.html` (197 KB, single file, every asset a data: URI, no external hosts; the font is inlined so Google Fonts is not needed), `attributes.json`, this file.
Source run: `/Users/aadi/simula-local/exp-connect/runs/luzia/20260929-204554-1f19585/`. Nothing written inside the repo, no commits.

## What it is

Interstitial (INT), advertiser Luzia, goal installs, about 16 s, 411 x 914 phone canvas scaled to fit (checked at 390, 400 and 1100 px wide: no horizontal scroll).

1. Intro (0 to about 5 s). Sponsored character "Teacher": media card, speech bubble with typing dots then the line, name + `@Luzia` footer, three sample-question buttons. Layout follows `template/character_ad.html` (AD badge, bubble with caret, bubble over a bottom gradient, CTA-style bordered buttons, name/@campaign footer). The template's square media is taller here (379 x 480) because the canvas is full screen.
2. Chat (about 5 to 14 s). Tap a question: Luzia's real Teacher thread slides in (the mock's own push animation), the user bubble is already sent, 0.8 s pause, answer streams over 2.8 s, then the reply action row appears. After 5 s it moves to the end card; tapping the composer moves on sooner.
3. End card. Teacher face with the Luzia glyph, "Luzia", tagline, "Get Luzia, it's free" button, the app's own AI disclaimer, close X. `Skip` shows from 4 s on screens 1 and 2 and jumps to the end card. X on the end card closes the ad (demo shows "Ad closed" + replay). No tap on screen 1 for 12 s also goes to the end card, so the ad is bounded.
4. The CTA mirrors the template's variables (`CHAR_NAME`, `CAMPAIGN`, `CTA`, `TRACKING_URL`). `TRACKING_URL` is empty here, so a tap shows a toast instead of opening a store page.

## Real (from the app / the mock)

- The whole chat screen: header (back, Teacher avatar, "Teacher", "New" pill, search), "Our new chat" pill, the Teacher greeting (full text), the user bubble style incl. the "00:00" timestamp the app shows, the six reply actions (thumbs up/down, copy, read aloud, star, share), "Ask Teacher" composer, mic button, "Luzia is AI and can make mistakes. Verify important information." This is the mock's `<section data-screen="s06">` markup, its batch-1 CSS rules copied verbatim, and its assets `s06.e21/e26/e10/e12/e17.png`, plus Plus Jakarta Sans and the colour tokens.
- Teacher art: `s01.e13.art.png`, a 318 px pixel crop of the Teacher card on the Chats home screen (s01). Used for the hero and the end-card face.
- Luzia glyph on the end card: the SVG from the mock's s01 tab bar. It is a drawing of the in-app glyph, not the store icon.
- Names and strings: "Teacher", "Luzia", "Our new chat", "Ask Teacher", "New", the greeting, the disclaimer.
- Timing: the reply starts 0.8 s after send and finishes about 3.9 s after (explore, 3 passes). The ad reuses 0.8 s and about 3 s.

What I changed in the s06 markup: the greeting is shown in full (the mock shows only its last line, scrolled under the pill); the bubble, answer and action row are moved down and the answer box grows with the text (the mock hard-codes one reply's line breaks); the question text is swapped in; the input is made non-interactive.

## Invented (each carries an `<!-- INVENTED ... -->` comment in `tryit.html`)

| # | Item | Why / note |
|---|---|---|
| 1 | "AD" badge, "Sponsored by Luzia" | Simula template convention, not app copy |
| 2 | "Skip", 4 s delay | ad chrome |
| 3 | Opening line "Stuck on homework? Ask me anything." | wording from the brief |
| 4 | "TAP A QUESTION TO TRY IT" | prompt label |
| 5 | `@Luzia` byline | template convention (`@campaign`) |
| 6 | Question: "Help me solve 2x + 6 = 14" | not captured in the explore |
| 7 | Question: "Why is the sky blue?" | not captured |
| 8 | Question: "Explain photosynthesis simply" | not captured |
| 9-11 | Three answers | Sample answer, written for this demo. The explore recorded reply length and timing only (95, 366 and 339 characters), never the text. The only reply text it captured is "Hello, what would you like to know? Remember that sometimes my answer may not be exact." (reply to the explorer's "fsfsd"), useless as an ad answer. |
| 12 | Label "Sample answer, written for this demo" | required by the brief; shown under the answer |
| 13 | End-card tagline "Homework help, any time. Resolve your doubts and clarify difficult concepts." | second sentence adapted from the observed greeting |
| 14 | CTA "Get Luzia, it's free" | from the brief. UNVERIFIED: the explore did not record the plan (product model open question q1). Confirm with the advertiser before serving. |
| 15 | Demo-only: "Ad closed", "Replay the demo", install-link toast | not part of the creative |

Observed share: 9 of 20 visible text strings (0.45), about 0.35 by characters. By pixels the middle screen is entirely real, but every persuasive piece (hook, questions, answers, CTA claim) is invented.

## Exclusions and caveats

- Screen s04 (New chat persona picker) is not used. Its content_rating is "unsafe" (it lists the Intimate and Adult personas), yet the approved mock still draws it. No s04 markup, CSS or asset is in the file (checked by hashing every embedded image: only s01.e13 art and s06 icons). The Teacher illustration also appears in s04, but it was taken from the safe s01 card. Only safe screens (s01, s06) feed the ad; s15 was read but not used.
- Luzia's mock QA was partial (`qa_incomplete`, core flow f1 "Continue a chat and get a reply" fails: "s06.type>s06: its screen has no text field to type into"). This matters directly: the mock stops exactly where the ad needs a reply state, so the type-and-reply behaviour is entirely my script, and the answers are invented. I did not use the mock's navigation runtime at all, only static markup and CSS. s06 itself scored well in QA (masked SSIM 0.938, 17/17 elements within 4 dp).
- Art resolution: the Teacher crop is 318 px wide and is shown at about 541 px (1.7x). Flat illustration hides it, but it is soft on a real phone. A production creative needs the advertiser's own art.
- Verification: headless Chrome DOM checks (no console errors, no external requests, all three questions run end to end, skip/X/CTA/replay/idle paths work, geometry of all three screens at scale 1). One composite screenshot was taken and deleted. It showed the intro fine, an end card with a big empty gap, and a chat screen caught mid-slide-animation (a cloning artifact) that still revealed the greeting was clipped to 2 of its 3 lines. I then changed three things (greeting height, end-card layout, s01 art instead of s04 art). Those are verified by bounding boxes, not by eye. Look at the page once before publishing.
