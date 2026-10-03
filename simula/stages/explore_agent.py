"""Goal 1 with an agent: an LLM plans each screen's next steps, Jev grounds the steps it names in words, and code guards
every action, records what happened and stops the run. Observing, recording, launching, the content filter's check
and the core loop are the scripted explorer's (Explorer), so explore/ keeps its contracts."""

import contextlib
import re

from PIL import Image

from simula import config, decide, llm
from simula.contracts import AdLine, AgentStep, AgentTurn
from simula.device import guard
from simula.device import observe as ob
from simula.stages.explore import (AWAY, CORE_SECONDS_PER_REP, DEVICE_LOST, FILTER_MENU_QUESTION, FILTER_QUESTION,
                                   FILTER_STATE_QUESTION, FILTER_SURE, NO_FILTER, PROMPTS, SEARCH_QUERIES, CoreAction,
                                   Explorer, Move, NeedRelaunch, Seen, Stop, controls_of, filter_holds, filter_label,
                                   png_half, put_back, title_of, where)

AGENT_MINUTES = 60  # one wall clock for everything the agent does: the tour, the core loop and the replay check
STALE_TURNS = 10  # planner turns in a row that found no new state end the run
RELAUNCHES = 10  # a guard against a launch loop, not a budget: the agent's stops are $, time, stale turns and done
HISTORY_LINES = 60  # ponytail: the latest steps only; a summary of older ones if long runs lose their way
ONE_STEP = "\n\nPlan exactly one step, and name its element by id: never by intent."
GROUND = "Which control does this: {}?"
AD_NOTE = re.compile(r"^\s*(o\d+)\s*:\s*(.*)$", re.MULTILINE)
AD_FORMATS = ("banner", "interstitial", "rewarded", "native")
SEARCH = re.compile(r"\bsearch\b", re.IGNORECASE)
URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
CHOOSER_STEPS = {"continue", "next", "allow", "agree", "i agree", "ok", "cancel"}  # the account chooser's flow buttons
FILTER_PATH = 4  # ponytail: the filter's last taps kept as its controls (opener, option, two hops to them)


class Halt(Stop):
    """The $ cap or the wall clock: no device action after it, in any phase."""


class ScreenMoved(NeedRelaunch):
    """The screen changed under an action (the Play Store came to the front, or the target is gone): it didn't run."""


def hard_block(c: ob.Candidate, screen: list[dict] | None, core: bool = False) -> str | None:
    """The guard's word against tapping c on the screen whose element list is given (its tap point, a dialog)."""
    r = c.rect
    element = {"text": c.tree_label, "label": c.label, "identifier": c.ident,
               "coordinates": {"x": r.x, "y": r.y, "width": r.w, "height": r.h}}
    return guard.blocked_tap(element, screen, core=core)


def chooser_step(said: str) -> bool:
    """On Google's account chooser only an account row (it shows an email, redacted or not) or a flow button."""
    return ob.REDACTED in said or bool(ob.EMAIL.search(said)) or said.strip().lower() in CHOOSER_STEPS


class AgentExplorer(Explorer):
    """The scripted explorer with the agent's policy: tour() is the planner's loop, the hard blocks replace the
    deny-list, and the content filter is the planner's goal, verified by code but never a gate."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dial = self.ctx.explore_jev
        self.limits = {**self.limits, "actions": float("inf")}
        self.tour_seconds = AGENT_MINUTES * 60 - self.core_reps * CORE_SECONDS_PER_REP
        self.deadline = self.started + AGENT_MINUTES * 60
        self.capped = False
        self.redacting = re.compile("|".join([ob.EMAIL.pattern, *(re.escape(x) for x in self.secrets if x.strip())]),
                                    re.IGNORECASE)
        self.steps: list[AgentStep] = []
        self.ids: dict[str, ob.Candidate] = {}  # this turn's element ids
        self.names: dict[str, str] = {}  # the planner's name for each state it planned on
        self.found: list[str] = []  # the planner's notes
        self.history: list[str] = []
        self.news: list[str] = []  # what code tells the planner on its next turn
        self.tapped: list[ob.Candidate] = []  # taps while the filter isn't verified: its controls when it says set
        self.tapped_on: dict[int, tuple[list[ob.Candidate], list[dict]]] = {}  # each one's screen: controls, elements
        self.turn: AgentTurn | None = None
        self.filter_news = "not set yet"
        self.picked_core: CoreAction | None = None
        self.ads_seen: set[tuple[str, str]] = set()
        self.ad_leave = False  # leaving an ad's landing: a relaunch for it isn't counted
        self.stale = self.known = self.noops = 0

    # ---------- the loop ----------

    def tour(self) -> None:
        with self.latching():
            self.relaunch(first=True)
            while True:
                if self.clock() - self.started >= self.tour_seconds:
                    raise Stop("wall-time cap")
                try:
                    if self.obs is None:
                        self.resync()
                    if self.escape_billing():
                        self.obs = None
                    elif self.current.kind in AWAY:
                        self.leave()
                    elif not self.steps:
                        self.next_turn()
                    else:
                        self.run_step(self.steps.pop(0))
                except ScreenMoved as e:
                    self.note("agent.moved", f"{e}: nothing was done, the plan ended")
                    self.news.append(f"The screen changed before a step could run ({e}): nothing was done.")
                    self.steps.clear()
                    self.obs = None
                except NeedRelaunch as e:
                    self.note("relaunch", str(e))
                    self.relaunch(why=str(e))
                except DEVICE_LOST as e:
                    self.hang(self.current, e)

    @contextlib.contextmanager
    def latching(self):
        """A $ cap anywhere latches the stop: no later phase touches the device."""
        try:
            yield
        except llm.CapReached:
            self.capped = True
            raise

    def halted(self) -> str:
        return "$ cap" if self.capped else "wall-time cap" if self.clock() >= self.deadline else ""

    def core_loop(self) -> None:
        self.touring = False
        if why := self.halted():
            self.core_results.append(f"not run: {why}")
            return
        with self.latching():
            super().core_loop()

    def verify_replay(self) -> None:
        if why := self.halted():
            self.note("replay", f"not run: {why}")
            return
        with self.latching():
            super().verify_replay()

    def next_turn(self) -> None:
        if guard.in_billing(self.phone.foreground()):  # the screen may have moved since the last look
            self.obs = None
            return
        if len(self.states) > self.known:
            self.stale, self.known = 0, len(self.states)
        elif self.stale >= STALE_TURNS:
            raise Stop(f"no new state in {STALE_TURNS} planner turns")
        self.stale += 1
        s = self.current
        try:
            turn = self.plan(s)
        except llm.LLMFailure as e:
            self.counts["model call failures"] += 1
            self.note("agent.turn", self.scrub(f"planner call failed: {e}"), outcome="error")
            return
        self.turn, self.names[s.sid], self.found = turn, turn.screen, turn.notes
        self.counts["planner turns"] += 1
        self.record_ads(s, turn)
        if turn.filter_set and not self.filtered:
            self.filter_reported()
        if turn.filter_set:
            self.tapped = []
        self.steps = list(turn.steps if self.dial == "on" else turn.steps[:1])
        self.counts["planned steps"] += len(self.steps)
        self.note("agent.plan", f"{s.sid} {turn.screen!r}: {turn.goal}; "
                                + "; ".join(f"{p.action} {p.element or p.intent or p.direction or ''}"
                                            for p in self.steps), decider="model")

    def plan(self, s: Seen) -> AgentTurn:
        """One planner call: the raw screen (the only unredacted thing, and never written), its elements by id, and
        the run so far. What it answers is scrubbed (scrub) before anything stores it."""
        cands = self.listing(s)
        self.ids = dict(zip(decide.option_ids([c.label for c in cands]), cands, strict=True))
        text = self.situation(s)
        self.news = []
        role = config.roles(self.ctx.profile)["explore_agent"]
        system = (PROMPTS / "agent.md").read_text() + (ONE_STEP if self.dial != "on" else "")

        def attempt(pngs: list[bytes]) -> AgentTurn:
            images = [{"type": "image", "png": png} for png in pngs]
            parsed, _ = llm.call(trace_path=self.trace_path, stage="explore", step="agent.turn", model=role["model"],
                                 effort=role.get("effort"), system=system,
                                 messages=[{"role": "user", "content": [*images, {"type": "text", "text": text}]}],
                                 max_tokens=config.max_tokens(role), budget=self.budget, schema=AgentTurn,
                                 no_cache=self.ctx.no_cache, replay=self.ctx.replay, cache_dir=self.cache_dir,
                                 scrub=self.scrub)
            return parsed

        try:
            turn, _ = llm.without_refused_images([self.raw_png()], attempt)
        except llm.LLMFailure as e:
            if e.outcome != "refusal":
                raise
            self.note("agent.turn", "the screenshot was refused: planned from the element list alone")
            turn = attempt([])
        return turn

    def raw_png(self) -> bytes:
        """The screen as it is, half-size, for the planner alone: the file it passes through is deleted at once."""
        shot = self.phone.screenshot(self.scratch / "planner.png", (self.device.w_px, self.device.h_px))
        try:
            return png_half(Image.open(shot).convert("RGB"))
        finally:
            shot.unlink()

    def scrub(self, text: str) -> str:
        """The planner saw the raw screen, so whatever it wrote may hold a redacted string or an email: llm.call
        scrubs its answers and failures with this before the cache or the trace sees them."""
        return self.redacting.sub(ob.REDACTED, text)

    def listing(self, s: Seen, obs=None) -> list[ob.Candidate]:
        """What the planner may name on the live screen (or the look given): the state's recorded controls it still
        shows (with the icon pass's names and the pictures it found), then live ones the record lacks; on an overlay,
        its own."""
        obs = obs or self.obs
        live = obs.cands if s.box is None else [c for c in (ob.find(obs.cands, o) for o in controls_of(s.cands)) if c]
        return [c for c in s.cands if ob.find(live, c)] + [c for c in live if s.box is None and not ob.find(s.cands, c)]

    def situation(self, s: Seen) -> str:
        d = self.device
        lines = [f"{i}: {c.kind} {c.label[:80]!r}" + (f" id={ob.short_id(c.ident)}" if c.ident else "")
                 + f" at {where(c, d)} [{int(c.rect.x)},{int(c.rect.y)},{int(c.rect.w)},{int(c.rect.h)}]"
                 + ("" if c.checked is None else " (on)" if c.checked else " (off)")
                 + ("" if c.enabled else " (disabled)") for i, c in self.ids.items()]
        history = self.history[-HISTORY_LINES:]
        return "\n".join([
            f"App in front: {self.obs.fg} (the app explored is {self.package}). Screen {d.w_px}x{d.h_px} px.",
            f"Current screen: {s.sid} ({s.kind})" + (f", you named it {self.names[s.sid]!r}." if s.sid in self.names
                                                     else "."),
            "Elements on this screen:", *(lines or ["none"]),
            "Screens recorded so far: " + "; ".join(f"{t.sid} {self.names.get(t.sid, t.kind)!r}" for t in self.states),
            "Your notes: " + ("; ".join(self.found) or "none yet"),
            f"Steps run so far ({len(self.history)}, the latest {len(history)}):", *(history or ["none yet"]),
            "Since your last turn: " + (" ".join(self.news) or "nothing to report"),
            f"Content filter: {self.filter_news}",
            f"Core action: {self.picked_core.name if self.picked_core else 'not marked yet'}",
            "Texts you may type, into a search box only: " + ", ".join(f'"{q}"' for q in SEARCH_QUERIES),
            f"Time used: {(self.clock() - self.started) / 60:.0f} of {self.tour_seconds / 60:.0f} minutes."])

    # ---------- running a step ----------

    def run_step(self, step: AgentStep) -> None:
        if step.action == "done":
            raise Stop(f"the planner said done: {self.turn.done_reason}"[:200])
        if step.action == "launch":
            self.relaunch(why="the planner asked for a fresh launch")
            return
        if step.action == "start_core":
            self.start_core(step)
            return
        move = self.move_for(step)
        if move is None:
            self.steps.clear()
            return
        s, before, ran = self.current, self.obs, self.actions
        ad = next((i for i in self.turn.ads if move.cand is not None and self.ids.get(i) is move.cand), None)
        if ad:
            self.tap_ad(s, move, ad)
            return
        self.act(move)
        what = repr(move.cand.label[:40]) if move.cand else move.text or move.direction if move.action != "back" else ""
        if self.actions == ran:
            self.history.append(f"{move.action} {what} on {s.sid}: refused or not run")
            self.news.append(f"Your {move.action} {what} was not run: a hard block refused it, or the control wasn't "
                             f"shown as listed.")
            self.steps.clear()
            return
        if move.action == "tap" and not self.filtered:
            tapped = ob.find(s.cands, move.cand) or move.cand
            self.tapped = [*self.tapped, tapped][-FILTER_PATH:]
            self.tapped_on[id(tapped)] = (self.listing(s, before), before.elements)
        self.after(step, move, what, s, before)

    def tap_ad(self, s: Seen, move: Move, element: str) -> None:
        """An ad's landing is ad evidence, never a product state: the tap goes through the guard like any other, the
        landing is read (another app's package, else a URL or the title it shows), and the agent goes back."""
        live = ob.find(self.obs.cands, move.cand)
        refused = self.perform(move, live)
        if refused:
            self.log(s, None, move, live, "unknown", f"denied: {refused}", "denied")
            self.news.append(f"The ad {move.cand.label[:40]!r} was not tapped: {refused}.")
            self.steps.clear()
            return
        self.actions += 1
        obs = self.observe()
        said = ob.texts(obs.elements, self.device)
        landing = obs.fg if obs.fg != self.package else next((t for t in said if URL.search(t)), None) or \
            title_of(obs.elements, self.device) or None
        self.log(s, None, move, ob.find(s.cands, move.cand) or live, "unknown", f"an ad, landed on {landing}"[:160],
                 "ok")
        self.ad_line(s, element, tapped=True, landing=landing)
        self.history.append(f"tap the ad {move.cand.label[:40]!r} on {s.sid}: landed on {landing}, then back")
        self.steps.clear()
        if not self.escape_billing():
            if obs.fg != self.package:
                self.ad_leave = True
                self.launch()
            else:
                self.phone.back()
        self.obs = None

    def move_for(self, step: AgentStep) -> Move | None:
        """The move a step asks for. A tap names an element of the planning screen, or (dial on) an intent Jev
        grounds on the live one; one the live screen doesn't show ends the plan, untapped."""
        why = step.expect[:80]
        if step.action in ("swipe", "back"):
            return Move(step.action, direction=step.direction or "up", decider="model", why=why)
        if step.action == "type":
            return Move("type", text=step.text or "", decider="model", why=why)
        cand, decider = self.ids.get(step.element or ""), "model"
        if cand and self.dial == "shadow":
            self.shadow(step, cand)
        if cand is None and step.intent and self.dial == "on":
            cand, decider = self.ground(self.current, step.intent), "jev"
            self.counts["steps grounded by Jev" if cand else "steps Jev couldn't ground"] += 1
        if cand is None:
            self.news.append(f"A tap named nothing on the screen ({step.element or step.intent!r}).")
            return None
        if ob.find(self.obs.cands, cand) is None:
            self.note("agent.skip", f"{cand.label[:40]!r} is not on the live screen ({self.current.sid}): plan ended")
            self.news.append(f"{cand.label[:40]!r} was gone from the screen when its turn came: nothing was tapped.")
            return None
        return Move("tap", cand, decider=decider, why=why)

    def ground(self, s: Seen, intent: str) -> ob.Candidate | None:
        """Jev picks the live control an intent names, or None when it fails or is unsure."""
        cands = self.listing(s)
        if not cands:
            return None
        try:
            order, confidence = decide.rank(self.trace_path, "explore", f"ground.{s.sid}", self.describe(s),
                                            GROUND.format(intent), [self.option_label(c) for c in cands],
                                            **self.jev_options())
        except decide.JevFailed:
            return None
        return cands[order[0]] if confidence >= FILTER_SURE else None

    def shadow(self, step: AgentStep, cand: ob.Candidate) -> None:
        """Dial shadow: would Jev, given the step's expectation, have picked the planner's element?"""
        pick = self.ground(self.current, step.expect)
        agree = pick is not None and pick.key == cand.key
        self.counts[f"jev shadow {'agree' if agree else 'disagree'}"] += 1
        self.note("jev.shadow", f"{'agree' if agree else 'disagree'}: planner {cand.label[:30]!r}, Jev "
                                f"{pick.label[:30] if pick else 'unsure'!r}", decider="jev")

    def after(self, step: AgentStep, move: Move, what: str, s: Seen, before) -> None:
        """The plan goes on while each step does what it expected: the screen changed, or the expected text shows.
        It ends on another app, a screen the planner hasn't named, or two steps in a row that changed nothing."""
        to = self.current
        said = ob.texts(self.obs.elements, self.device)
        changed = to is not s or said != ob.texts(before.elements, self.device)
        self.noops = 0 if changed else self.noops + 1
        self.history.append(f"{move.action} {what} on {s.sid} -> {to.sid}: {'changed' if changed else 'no change'}")
        shown = step.expect.lower() in " ".join(said).lower()
        why = ("another app is in front" if to.kind in AWAY
               else "a screen you haven't named" if to.sid not in self.names
               else "two steps in a row changed nothing" if self.noops >= 2
               else "" if changed or shown else f"{step.expect!r} didn't happen")
        if why and self.steps:
            self.news.append(f"Your plan stopped after {move.action} {what}: {why}.")
        if why:
            self.steps.clear()

    def start_core(self, step: AgentStep) -> None:
        """Marks the core action the inherited core loop measures after the tour, only for an AI recipient."""
        if not guard.core_allowed(step.recipient):
            self.note("agent.core", f"start_core refused: the recipient is {step.recipient!r}", outcome="blocked")
            self.news.append("start_core was refused: code sends messages only to the app's AI or bot.")
            return
        s, chat = self.current, self.live_composer()
        if chat:
            self.picked_core = CoreAction("chat", s, [ob.find(s.cands, c) or c for c in chat],
                                          f"send messages in a conversation and read the replies "
                                          f"({self.chat_title(s)!r}; text box + send on {s.sid})")
        else:
            cand = self.ids.get(step.element or "") or (self.ground(s, step.intent) if step.intent and self.dial == "on"
                                                        else None)
            if cand is None:
                self.news.append("start_core named no element on this screen.")
                return
            self.picked_core = CoreAction("action", s, [ob.find(s.cands, cand) or cand],
                                          f"tap {cand.label[:40]!r} again and again on {s.sid}")
        self.note("agent.core", f"marked: {self.picked_core.name}", decider="model")
        self.news.append(f"Core action marked ({self.picked_core.name}): code measures it after you are done.")

    def choose_core(self) -> list[CoreAction]:
        return [self.picked_core] if self.picked_core else []

    def paywall_pass(self) -> None:
        """The planner opens the paywall itself; this only names the priced screen it found, for the exhibit."""
        found = self.priced_paywall()
        self.paywall = found.sid if found else None

    # ---------- ads ----------

    def record_ads(self, s: Seen, turn: AgentTurn) -> None:
        for element in turn.ads:
            c = self.ids.get(element)
            if c is not None and (s.sid, c.key) not in self.ads_seen:
                self.ads_seen.add((s.sid, c.key))
                self.ad_line(s, element)

    def ad_line(self, s: Seen, element: str, tapped: bool = False, landing: str | None = None) -> None:
        """One line of explore/ads.jsonl, written live: a tapped ad's line names where it landed."""
        said = dict(AD_NOTE.findall(self.turn.ad_notes)).get(element, "")
        kind, advertiser, category = ([p.strip() for p in said.split(";")] + ["", "", ""])[:3]
        line = AdLine(step=self.step, state=s.sid, format=kind.lower() if kind.lower() in AD_FORMATS else "other",
                      advertiser=advertiser or None, category=category or None, tapped=tapped, landing=landing)
        with open(self.out / "ads.jsonl", "a") as f:
            f.write(line.model_dump_json() + "\n")

    # ---------- the hard blocks, instead of the deny-list ----------

    def denied_at(self, s: Seen, cand: ob.Candidate, **rules) -> str | None:
        """The hard blocks on the last look, before act() logs the move; tap() asks again on a fresh one."""
        return self.refusal(cand, self.obs.fg, self.obs.elements, rules.get("core", False)) or None

    def refusal(self, c: ob.Candidate, fg: str, elements: list[dict], core: bool) -> str:
        """Why the guard refuses a tap on c on this screen, or "": a hard-block word, or on Google's account chooser
        anything but an account row or a flow button."""
        if guard.signing_in(fg) and not chooser_step(f"{c.tree_label}\n{c.label}"):
            reason = "not an account row or a sign-in step on Google's account chooser"
        else:
            word = hard_block(c, elements, core=core)
            reason = f"hard block: {word}" if word else ""
        self.counts["hard blocks refused"] += bool(reason)
        return reason

    def boundary(self) -> str:
        """Right before every tap, type and swipe: nothing after the $ cap or the wall clock (Halt), and the foreground,
        read fresh, must be the app or Google's account chooser; otherwise the action doesn't run (ScreenMoved) and the
        away handling takes over. The Play Store gets BACK first. The foreground."""
        if why := self.halted():
            raise Halt(why)
        fg = self.phone.foreground()
        if guard.in_billing(fg):
            self.back_out(fg)
            raise ScreenMoved("the Play Store came to the front")
        if fg != self.package and not guard.signing_in(fg):
            raise ScreenMoved(f"{fg} came to the front")
        return fg

    def fresh(self) -> list[dict]:
        """The element list as the screen is now, redacted like every look (no image: nothing is painted or kept)."""
        reply, _ = self.phone.elements()
        return ob.redact(reply, Image.new("RGB", (1, 1)), self.secrets, self.parts)[1]

    def tap(self, live: ob.Candidate, elements: list[dict], **deny) -> str:
        """Every tap reaches the device here (the planner's, the core loop's with core, a launch's, the replay's): the
        target is found again on a fresh element list and the guard reads that list. No content-filter gate: the filter
        is the planner's goal."""
        fg = self.boundary()
        elements = self.fresh()
        target = ob.find(ob.controls(elements, self.device), live)
        if target is None:
            raise ScreenMoved(f"{live.label[:40]!r} is gone from the screen")
        reason = self.refusal(target, fg, elements, deny.get("core", False))
        if not reason:
            self.phone.tap(*target.point)
        return reason

    def safe_tap(self, c: ob.Candidate, why: str) -> bool:
        """The replay's taps: only while the app is in front, and through tap()'s hard blocks, not the deny-list."""
        reason = f"{self.obs.fg} is in front" if self.obs.fg != self.package else self.perform(Move("tap", c), c)
        if reason:
            self.note(why, f"{c.label[:30]!r} not tapped: {reason}", outcome="blocked")
            return False
        return True

    def perform(self, move: Move, live: ob.Candidate | None, upsell: bool = False, core: bool = False,
                toggle_ok: bool = False, account: bool = False) -> str:
        """The one place a move reaches the device. BACK always runs (it is how the agent escapes); everything else
        passes the boundary first. Typing is a core-loop message in the composer or a search query in a focused search
        box, read on a fresh list; anything else is refused unwritten. The scripted deny-list isn't consulted."""
        if move.action == "back":
            self.phone.back()
            return ""
        if move.action == "tap":
            return self.tap(live, self.obs.elements, core=core)
        self.boundary()
        if move.action == "swipe":
            self.phone.swipe(move.direction)
        elif guard.allowed_text(move.text, core=core, field=self.field(core, self.fresh())):
            self.phone.type_text(move.text)
        else:
            move.text = ""  # the refused text never reaches the record
            self.counts["hard blocks refused"] += 1
            return "hard block: not an allowed text"
        return ""

    def launch(self) -> None:
        if why := self.halted():
            raise Halt(why)
        fg = self.phone.foreground()
        if guard.in_billing(fg):
            self.back_out(fg)
        super().launch()

    def unrun(self, s: Seen, move: Move, expect: Seen | None) -> Seen:
        """A refused type has no control to mark tried."""
        return s if move.cand is None and expect is None else super().unrun(s, move, expect)

    def field(self, core: bool, elements: list[dict]) -> str:
        """What a type goes into, on this list: the core loop's composer (the chat's text box it just tapped), a search
        box (the focused box says search), or anything else."""
        if core:
            cands = ob.controls(elements, self.device)
            return "composer" if self.lower_box(cands) and not ob.dialog_box(cands, self.device) else "other"
        box = next((e for e in elements if e.get("focused") and e["type"].endswith("EditText")), None)
        said = " ".join(str(box.get(k) or "") for k in ("text", "label", "identifier")) if box else ""
        return "search" if SEARCH.search(ob.ID_WORDS.sub(" ", said)) else "other"

    def log_denied(self, s: Seen, cands: list[ob.Candidate] | None = None) -> None:
        for c in s.cands if cands is None else cands:
            word = hard_block(c, s.elements)
            if word and c.key not in self.tab_keys():
                why = f"denied: hard block: {word}"
                self.log(s, None, Move("tap", c, why=why), c, "unknown", why, "denied")

    def escape_billing(self) -> bool:
        """The Play Store in the last look gets BACK before the planner sees it; the rest of the plan is dropped."""
        if not (self.obs and guard.in_billing(self.obs.fg)):
            return False
        self.back_out(self.obs.fg)
        return True

    def back_out(self, fg: str) -> None:
        self.note("billing", f"the store's billing screen opened ({fg}); pressed BACK at once", outcome="blocked")
        self.phone.back()
        self.counts["billing screens escaped"] += 1
        self.steps.clear()
        self.news.append("A purchase screen opened and code pressed back at once.")

    def away(self, obs) -> str | None:
        """Google's account chooser is part of signing in, not another app; any other Google screen is away."""
        if guard.signing_in(obs.fg):
            chooser = any(chooser_step(e.get(k) or "") for e in obs.elements for k in ("text", "label"))
            return None if chooser else "external"
        return super().away(obs)

    def account_wall(self, s: Seen) -> bool:
        """Signing in is the planner's (Google only, no typing): code never fills a sign-up form for the agent."""
        return False

    # ---------- launching ----------

    def leave(self) -> None:
        """As the scripted explorer leaves another app, set up: a return never relaunches for the filter, which the
        planner is told to set again when it isn't verified. A Google screen gets BACK first, toward the sign-in."""
        self.steps.clear()
        if self.obs is not None and guard.signing_in(self.obs.fg):
            self.act(Move("back", why="leave a Google screen that isn't the account chooser"), purpose="nav")
            if self.current.kind not in AWAY:
                return
        try:
            with self.setting_up():
                super().leave()
        finally:
            self.ad_leave = False

    def count_relaunch(self, why: str) -> None:
        if self.relaunches >= RELAUNCHES:
            self.human(f"the explorer needed relaunch {RELAUNCHES + 1}", f"{RELAUNCHES} relaunches used; the next was "
                                                                        f"for: {why}")
            raise Stop("relaunch cap")
        self.steps.clear()
        self.relaunch_reasons.append(why + (" (an ad's landing, not counted)" if self.ad_leave else ""))
        self.relaunches += not self.ad_leave
        if self.current:
            self.log(self.current, self.root, Move("relaunch", why=why), None, "unknown", "", "ok")

    # ---------- the content filter: the planner's goal, verified by code ----------

    def apply_filter(self, first: bool) -> None:
        """After a launch, the filter's recorded controls the screen shows are put back, then rechecked. The first
        launch has none: the planner finds the filter."""
        if first or not self.filter_taps:
            return

        def again(c: ob.Candidate) -> None:
            if ob.find(self.obs.cands, c):
                self.act(Move("tap", c, why="re-apply the content filter"), purpose="filter")
        put_back(self.filter_taps, self.filter_on, lambda: self.obs, again)
        self.recheck()

    def refilter(self) -> None:
        if self.filter_taps and not self.filtered and self.obs is not None:
            self.recheck()

    def filter_reported(self) -> None:
        """The planner says the filter is set: its taps since the filter goal began (while it wasn't verified) are the
        filter's controls, or, with none (it was set already), the element it names. Code judges them with the
        scripted explorer's own questions (recognized), then checks the screen."""
        named = self.ids.get(self.turn.filter_element or "")
        taps = self.tapped or ([ob.find(self.current.cands, named) or named] if named else [])
        if not taps:
            if self.filter_taps:
                self.recheck()
            else:
                self.filter_news = ("you said it is set, but none of your taps set it and filter_element names nothing "
                                    "on this screen: name its control")
            return
        on, why = self.recognized(taps)
        if why:
            self.note("filter.recognize", why, outcome="error")
            self.filtered = False
            self.filter_news = f"not verified: {why}"
            return
        self.filter_taps, self.filter_on, self.opener_says = taps, on, ""
        self.note("filter", f"content filter: {filter_label(taps, on)}", decider="jev")
        self.recheck()

    def recognized(self, taps: list[ob.Candidate]) -> tuple[bool | None, str]:
        """Whether taps set the strictest content filter, by the scripted explorer's recognizer: Jev's filter question
        on this screen picks their last control (or the opener showing the option); a later control is the strictest
        option of the screen it was tapped on; a switch's restrictive state is Jev's answer, never the state it shows.
        The switch's wanted state (None for a tap), and "" or why not."""
        s, last, opener = self.current, taps[-1], taps[0]
        opts = self.filter_options(self.listing(s), self.obs.elements)
        pick = self.pick(s, opts, FILTER_QUESTION, "filter", none_label=NO_FILTER) if opts else None
        if pick is None or not (ob.find([pick], last) or ob.overlaps(pick.rect, opener.rect)):
            return None, f"{last.label[:40]!r} isn't the content filter as code reads this screen"
        if len(taps) > 1:
            options = self.filter_options(*self.tapped_on.get(id(last), ([], [])))
            option = self.pick(s, options, FILTER_MENU_QUESTION, "filter.menu") if options else None
            if option is None or option.key != last.key:
                return None, f"{last.label[:40]!r} isn't the strictest option of the screen it was tapped on"
        live = ob.find(self.obs.cands, last)
        if (live or last).checked is None:
            return None, ""
        name = last.label[:60]
        try:
            result = decide.choose(self.trace_path, "explore", "filter.state", self.describe(s), FILTER_STATE_QUESTION,
                                   [f"{name} on", f"{name} off"], **self.jev_options())
        except decide.JevFailed:
            return None, f"no answer on which state of {name!r} is the strictest"
        return decide.index_of(result.option_id) == 0, ""

    def filter_options(self, cands: list[ob.Candidate], elements: list[dict]) -> list[ob.Candidate]:
        """The controls a filter question offers Jev: labeled ones, never a tab or the screen's title."""
        title = title_of(elements, self.device)
        return [c for c in cands if c.tree_label and c.key not in self.tab_keys() and c.tree_label[:60] != title]

    def recheck(self) -> None:
        """The filter is checked only where its control shows: elsewhere the planner is told to go back to it."""
        if any(ob.find(self.obs.cands, c) for c in (self.filter_taps[0], self.filter_taps[-1])):
            self.check_filter()
        else:
            self.filtered = False
            self.filter_news = ("not verified since the app was launched again: go back to where you set it and "
                                "say it is set on the screen that shows it")

    def check_filter(self) -> None:
        """The scripted explorer's check, with its evidence and trace line, but a failure doesn't stop the run: the
        planner is told and sets it again."""
        ok, self.opener_says = filter_holds(self.filter_taps, self.filter_on, self.obs.cands, self.obs.image,
                                            self.opener_says)
        n, last = len(self.filter_checks) + 1, self.filter_taps[-1]
        evidence = self.out / "filter" / f"check-{n:02d}.png"
        evidence.parent.mkdir(exist_ok=True)
        self.obs.image.save(evidence)
        self.filter_checks.append((n, ok, f"explore/filter/{evidence.name}"))
        self.note("filter.check", f"{last.label!r} {'verified' if ok else 'NOT verified'} by screenshot "
                                  f"(check {n})", outcome="ok" if ok else "error")
        self.filtered = ok
        self.filter_news = (f"verified (check {n}): {filter_label(self.filter_taps, self.filter_on)}" if ok else
                            f"not verified (check {n}): {last.label[:40]!r} doesn't show it set here; set it again")
