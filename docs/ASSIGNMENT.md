# Assignment (verbatim)

Source: Simula's public Notion page, https://simula-ad.notion.site/Simula-App-Monetization-Agent-Take-Home-3e5af70f6f0d818bb2dfc4b70993508e (copied 2026-09-26). Kept in the repo so every PR can be checked against what was actually asked.

---

# Simula App Monetization Agent Take‑Home
Deadline: 72 hours from email send time, email us if you need more time.

## Context
Simula places ads inside AI chat and consumer apps. Part of that job is telling an app where advertising should live inside its product and showing them what it would look like. Today we do it by hand: use the app, map its flows, rebuild the relevant screens, find the monetization opportunities, and mock the proposed changes.
We want an agent to do it. For this take-home you'll build the beginnings of a system that goes mobile app → product model → 1:1 mock → rewarded-ad proposal with as little app-specific work as possible.
[image: simula-app-redesign-pipeline.png]
A rewarded ad is one the user opts into in exchange for something they value. Games have natural exchanges (watch an ad → get currency, lives, energy). Most consumer apps don't, so the question is rarely "where can we put an ad?" and usually "what product mechanic could exist here that creates a real value exchange without hurting the rest of the business?" A subscription app might only surface a rewarded option after a user declines the paywall; another app might need something else entirely.
You do not need prior ad-tech experience; when something is unspecified, make a reasonable choice and explain it.

## What you get
### Reference outputs
Two examples of what we make by hand today: a manually recreated app experience, and slides proposing new rewarded flows inside an existing app. They set the bar for fidelity and product thinking. They are references, not templates to hard-code, and your output should be more detailed than these.
[image: Screenshot 2026-09-19 at 10.40.45 PM.png]
[image: Screenshot 2026-09-19 at 10.40.35 PM.png]
[image: Screenshot 2026-09-19 at 10.40.27 PM.png]

### Test apps
Run your system against the mobile apps for OOC (ooc.ai <http://ooc.ai/>), JanitorAI (janitorai.com <https://janitorai.com/>), Luzia (luzia.com <https://www.luzia.com/en>), and AOL (aol.com <https://www.aol.com/>). The links only identify the products; product understanding must come from the apps themselves. Go deepest on the one that best shows your architecture. For the rest, show enough output to prove the same system transfers without per-app rework.
### Setup
> 
  Models and tools: any LLM, vision model, agent framework, or AI product is fine. We expect AI to do meaningful work throughout the system. Log what you spent.

## Goals
> 
  How to approach this: these instructions are intentionally about outcomes, not implementation. We're evaluating how you operate when the architecture isn't decided for you: how the device is controlled, how the agent decides what to explore, how app knowledge is represented, how agents share context, what is deterministic vs. model-driven, and how failures are handled. Make a sensible call and explain it in your recording. A smaller system that works end to end beats a pile of disconnected demos. If you're genuinely blocked, don't spin — ask questions by emailing Yizhen and Athreya.
### 1. Explore the app
Build an explorer agent that drives the app through the mobile MCP and produces a structured product model: screens and states, the navigation graph and core flows, key UI elements and interactions, modals and transitions, what changes after an action, existing product and monetization mechanics (paywalls, limits, currencies, entitlements), and the visual detail and assets needed to recreate each screen. The format of the product model is up to you.
> 
  Device control: drive the apps through a mobile MCP server on an iOS Simulator or Android emulator, for example mobile-mcp <https://github.com/mobile-next/mobile-mcp>, which exposes screenshots, the accessibility tree, and tap / swipe / type as tools your agent can call. Appium, Maestro, or your own ADB / xcrun simctl wrapper is fine if you already know it. Do not point the agent at the websites. Install the apps early; some need an account.
Things to think about:
- How does the agent pick the next action, and how does it know a screen is done?
- What is a "state": accessibility tree, screenshot, both? How do you diff two of them?
- How do you capture assets (icons, colors, type, copy) with enough fidelity to rebuild the screen?
- Scope: stay in the core experience. If a tap opens the camera, photo library, OS settings, a permission dialog, or an external browser, record that it happens and move on. Settings and account screens only if they matter.
- Could another agent mock the app from your output alone, without exploring the original?
### 2. Recreate it
Generate a high-fidelity interactive mock of the relevant experience from the product model. This is not a rebuild of the production app: no real backend, auth, or data handling; simulate whatever isn't needed to demonstrate the experience. Someone who knows the app should recognize it immediately and be able to move through its important flows. The standard is product fidelity (layout, typography, imagery, spacing, colors, states; navigation, modals, transitions), not production completeness.
> 
  💡 The mock should be substantially generated from the product model. We are evaluating the pipeline app → explore → product model → mock, not your ability to rebuild several apps by hand.
QA loop. Build a way for the system to continuously improve the mock. You decide what gets compared (screenshot diffs, element trees, a second agent walking both flows) and when to stop. 
### 3. Propose rewarded-ad opportunities, then judge them
Use the product model to answer: how should this product change so rewarded advertising works naturally inside it?
Build an agent (or team) with enough expertise on the product, its users, its current monetization, and rewarded ads to propose candidate opportunities. How you give it that expertise is part of the problem: research, retrieval, a knowledge base, specialist agents, examples. We care whether it can apply rewarded-ad knowledge to a new product, not repeat generic best practices. It should recognize two cases:
- Existing opportunity: the app already has something valuable (a resource, limit, entitlement, action) that can anchor a rewarded exchange.
- Product change required: no strong mechanic exists, but adding or changing a constraint, resource, surface, or flow would create one. Product changes are in scope.
Review and judge. Don't ship the first idea. Add an explicit review step (a separate judge agent, a rubric, or both) that scores every candidate and gates what goes on to Goal 4. Things to think about:
- What are the criteria?
- Does the judge send weak proposals back for revision? How many rounds?
- How do you know the judge is good?
### 4. Show the recommendation
For each opportunity that survives the judge, generate visual product flows and package them into a short slide flow: existing state → proposed mechanic → rewarded interaction → user receives value. Use mocked screens, arrows, annotations, and concise copy; the ad itself can be simulated. A reader should see where the flow starts, what changed in the product, what triggers the offer, what the user sees and chooses, where the ad plays, what they get, and why the system thinks it's a good idea.
The standard: could we put this in front of the app's product team and have them understand it immediately?
### 5. Productionization sketch
Sketch how this runs as a service across hundreds of apps:
- How are product models and mocks are stored and versioned?
- What triggers a re-explore when an app ships an update?
- Where do humans review?
- Cost per app?
- How does the output plug into Simula's sales and integration process?

## Deliverables
- Code: the explorer, mock generator, QA loop, proposer, and judge, runnable with a single command per stage. List required models, keys, simulators or emulators, and services.
- Product model: the Goal 1 output for each app: what was explored, what was learned, how it's represented.
- Mock + QA evidence: the Goal 2 recreation and the diffs and corrections from the QA loop.
- Rewarded flows: the slide flows from Goal 4, plus the judge's scores and reasoning for every candidate, including the rejected ones.
- Trajectory: logs or traces showing what ran autonomously, where it failed, what you fixed by hand, and how key decisions were made.
- A brief recording (10–15 minutes, screen recording / Loom is fine): walk through explore → understand → mock → QA → propose → judge → flows, the architecture choices, trade-offs, limitations, and what you'd build next. We honestly care about this the most.
You can use any libraries or AI tools, as long as you understand what you submit and can explain and modify it… and we can run it locally.
> 
  Ready to submit your deliverable? Use the link below to submit and email Yizhen that you've completed the assignment.
  Submit your Deliverable here → <https://app.notion.com/p/3e1af70f6f0d8025a82fed7bf06b84ce>

## What we're looking for
- Curiosity and initiative: Did you go beyond the obvious placements?
- Judgment: How did you allocate your time? Were you 80/20? Deep on one app beats shallow on four.
- Technical maturity: Can you build a system, not a set of demos? Explorer, mock, QA, and judge have to work together end to end, and we should be able to run it.
- Clarity: Are your write-up, flows, and recording easy to follow?
Extra complexity is not rewarded. We want the beginnings of a system that could credibly become any app → understand it → mock it → redesign it.

