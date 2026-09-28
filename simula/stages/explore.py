"""Goal 1: drive the app through mobile-mcp and record every distinct state, the tap graph with transitions,
and full-res screenshots. Then use the app: repeat its core action and measure the free experience.

The only agent in the pipeline: code decides what is possible, Jev what is likely, Sonnet what is hard, and
code checks every answer."""

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from simula import config, decide, llm
from simula.contracts import (ActionLine, Coverage, Device, ExploreFile, HardScreenAction, IconLabel, IconPass, Point,
                              Rect, StateFile, VisionElement)
from simula.device import observe as ob
from simula.device.mcp import McpReplyError, McpTimeout, Phone, Server
from simula.doctor import adb, emulator_lock, resolve_serial
from simula.runlog import needs_human, now, run_trace, update_manifest, write_exhibit
from simula.stages import Ctx

PROMPTS = config.ROOT / "prompts" / "explore"
BILLING = {"com.android.vending"}
TAPS_PER_STATE = 6
SWIPES_PER_STATE = 2
UPSELL_SWIPES = 8
MAX_RELAUNCHES = 3
CORE_RELAUNCHES = 2
STALE_ACTIONS = 10
NOOPS_BEFORE_SONNET = 3
UNSURE = 0.35
FEW_LABELED = 4
FILTER_SURE = 0.5
LOCK_WAIT_S = 1800
ICON_SCALE = 0.5
VISION_BOX_DP = 48
CORE_REPS = {"deep": 8, "transfer": 3}  # ponytail: belongs in profiles.toml budgets; listed under shared-file needs
CORE_SECONDS_PER_REP = 75
REPLY_WAIT_S = 45
LOAD_WAIT_S = 20
QUIET_S = 2.0
REPLAY_MINUTES = 8
SPLASH_WAIT_S = 90
LAUNCH_WAIT_S = 30
LAUNCH_QUIET_S = 3
WALK_STEPS = 3
COMPOSER_BAND_PX = 150

RANK_QUESTION = "Which tap most likely reveals a limit, a paywall, a currency, an ad, or a new core screen of the app?"
FILTER_QUESTION = ("A content or safety filter decides how much adult or unsafe content the app shows (for "
                   "example 'SFW only', 'Safe mode', 'Hide NSFW', 'Family mode'). Which control sets or opens such a "
                   "filter? If the filter's options are visible, pick the most restrictive one. Pick the last option "
                   "if no control here is a content or safety filter.")
FILTER_MENU_QUESTION = "Which option is the most restrictive content setting (shows the least adult or unsafe content)?"
CORE_QUESTION = "What is the one thing a user comes to this app to do?"
NO_FILTER = "none of these is a content or safety filter"
NO_CORE = "none of these is what users come to the app to do"
CORE_MESSAGES = [
    "Hi! What can you help me with?",
    "Tell me a short story about a lighthouse.",
    "What's the best phone under $300?",
    "Give me three tips for staying focused while studying.",
    "What's a good name for a friendly golden retriever?",
    "Where can I buy comfortable running shoes on sale?",
    "Can you recommend a board game for four friends?",
    "Thanks! Can you sum up our chat in one sentence?",
]


class Stop(Exception):
    """Ends the tour; the message is the stop reason explore.json records."""


class NeedRelaunch(Exception):
    pass


class ExploreFailed(Exception):
    """A device error ended the tour, or nothing was recorded: the CLI writes failure.json, never done.json, so
    `simula run` can't reuse it. A blocked root seen after the full splash wait is a result, not a failure."""


DEVICE_ERRORS = (McpReplyError, McpTimeout, NeedRelaunch)
DEVICE_STOPS = ("device error", "second hang")


@dataclass
class Obs:
    reply: dict
    elements: list[dict]
    image: Image.Image
    fg: str
    fp: ob.Fingerprint
    cands: list[ob.Candidate]
    settled: bool
    settle_s: float


@dataclass
class Move:
    action: str
    cand: ob.Candidate | None = None
    direction: str = "up"
    text: str = ""
    decider: str = "code"
    why: str = ""


@dataclass
class Seen:
    """One recorded state. Its canonical capture is the first time it was seen."""
    sid: str
    kind: str
    parent: str | None
    fp: ob.Fingerprint
    fg: str
    cands: list[ob.Candidate]
    elements: list[dict]
    depth: int
    back_to: str | None
    settled: bool
    settle_s: float
    captured_at: str
    upsell: bool
    priced: bool = False
    via: str = ""
    box: Rect | None = None
    unscroll_to: str | None = None
    launch: bool = False
    tried: set = field(default_factory=set)
    waiting: dict = field(default_factory=dict)
    noop_run: int = 0
    taps: int = 0
    swipes: int = 0
    visits: int = 1
    order: list | None = None
    confidence: float = 1.0
    asked_sonnet: bool = False
    done: bool = False
    dynamic: list = field(default_factory=list)
    icon_labels: list = field(default_factory=list)
    vision: list = field(default_factory=list)
    blocked_reason: str | None = None


@dataclass
class CoreAction:
    kind: str
    state: Seen
    controls: list[ob.Candidate]
    name: str


def where(c: ob.Candidate, device: Device) -> str:
    x, y = c.point
    row = ("top", "middle", "bottom")[min(2, int(3 * (y - device.content_top_px)
                                              / (device.content_bottom_px - device.content_top_px)))]
    return f"{row} {('left', 'center', 'right')[min(2, int(3 * x / device.w_px))]}"


class Explorer:
    def __init__(self, ctx: Ctx, phone, out: Path, clock=time.monotonic, sleep=time.sleep, goal: str | None = None):
        """goal: an optional sentence that replaces the ranker's question, for a later goal-directed pass."""
        self.ctx, self.phone, self.out, self.goal, self.run_dir = ctx, phone, out, goal, ctx.run_dir
        self.serial = ctx.device or phone.device
        self.clock, self.sleep = clock, sleep
        self.package = ctx.app["package"]
        self.trace_path = ctx.run_dir / "trace.jsonl"
        self.budget = llm.Budget.for_stage("explore", self.trace_path, ctx.usd_cap)
        self.cache_dir = llm.CACHE
        self.limits = config.budget(ctx.budget)
        self.may_send = not ctx.no_send
        self.core_reps = CORE_REPS.get(ctx.budget, 3) if self.may_send else 0
        self.tour_seconds = self.limits["wall_minutes"] * 60 - self.core_reps * CORE_SECONDS_PER_REP
        self.device = Device()
        self.states: list[Seen] = []
        self.by_id: dict[str, Seen] = {}
        self.edges: dict[tuple[str, str], Move] = {}
        self.root: Seen | None = None
        self.launch_root: Seen | None = None
        self.current: Seen | None = None
        self.obs: Obs | None = None
        self.tabs: list[ob.Candidate] = []
        self.tab_to: dict[str, str] = {}
        self.filter_taps: list[ob.Candidate] = []
        self.filter_checks: list[tuple[int, bool, str]] = []
        self.segments: list[list[tuple[Move, str, str]]] = []
        self.touring = True
        self.step = self.actions = self.relaunches = self.last_new_at = 0
        self.hangs: Counter = Counter()
        self.anr_waited = False
        self.stop_reason = ""
        self.core: CoreAction | None = None
        self.core_results: list[str] = []
        self.core_hit = ""
        self.paywall: str | None = None
        self.relaunch_reasons: list[str] = []
        self.last_summary = ""
        self.stop_kind = self.stop_evidence = ""
        self.tour_actions = 0
        self.replay = (0, 0)
        self.started = clock()
        self.secrets = redact_list()
        self.redacted = 0
        self.denied_executed = 0
        (out / "states").mkdir(parents=True, exist_ok=True)
        self.scratch = out / ".scratch"
        self.scratch.mkdir(exist_ok=True)

    # ---------- observing ----------

    def observe(self) -> Obs:
        self.obs = None
        settled = ob.settle(self.phone.elements, self.phone.small_hash, self.device, self.clock, self.sleep)
        if any(ob.ANR.search(ob.words(e)) for e in settled.elements):
            return self.answer_anr(settled.elements)
        self.anr_waited = False
        shot = self.phone.screenshot(self.scratch / "now.png", (self.device.w_px, self.device.h_px))
        image = Image.open(shot).convert("RGB")
        reply, elements, hits = ob.redact(settled.reply, image, self.secrets)
        self.redacted += hits
        image.save(shot)
        fg = self.phone.foreground()
        if not settled.ok:
            self.note("settle", f"unsettled after {settled.seconds:.1f}s", outcome="timeout")
        self.obs = Obs(reply, elements, image, fg, ob.fingerprint(fg, elements, image, self.device),
                       ob.controls(elements, self.device), settled.ok, round(settled.seconds, 2))
        return self.obs

    def answer_anr(self, elements: list[dict]) -> Obs:
        wait = next((c for c in ob.controls(elements, self.device) if c.label.strip().lower() == "wait"), None)
        if wait and not self.anr_waited:
            self.anr_waited = True
            self.note("anr", "app not responding: tapped Wait once")
            self.phone.tap(*wait.point)
            return self.observe()
        raise NeedRelaunch("the app is not responding")

    def resync(self) -> None:
        """After a failed capture: a fresh look at the screen (a relaunch if the device still can't give one), so
        no move is ever aimed from an old screen."""
        try:
            self.current = self.record(self.observe(), None, None, [])
        except (McpReplyError, McpTimeout) as e:
            self.relaunch(why=f"no fresh look after a failed capture: {type(e).__name__}")

    def measure_device(self) -> None:
        """Screen size, density, and insets, read with the app closed: an animating feed can keep uiautomator
        from ever getting an idle dump. The defaults stand if the device still won't answer."""
        try:
            self.phone.terminate()
            w, h = self.phone.screen_size()
            _, elements = self.phone.elements()
        except DEVICE_ERRORS as e:
            self.note("device", f"kept the default geometry: {type(e).__name__}: {e}"[:200], outcome="error")
            return
        density = adb_value(self.serial, ["wm", "density"], "density:")
        self.device = ob.device_from(elements, w, h, int(density) if density and density.isdigit() else 420)

    # ---------- recording ----------

    def record(self, obs: Obs, came_from: Seen | None, move: Move | None, before: list[ob.Candidate]) -> Seen:
        for known in self.states:
            if ob.same_state(known.fp, obs.fp):
                if known is not came_from:
                    self.revisit(known, obs)
                return known
        kind, box = self.kind_of(obs, before)
        sid = f"s{len(self.states) + 1:02d}"
        shutil.copyfile(self.scratch / "now.png", self.out / "states" / f"{sid}.png")
        (self.out / "states" / f"{sid}.elements.json").write_text(json.dumps(obs.reply, indent=1, ensure_ascii=False))
        cands = [c for c in obs.cands if box is None or ob.inside(c.rect, box)]
        tab_move = move is not None and move.cand is not None and move.cand.key in self.tab_keys()
        seen = Seen(sid=sid, kind=kind, parent=came_from.sid if came_from and kind != "screen" else None,
                    fp=obs.fp, fg=obs.fg, cands=cands, elements=obs.elements,
                    depth=1 if tab_move else (came_from.depth + 1 if came_from else 0),
                    back_to=came_from.sid if came_from and not tab_move and move.action != "swipe" else None,
                    settled=obs.settled,
                    settle_s=obs.settle_s,
                    captured_at=now(), upsell=ob.is_upsell(obs.elements, self.device),
                    priced=ob.priced(obs.elements, self.device),
                    via=move.cand.label if move and move.cand else "", box=box,
                    unscroll_to=came_from.sid if came_from and move.action == "swipe" else None)
        self.states.append(seen)
        self.by_id[sid] = seen
        self.last_new_at = self.actions
        self.note("state", f"new {kind} {sid}" + (f" over {seen.parent}" if seen.parent else "")
                  + (" (upsell)" if seen.upsell else ""))
        if kind in ("screen", "modal", "sheet"):
            self.name_icons(seen)
            self.log_denied(seen)
        return seen

    def kind_of(self, obs: Obs, before: list[ob.Candidate]) -> tuple[str, Rect | None]:
        if obs.fg != self.package:
            return "external", None
        box = ob.dialog_box(obs.cands, self.device) or (
            ob.overlay_box(before, obs.cands, self.device, frozenset(self.tab_keys())) if before else None)
        return (ob.box_kind(box, self.device), box) if box else ("screen", None)

    def revisit(self, s: Seen, obs: Obs) -> None:
        s.visits += 1
        boxes = ob.changed_boxes(Image.open(self.out / "states" / f"{s.sid}.png"), obs.image, self.device)
        for b in boxes or []:
            if not any(ob.inside(b, d) for d in s.dynamic):
                s.dynamic.append(b)

    def log_denied(self, s: Seen) -> None:
        for c in s.cands:
            reason = ob.denied(c, upsell=s.upsell)
            if reason and c.key not in self.tab_keys():
                self.log(s, None, Move("tap", c, why=f"denied: {reason}"), c, "unknown", f"denied: {reason}", "denied")

    def log(self, s: Seen, to: Seen | None, move: Move, canonical: ob.Candidate | None, transition: str,
            summary: str, outcome: str, loop_pass: int | None = None, loop_stop: str | None = None) -> None:
        self.step += 1
        point = canonical.point if canonical else None
        fields = dict(step=self.step, from_state=s.sid, to_state=to.sid if to else None, action=move.action,
                      mcp_ref=canonical.ref if canonical else None,
                      tap_px=Point(x=point[0], y=point[1]) if move.action == "tap" and point else None,
                      transition=transition, change_summary=summary, outcome=outcome, loop_pass=loop_pass,
                      loop_stop=loop_stop)
        line = ActionLine(**fields)
        with open(self.out / "actions.jsonl", "a") as f:
            f.write(line.model_dump_json() + "\n")
        label = f" {move.cand.label[:40]!r}" if move.cand else (f" {move.text[:40]!r}" if move.text else "")
        run_trace(self.ctx.run_dir, stage="explore", step=f"act{self.step:03d}", decider=move.decider,
                  outcome=outcome, note=f"{move.action}{label} {s.sid}>{to.sid if to else '-'} "
                                        f"[{transition}] {summary[:120]} ({move.why})"[:300])

    def note(self, step: str, text: str, outcome: str = "ok", decider: str = "code") -> None:
        run_trace(self.ctx.run_dir, stage="explore", step=step, decider=decider, outcome=outcome, note=text[:300])

    # ---------- acting ----------

    def act(self, move: Move, purpose: str = "tour", watch=None, loop: int | None = None) -> Seen:
        """Runs one move from the current state, observes, records where it landed, and logs the line. On a
        core-loop pass, loop is the pass number and the line records what stopped the loop, if anything."""
        if self.obs is None:
            planned = self.current
            self.resync()
            if self.current is not planned:
                self.note("resync", f"after a failed capture the screen is {self.current.sid}, not {planned.sid}: "
                                    f"{move.action} skipped", outcome="error")
                return self.current
        s, before = self.current, self.obs
        if self.touring and purpose in ("tour", "nav") and self.actions >= self.limits["actions"]:
            raise Stop(f"action cap ({self.limits['actions']})")
        if move.cand and move.action == "tap":
            reason = ob.denied(move.cand, upsell=s.upsell, core=purpose == "core", toggle_ok=purpose == "filter")
            if reason:
                self.log(s, None, move, move.cand, "unknown", f"denied: {reason}", "denied")
                s.tried.add(move.cand.key)
                return s
        live = ob.find(before.cands, move.cand) if move.cand else None
        if move.cand and live is None:
            self.log(s, None, move, move.cand, "unknown", "control not on the live screen", "error")
            s.tried.add(move.cand.key)
            if move.cand.key in self.tab_keys():
                self.tab_to.setdefault(move.cand.key, "")
            return s
        outcome = "ok"
        try:
            self.perform(move, live, s.upsell, core=purpose == "core", toggle_ok=purpose == "filter")
        except McpTimeout:
            outcome = "timeout"
            self.hang(s)
        except McpReplyError as e:
            self.obs = None
            self.log(s, None, move, live, "unknown", f"the device refused the {move.action}: {e}"[:160], "error")
            raise
        self.actions += 1
        try:
            summary = watch() if watch else ""
            obs = self.observe()
        except (McpReplyError, McpTimeout) as e:
            self.log(s, None, move, ob.find(s.cands, move.cand) if move.cand else None, "unknown",
                     f"observing after the move failed: {type(e).__name__}", "error")
            raise
        self.escape_billing()
        # a chat pass stays on the chat while its composer shows: the growing conversation is not a new state
        chatting = purpose == "core" and self.core.kind == "chat" and self.live_composer()
        to = s if chatting else self.record(obs, s, move, before.cands)
        if to is s and not summary:
            summary = ob.change_summary(before.elements, obs.elements, self.device)
        canonical = ob.find(s.cands, move.cand) if move.cand else None
        self.stop_kind, self.stop_evidence = self.hit(s, to, before, move) if loop else ("", "")
        self.log(s, to, move, canonical or live, self.transition(s, to, move), summary, outcome, loop,
                 self.stop_kind or None)
        if purpose == "tour":
            self.after_tour_move(s, move, to, summary)
        if self.touring and purpose in ("tour", "nav") and self.segments:
            self.segments[-1].append((move, s.sid, to.sid))
        if to is not s and outcome == "ok" and move.action != "type":
            self.edges.setdefault((s.sid, to.sid), move)
        if move.cand and move.cand.key in self.tab_keys():
            self.tab_to.setdefault(move.cand.key, to.sid)
        self.current = to
        if to.fg in BILLING:
            back = Move("back", why="the store's billing screen")
            self.current = self.record(self.observe(), to, back, [])
            self.log(to, self.current, back, None, "back", "", "ok")
        return self.current

    def escape_billing(self) -> bool:
        """The store's billing screen gets BACK the moment it is seen, before anything else can run. What was
        observed is still recorded and logged as evidence."""
        if not (self.obs and self.obs.fg in BILLING):
            return False
        self.note("billing", f"the store's billing screen opened ({self.obs.fg}); pressed BACK at once",
                  outcome="blocked")
        self.phone.back()
        return True

    def safe_tap(self, c: ob.Candidate, why: str) -> bool:
        """A tap outside act() (the replay check, a quiet relaunch): the same deny-list, and only while the app
        itself is in front."""
        upsell = ob.is_upsell(self.obs.elements, self.device)
        reason = (f"{self.obs.fg} is in front" if self.obs.fg != self.package else
                  ob.denied(c, upsell=upsell, toggle_ok=c.key in {t.key for t in self.filter_taps}))
        if reason:
            self.note(why, f"{c.label[:30]!r} not tapped: {reason}", outcome="blocked")
            return False
        self.perform(Move("tap", c), c, upsell, toggle_ok=True)
        return True

    def perform(self, move: Move, live: ob.Candidate | None, upsell: bool = False, core: bool = False,
                toggle_ok: bool = False) -> None:
        """The one place a move reaches the device. A tap the deny-list flags is counted here, whoever sent it,
        so the exhibit's count is measured, not assumed."""
        if move.action == "tap":
            if ob.denied(live, upsell=upsell, core=core, toggle_ok=toggle_ok):
                self.denied_executed += 1
            self.phone.tap(*live.point)
        elif move.action == "back":
            self.phone.back()
        elif move.action == "swipe":
            self.phone.swipe(move.direction)
        elif move.action == "type":
            self.phone.type_text(move.text)

    def after_tour_move(self, s: Seen, move: Move, to: Seen, summary: str) -> None:
        if move.action == "swipe":
            s.swipes += 1
            return
        if not move.cand or move.cand.key in self.tab_keys():
            return
        s.taps += 1
        key = move.cand.key
        if to is s and not summary:
            s.noop_run += 1
            if key in s.waiting:
                s.tried.add(key)
            else:
                s.waiting[key] = s.visits
        else:
            s.noop_run = 0
            s.tried.add(key)

    def transition(self, s: Seen, to: Seen, move: Move) -> str:
        if move.action == "back":
            return "back"
        if move.cand and move.cand.key in self.tab_keys():
            return "tab"
        if s.kind in ("modal", "sheet") and (to.sid == s.parent or (s.parent is None and to.kind == "screen")):
            return "back"
        if to.kind in ("modal", "sheet") and to is not s:
            return "modal"
        if to is self.root and s is not self.root and move.action == "tap":
            return "replace"
        return "push"

    def hang(self, s: Seen) -> None:
        self.hangs[s.sid] += 1
        if self.hangs[s.sid] >= 2:
            self.human("the device hung twice on one screen", f"two mobile-mcp calls timed out on {s.sid}")
            raise Stop(f"second hang on {s.sid}")

    def human(self, what: str, why: str) -> None:
        needs_human(self.ctx.run_dir, "explore", what, why, ["trace.jsonl", "explore/actions.jsonl"],
                    f"simula explore {self.ctx.app['name']} --run {self.ctx.run_dir.name}")

    # ---------- launching ----------

    def relaunch(self, first: bool = False, why: str = "") -> None:
        """Terminate, launch, settle, record and dismiss launch dialogs, then re-apply the content filter."""
        if not first:
            self.count_relaunch(why)
        self.phone.terminate()
        self.phone.launch()
        self.wait_for_app(None if first else self.launch_root)
        self.current = None
        self.normalize()
        home = self.record(self.obs, None, None, [])
        self.current = home
        if first:
            self.root = self.launch_root = home
            home.depth, home.back_to = 0, None
            reason = self.blocked(self.obs)
            if reason:
                home.kind, home.blocked_reason = "blocked", reason
                self.human("the app can't be explored", reason)
                raise Stop(f"blocked root: {reason}")
            self.tabs = [t for t in ob.tab_bar(home.cands, self.device) if not ob.denied(t)]
        else:
            self.back_to_root()
        self.apply_filter(first)
        self.segments.append([])

    def count_relaunch(self, why: str) -> None:
        self.relaunch_reasons.append(why)
        cap = MAX_RELAUNCHES + (0 if self.touring else CORE_RELAUNCHES)
        if self.relaunches >= cap:
            self.human(f"the explorer needed relaunch {cap + 1}", f"{cap} relaunches used")
            raise Stop("relaunch cap")
        self.relaunches += 1
        if self.current:
            self.log(self.current, self.root, Move("relaunch", why=why), None, "unknown", "", "ok")

    def reopen(self, dialog: Seen) -> bool:
        """A relaunch that stops at a launch dialog instead of dismissing it, to follow its call to action."""
        self.count_relaunch(f"reopen the launch dialog {dialog.sid}")
        self.phone.terminate()
        self.phone.launch()
        self.wait_for_app(dialog)
        self.current = self.record(self.obs, None, None, [])
        return self.current is dialog

    def wait_for_app(self, expect: Seen | None = None) -> Obs:
        """Observes after a launch, waiting up to SPLASH_WAIT_S for a splash to end (a cold start on a busy
        emulator took over a minute), then up to LAUNCH_WAIT_S for the launch screen seen before. A feed can sit on
        still loading placeholders for many seconds, so a launch also waits until two looks LAUNCH_QUIET_S apart
        agree. Until the splash deadline, a dump that times out is only "not yet"; the last good look stands, since
        nothing has moved on the screen since."""
        splash_deadline = self.clock() + SPLASH_WAIT_S
        obs = self.look(splash_deadline)
        while obs is None or ((not self.launchable(obs) or obs.fg != self.package) and self.clock() < splash_deadline):
            self.sleep(1.5)
            obs = self.look(splash_deadline)
        deadline = self.clock() + LAUNCH_WAIT_S
        while obs.fg == self.package and self.clock() < deadline and (not self.launchable(obs) or (
                expect and not ob.same_state(obs.fp, expect.fp) and not ob.dialog_box(obs.cands, self.device))):
            self.sleep(1.5)
            obs = self.look(splash_deadline) or obs
        while not (expect and ob.same_state(obs.fp, expect.fp)) and obs.fg == self.package and self.clock() < deadline:
            self.sleep(LAUNCH_QUIET_S)
            again = self.look(splash_deadline)
            if again and ob.same_state(again.fp, obs.fp):
                return again
            obs = again or obs
        if self.obs is not obs:
            obs.image.save(self.scratch / "now.png")
            self.obs = obs
        return obs

    def launchable(self, obs: Obs) -> bool:
        """A launch screen shows at least two labeled controls or a tab bar; a logo alone is a splash."""
        return sum(bool(c.tree_label) for c in obs.cands) >= 2 or bool(ob.tab_bar(obs.cands, self.device))

    def look(self, deadline: float) -> Obs | None:
        """An observation during a cold start, or None while the device can't answer yet (a loaded emulator
        can take over 20 s per element dump while the app starts). Past the deadline the error stands."""
        try:
            return self.observe()
        except (McpTimeout, McpReplyError) as e:
            if self.clock() >= deadline:
                raise
            self.note("launch", f"the device didn't answer yet: {type(e).__name__}", outcome="timeout")
            return None

    def normalize(self) -> None:
        """Records any launch banner or dialog before dismissing it, so a launch paywall is never lost."""
        for _ in range(3):
            box = ob.dialog_box(self.obs.cands, self.device) if self.obs.fg == self.package else None
            if box is None:
                return
            dialog = self.record(self.obs, None, None, [])
            dialog.done, dialog.depth, dialog.launch = True, 0, True
            self.current = dialog
            close = ob.dismiss_control(dialog.cands)
            self.act(Move("tap", close, why="dismiss a launch dialog") if close else
                     Move("back", why="dismiss a launch dialog"), purpose="setup")
            if self.current is not dialog and self.current.kind == "screen":
                dialog.parent = self.current.sid

    def blocked(self, obs: Obs) -> str | None:
        if obs.fg != self.package:
            return f"the app is not in the foreground after launch ({obs.fg} is)"
        if ob.dialog_box(obs.cands, self.device):
            return "a dialog that does not dismiss covers the launch screen"
        hit = next((t for t in ob.texts(obs.elements, self.device) if ob.BLOCKING.search(t)), None)
        if hit:
            return f"a blocking screen at launch: {hit[:80]!r}"
        return None if self.launchable(obs) else "no controls on the launch screen (fewer than two labeled, no tab bar)"

    def back_to_root(self) -> None:
        """A relaunch that restored a deeper screen goes back up to 4 times to the launch screen. A screen that
        shows the bottom tabs is a top screen already, and BACK there would leave the app."""
        for _ in range(4):
            here = self.current
            if here is self.launch_root or here.kind == "external" or self.shows_tabs(here):
                return
            self.act(Move("back", why="relaunch landed off the launch screen"), purpose="setup")

    # ---------- the content filter ----------

    def apply_filter(self, first: bool) -> None:
        if first:
            self.filter_taps = self.find_filter()
            if self.filter_taps and self.current is not self.root:
                self.root.done = True
                self.root, self.current.depth, self.current.back_to = self.current, 0, None
        else:
            for n, tap in enumerate(self.filter_taps):
                if n == len(self.filter_taps) - 1 and self.stands_out(tap):
                    break
                self.act(Move("tap", tap, decider="code", why="re-apply the content filter"), purpose="filter")
        if self.filter_taps:
            self.check_filter()

    def find_filter(self) -> list[ob.Candidate]:
        home = self.current
        opts = [c for c in home.cands if c.tree_label and not ob.denied(c, toggle_ok=True)
                and c.key not in self.tab_keys()]
        if not opts:
            return []
        pick = self.pick(home, opts, FILTER_QUESTION, "filter", none_label=NO_FILTER)
        if pick is None:
            self.note("filter", "no content or safety filter on the root", decider="jev")
            return []
        taps = [pick]
        if not self.stands_out(pick):
            opened = self.act(Move("tap", pick, decider="jev", why="content filter"), purpose="filter")
            if opened is not home and opened.kind in ("modal", "sheet"):
                option = self.pick(opened, [c for c in opened.cands if not ob.denied(c, toggle_ok=True)],
                                   FILTER_MENU_QUESTION, "filter.menu")
                if option:
                    self.act(Move("tap", option, decider="jev", why="content filter option"), purpose="filter")
                    taps.append(option)
        self.note("filter", "content filter: " + " > ".join(repr(t.label) for t in taps), decider="jev")
        return taps

    def pick(self, s: Seen, opts: list[ob.Candidate], question: str, step: str,
             none_label: str | None = None) -> ob.Candidate | None:
        """One Jev Choice over these controls (plus an optional 'none'); Sonnet if Jev fails."""
        labels = [self.option_label(c) for c in opts] + ([none_label] if none_label else [])
        try:
            result = decide.choose(self.trace_path, "explore", step, self.describe(s), question, labels,
                                   **self.jev_options())
        except decide.JevFailed:
            move = self.hard_screen(s, opts, question + " Answer done if none applies.")
            return move.cand if move and move.cand else None
        index = decide.index_of(result.option_id)
        if index >= len(opts) or (none_label and result.confidence < FILTER_SURE):
            return None
        return opts[index]

    def stands_out(self, c: ob.Candidate) -> bool:
        """Selected-looking: the control's pixels differ clearly from the other controls in its row."""
        live = ob.find(self.obs.cands, c)
        if live is None:
            return False
        cy = ob.center(live.rect)[1]
        row = [o for o in self.obs.cands if o is not live and abs(ob.center(o.rect)[1] - cy) < 24
               and abs(o.rect.h - live.rect.h) < 24]
        if not row:
            return False
        others = np.median([mean_color(self.obs.image, o.rect) for o in row], axis=0)
        return float(np.abs(mean_color(self.obs.image, live.rect) - others).sum()) > 40

    def check_filter(self) -> None:
        last = self.filter_taps[-1]
        visible = ob.find(self.obs.cands, last) is not None
        ok = self.stands_out(last) if visible else last.tree_label in ob.texts(self.obs.elements, self.device)
        n = len(self.filter_checks) + 1
        evidence = self.out / "filter" / f"check-{n:02d}.png"
        evidence.parent.mkdir(exist_ok=True)
        shutil.copyfile(self.scratch / "now.png", evidence)
        self.filter_checks.append((n, ok, f"explore/filter/{evidence.name}"))
        self.note("filter.check", f"{last.label!r} {'verified' if ok else 'NOT verified'} by screenshot "
                                  f"(check {n})", outcome="ok" if ok else "error")

    # ---------- the tour ----------

    def tab_keys(self) -> set[str]:
        return {t.key for t in self.tabs}

    def next_tab(self) -> ob.Candidate | None:
        return next((t for t in self.tabs if t.key not in self.tab_to and ob.find(self.obs.cands, t)), None)

    def options(self, s: Seen) -> list[ob.Candidate]:
        filter_row = self.filter_row(s)
        return [c for c in s.cands if c.key not in s.tried and c.key not in self.tab_keys() and c.key not in filter_row
                and not ob.denied(c, upsell=s.upsell) and (c.key not in s.waiting or s.visits > s.waiting[c.key])]

    def filter_row(self, s: Seen) -> set[str]:
        """The content filter's controls on this screen: the opener, and every option in the chosen one's row.
        The tour never taps them, so it can't undo the filter."""
        keys = {t.key for t in self.filter_taps}
        chosen = ob.find(s.cands, self.filter_taps[-1]) if self.filter_taps else None
        if chosen:
            cy = ob.center(chosen.rect)[1]
            keys |= {c.key for c in s.cands
                     if abs(ob.center(c.rect)[1] - cy) < 24 and abs(c.rect.h - chosen.rect.h) < 24}
        return keys

    def has_work(self, s: Seen) -> bool:
        if s.done or s.kind not in ("screen", "modal", "sheet"):
            return False
        can_tap = s.taps < TAPS_PER_STATE and bool(self.options(s))
        return can_tap or (s.kind == "screen" and s.swipes < SWIPES_PER_STATE)

    def next_target(self) -> Seen | None:
        pending = [t for t in self.tabs if t.key not in self.tab_to]
        if pending:
            if self.next_tab():
                return self.current
            if self.current is not self.root:
                return self.root
            for t in pending:
                self.tab_to[t.key] = ""
        work = [s for s in self.states if self.has_work(s)]
        return min(work, key=lambda s: (s.depth, s is not self.current, self.states.index(s)), default=None)

    def next_move(self, s: Seen) -> Move | None:
        tab = self.next_tab()
        if tab:
            return Move("tap", tab, why="tab sweep")
        opts = self.options(s)
        if opts and s.taps < TAPS_PER_STATE:
            if s.noop_run >= NOOPS_BEFORE_SONNET and not s.asked_sonnet:
                s.asked_sonnet = True
                move = self.hard_screen(s, opts, "several taps here changed nothing; find a move that does something")
                if move:
                    return move
            return self.ranked_move(s, opts)
        if s.kind == "screen" and s.swipes < SWIPES_PER_STATE:
            return Move("swipe", direction="up", why="scroll for more")
        return None

    def ranked_move(self, s: Seen, opts: list[ob.Candidate]) -> Move | None:
        if s.order is None:
            s.order = [c.key for c in opts]
            if len(opts) > 1:
                try:
                    order, s.confidence = decide.rank(self.trace_path, "explore", f"rank.{s.sid}", self.describe(s),
                                                      self.question(), [self.option_label(c) for c in opts],
                                                      **self.jev_options())
                    s.order = [opts[i].key for i in order]
                except decide.JevFailed:
                    s.asked_sonnet = True
                    move = self.hard_screen(s, opts, "the ranker failed; " + self.question())
                    if move:
                        return move
            labeled = sum(bool(c.tree_label) for c in opts)
            if s.confidence < UNSURE and labeled < FEW_LABELED and not s.asked_sonnet:
                s.asked_sonnet = True
                move = self.hard_screen(s, opts, "the ranker was unsure; " + self.question())
                if move:
                    return move
        by_key = {c.key: c for c in opts}
        key = next((k for k in s.order if k in by_key), None) or opts[0].key
        return Move("tap", by_key[key], decider="jev" if len(opts) > 1 else "code", why="ranked")

    def goto(self, target: Seen) -> bool:
        """Walks recorded moves to the target, checking every hop. A tab tap that changes nothing means something
        (a sheet, a drawer) covers the tab bar: BACK closes it once and the walk starts over. Any other miss
        relaunches once."""
        relaunched = backed = False
        while self.current is not target:
            hops = self.route(self.current, target)
            if hops is None and self.current.kind != "external" and not self.shows_tabs(self.current):
                self.act(Move("back", why="no recorded way back from here"), purpose="nav")
                hops = [] if self.current is target else self.route(self.current, target)
            why, covered = f"no recorded way from {self.current.sid} to {target.sid}", False
            for move, expected in hops or []:
                here = self.current
                self.act(move, purpose="nav")
                if self.current is not expected:
                    why = f"{move.action} toward {expected.sid} landed on {self.current.sid}"
                    covered = self.current is here and move.cand is not None and move.cand.key in self.tab_keys()
                    break
            if self.current is target:
                return True
            if covered and not backed:
                backed = True
                self.act(Move("back", why="a tab tap changed nothing: something covers the tab bar"), purpose="nav")
            elif relaunched:
                return False
            else:
                relaunched = True
                self.relaunch(why=why)
        return True

    def shows_tabs(self, s: Seen) -> bool:
        return any(ob.find(s.cands, t) for t in self.tabs)

    def route(self, src: Seen, dst: Seen) -> list[tuple[Move, Seen]] | None:
        came = {src.sid: None}
        queue = deque([src])
        while queue:
            x = queue.popleft()
            if x is dst:
                break
            for move, y in self.links(x):
                if y.sid not in came and y.kind not in ("external", "blocked"):
                    came[y.sid] = (x, move)
                    queue.append(y)
        if dst.sid not in came:
            return None
        hops, node = [], dst
        while came[node.sid]:
            x, move = came[node.sid]
            hops.append((move, node))
            node = x
        return hops[::-1]

    def links(self, x: Seen):
        for (a, b), move in self.edges.items():
            if a == x.sid:
                yield move, self.by_id[b]
        for tab in self.tabs:
            if self.tab_to.get(tab.key) and ob.find(x.cands, tab):
                yield Move("tap", tab, why="tab"), self.by_id[self.tab_to[tab.key]]
        if x.back_to and not any(a == x.sid and m.action == "back" for (a, _), m in self.edges.items()):
            yield Move("back", why="back"), self.by_id[x.back_to]
        if x.unscroll_to:
            yield Move("swipe", direction="down", why="scroll back"), self.by_id[x.unscroll_to]

    def leave_external(self) -> None:
        if self.obs is None:
            self.resync()
            if self.current.kind != "external":
                return
        self.act(Move("back", why="return from another app"), purpose="nav")
        if self.current.kind == "external":
            self.relaunch(why=f"BACK did not return from {self.obs.fg}")

    def read_upsell(self) -> None:
        """Scrolls an upsell to its end so every benefit and price is captured verbatim; never taps inside it."""
        for _ in range(UPSELL_SWIPES):
            s = self.current
            if not s.upsell or s.swipes >= 1 or s.kind == "external":
                return
            self.act(Move("swipe", direction="up", why="read the whole upsell"), purpose="tour")
            if self.current is s:
                return

    def check_caps(self) -> str:
        if self.actions >= self.limits["actions"]:
            return f"action cap ({self.limits['actions']})"
        if self.clock() - self.started >= self.tour_seconds:
            return "wall-time cap"
        if self.actions - self.last_new_at >= STALE_ACTIONS:
            return f"{STALE_ACTIONS} actions without a new state"
        return "" if self.checklist()[1] else "checklist answered"

    def tour(self) -> None:
        self.relaunch(first=True)
        while True:
            reason = self.check_caps()
            if reason:
                raise Stop(reason)
            try:
                if self.obs is None:
                    self.resync()
                if self.current.kind == "external":
                    self.leave_external()
                    continue
                target = self.next_target()
                if target is None:
                    raise Stop("nothing left to try")
                if not self.goto(target):
                    target.done = True
                    continue
                move = self.next_move(target)
                if move is None or move.action == "done":
                    target.done = True
                    continue
                seen = len(self.states)
                self.act(move)
                if move.decider == "model" and move.action == "back":
                    target.done = True
                if len(self.states) > seen and self.current.upsell:
                    self.read_upsell()
            except NeedRelaunch as e:
                self.note("relaunch", str(e))
                self.relaunch(why=str(e))
            except (McpTimeout, McpReplyError) as e:
                self.hang(self.current)
                self.note("hang", str(e)[:200], outcome="timeout")

    # ---------- the paywall and the core loop ----------

    def paywall_pass(self) -> None:
        """A paywall counts once a screen that isn't the launch teaser shows a price. Until then, follows an entry
        control (upgrade, plans, premium, plus, the teaser's own call to action) at most twice, reads what opens to
        the end, and goes back. Confirm words stay denied. With no price anywhere, the best upsell seen stands."""
        self.touring = False
        entries = sorted(((s, c) for s in self.states if s.kind in ("screen", "modal", "sheet")
                          for c in [self.entry(s)] if c), key=lambda sc: (sc[0].launch, sc[0].depth))
        for s, entry in entries[:2]:
            if self.priced_paywall():
                break
            if self.reopen(s) if s.launch else self.goto(s):
                self.follow_entry(entry)
            if s.launch:
                self.relaunch(why="the launch screen again, with the content filter, after the launch dialog")
        priced = self.priced_paywall()
        best = priced or max((s for s in self.states if s.upsell and not s.launch and s.kind != "external"),
                             key=lambda s: sum(bool(ob.PAYWALL.search(t)) for t in ob.texts(s.elements, self.device)),
                             default=None)
        self.paywall = best.sid if best else None
        if not priced:
            self.note("paywall", f"no price seen ({len(entries)} entry controls)")

    def entry(self, s: Seen) -> ob.Candidate | None:
        """The screen's best upsell entry: a control over a line of text (a dialog's title says "plus" too), then
        the shortest."""
        found = [c for c in s.cands if ob.ENTRY.search(c.label) and not ob.DISMISS.match(c.label.strip())
                 and not ob.denied(c, upsell=s.upsell)]
        return min(found, key=lambda c: (c.kind == "TextView", len(c.label.split())), default=None)

    def follow_entry(self, entry: ob.Candidate) -> None:
        self.act(Move("tap", entry, why="open the upsell on purpose"), purpose="nav")
        if self.current.kind == "external":
            self.leave_external()
        elif self.current.upsell and not self.current.launch:
            self.read_upsell()
            self.act(Move("back", why="out of the upsell"), purpose="nav")

    def priced_paywall(self) -> Seen | None:
        return next((s for s in self.states if s.priced and not s.launch and s.kind in ("screen", "modal", "sheet")),
                    None)

    def core_options(self) -> list[CoreAction]:
        options = []
        for s in self.states:
            found = ob.composer(s.cands, self.device) if s.kind == "screen" else None
            if found:
                options.append(CoreAction("chat", s, list(found), f"send a message on {s.sid} "
                                                                    f"({found[0].label or 'text box'})"))
                break
        feed = self.feed_option()
        options += [feed] if feed else []
        options += [CoreAction("action", s, [c], f"tap {c.label[:40]!r} on {s.sid}")
                    for s in self.states if s.kind == "screen" for c in [self.input_action(s)] if c]
        if feed and not any(o.kind in ("chat", "action") for o in options):
            inside = self.walk_to_input(feed)
            options = ([inside] if inside else []) + options
        return options[:10]

    def feed_option(self) -> CoreAction | None:
        main = {self.root.sid, *self.tab_to.values()}
        feeds = [(s, ob.feed_items(s.cands, self.device, self.tab_keys())) for s in self.states if s.sid in main]
        feeds = [(s, items) for s, items in feeds if items]
        if not feeds:
            return None
        s, items = max(feeds, key=lambda f: len(f[1]))
        return CoreAction("feed", s, items, f"open an item from the list on {s.sid} (e.g. {items[0].label[:40]!r})")

    def input_action(self, s: Seen) -> ob.Candidate | None:
        """A button that makes new content on each tap (Play, Generate, Draw, Spin ...): at most three words, so
        a sentence that starts with "Create" is not one."""
        return next((c for c in s.cands if c.tree_label and len(c.label.split()) <= 3 and ob.CREATE.match(c.label)
                     and c.key not in self.tab_keys() and not ob.denied(c, upsell=s.upsell)), None)

    def choose_core(self) -> CoreAction | None:
        """Jev names the core action among what the tour saw, or none of them."""
        options = self.core_options()
        if not options:
            return None
        shown = {s.sid: s for s in [o.state for o in options] + [s for s in self.states if s.kind == "screen"]}
        seen = "; ".join(self.describe(s) for s in shown.values())[:3000]
        try:
            result = decide.choose(self.trace_path, "explore", "core", seen, CORE_QUESTION,
                                   [o.name for o in options] + [NO_CORE], **self.jev_options())
        except decide.JevFailed:
            return next((o for o in options if o.kind == "chat"), options[0])
        index = decide.index_of(result.option_id)
        return options[index] if index < len(options) else None

    def walk_to_input(self, feed: CoreAction) -> CoreAction | None:
        """Opening an item is a step, not the core action: goes into the first item and takes its main action
        (Chat, Start ...) up to WALK_STEPS times, until a text box with send or a play/generate button shows. What it
        finds is one more option for Jev, never a replacement for Jev's answer: a text box inside an item may be a
        comment box or a message to another person."""
        if not self.goto(feed.state):
            return None
        self.act(Move("tap", feed.controls[0], why="core loop: look inside an item"), purpose="nav")
        tapped = set()
        for _ in range(WALK_STEPS):
            here = self.current
            if here.kind != "screen":
                break
            chat = self.chat_here()
            if chat:
                return CoreAction("chat", here, list(chat), f"send a message on {here.sid} (inside an item "
                                                            f"from {feed.state.sid})")
            action = self.input_action(here)
            if action:
                return CoreAction("action", here, [action], f"tap {action.label[:40]!r} on {here.sid}")
            steps = self.walk_steps(here, tapped)
            move = (self.ranked_move(here, steps) if len(steps) > 1 else
                    Move("tap", steps[0], why="the only main action") if steps else None)
            if move is None or move.action != "tap":
                break
            tapped.add(move.cand.key)
            self.act(Move("tap", move.cand, decider=move.decider, why="core loop: the item's main action"),
                     purpose="nav")
        self.note("core", "no input control inside the item")
        return None

    def walk_steps(self, here: Seen, tapped: set[str]) -> list[ob.Candidate]:
        """The item's main-action controls: a button, or a short label (Chat, New chat, Start), never a line of
        content that happens to say "chat" or "start", and never one already tapped."""
        return [c for c in here.cands if ob.PRIMARY.search(c.label)
                and (c.kind in ("Button", "ImageButton") or len(c.label.split()) <= 3)
                and c.key not in tapped and c.key not in self.tab_keys() and not ob.denied(c, upsell=here.upsell)]

    def core_loop(self) -> None:
        self.touring = False
        if not self.may_send:
            self.core_results.append("off (--no-send)")
            return
        choice = self.choose_core()
        if choice is None:
            self.core_results.append("no core action found on the screens seen")
            return
        self.core = choice
        self.note("core", f"core action: {self.core.name}", decider="jev")
        deadline = self.clock() + self.core_reps * CORE_SECONDS_PER_REP
        for n in range(1, self.core_reps + 1):
            if self.clock() > deadline:
                self.core_results.append("stopped: out of time")
                return
            try:
                if not self.at_core(n) and not (n == 1 and self.chat_through_an_item()):
                    self.core_results.append(f"pass {n}: could not get back to {self.core.state.sid}")
                    return
                result, hit = self.core_once(n)
            except DEVICE_ERRORS as e:
                self.core_results.append(f"pass {n}: {type(e).__name__}: {e}"[:200])
                return
            self.core_results.append(f"pass {n}: {result or 'no measurement'}")
            if hit:
                self.core_hit = f"{hit} on pass {n}"
                if self.current.upsell:
                    self.read_upsell()
                return

    def at_core(self, n: int) -> bool:
        """After the first pass a chat stays where it is: the conversation grows, so it never matches its first
        capture again, and walking 'back' to it would leave it. Anything else walks back to the core state."""
        if n > 1 and self.core.kind == "chat" and self.live_composer():
            self.current = self.core.state
            return True
        return self.goto(self.core.state)

    def chat_through_an_item(self) -> bool:
        """The chosen chat can't be reached again (a tab that reopens whichever chat was open last lands there
        only by chance): walks into the first feed item to its composer instead, the same kind of core action."""
        feed = self.feed_option() if self.core.kind == "chat" else None
        inside = self.walk_to_input(feed) if feed else None
        if inside is None or inside.kind != "chat":
            return False
        self.note("core", f"{self.core.state.sid} can't be reached again; the chat is {inside.name}")
        self.core = inside
        return True

    def core_once(self, n: int) -> tuple[str, str]:
        """One pass of the core action. Returns its measurement and what stopped the loop, if anything."""
        core, before = self.core, ob.texts(self.obs.elements, self.device)
        self.last_summary = ""
        if core.kind == "chat":
            message = CORE_MESSAGES[(n - 1) % len(CORE_MESSAGES)]
            box, _ = self.live_composer() or core.controls
            self.act(Move("tap", box, why="core loop: focus the text box"), purpose="core", loop=n)
            if not self.stop_kind:
                self.act(Move("type", text=message, why="core loop: type"), purpose="core", loop=n)
            if not self.stop_kind:
                _, send = self.live_composer()
                self.act(Move("tap", send, why="core loop: send"), purpose="core", loop=n,
                         watch=lambda: self.watch(before | {message}, REPLY_WAIT_S, "reply"))
            return self.last_summary, self.stop_text()
        control = core.controls[(n - 1) % len(core.controls)]
        verb = "load" if core.kind == "feed" else "result"
        self.act(Move("tap", control, why=f"core loop: {core.kind}"), purpose="core", loop=n,
                 watch=lambda: self.watch(before, LOAD_WAIT_S, verb))
        if not self.stop_kind:
            if self.current.kind == "external":
                self.leave_external()
            elif self.current is not core.state:
                self.act(Move("back", why="core loop: back"), purpose="core", loop=n)
        return self.last_summary, self.stop_text()

    def stop_text(self) -> str:
        return f"{self.stop_kind} ({self.stop_evidence})" if self.stop_kind else ""

    def chat_here(self) -> tuple[ob.Candidate, ob.Candidate] | None:
        return ob.composer(self.current.cands, self.device) if self.current.kind == "screen" else None

    def live_composer(self) -> tuple[ob.Candidate, ob.Candidate] | None:
        """The text box and its send control on the screen as it is now."""
        if self.obs is None or self.obs.fg != self.package or ob.dialog_box(self.obs.cands, self.device):
            return None
        return ob.composer(self.obs.cands, self.device)

    def watch(self, before: set[str], max_s: float, verb: str) -> str:
        """Polls the element list after the action until new text stops changing for QUIET_S."""
        start, samples = self.clock(), []
        while self.clock() - start < max_s:
            _, elements = self.phone.elements()
            new = frozenset(ob.texts(elements, self.device) - before)
            samples.append((self.clock() - start, new))
            started, finished, _ = ob.reply_timing(samples)
            if started is not None and samples[-1][0] - finished >= QUIET_S:
                break
            self.sleep(0.5)
        self.last_summary = ob.timing_line(verb, samples, max_s)
        return self.last_summary

    def hit(self, s: Seen, here: Seen, before: Obs, move: Move) -> tuple[str, str]:
        """What a core-loop move brought up that ends the loop, read from what changed on screen and never from
        words in content. A few words for loop_stop, and the evidence."""
        if here.kind == "external":
            if self.obs.fg in BILLING:
                return "billing", self.obs.fg
            return ("left the app", self.obs.fg) if self.core.kind == "chat" else ("", "")
        if self.core.kind == "chat":
            return self.chat_stop(here, before, move)
        if here is not s and here.kind in ("modal", "sheet"):
            return ("paywall" if here.priced else f"{here.kind} opened"), here.sid
        if here is not s and here.upsell:
            return ("paywall" if here.priced else "upsell screen"), here.sid
        moved = ob.counters(before.elements, self.obs.elements, self.device, [(0, ob.TOP_CHROME_BOTTOM_PX)])
        return ("counter", moved[0]) if moved else ("", "")

    def chat_stop(self, here: Seen, before: Obs, move: Move) -> tuple[str, str]:
        """In a chat only the window can stop the loop: a dialog over it, the text box disabled or gone, send still
        disabled once a message is typed, or a counter moving beside the composer. The conversation's own text,
        prices and timestamps included, never does."""
        if here.kind in ("modal", "sheet"):
            inside = [e for e in here.elements if here.box is None or ob.inside(ob.rect(e), here.box)]
            return ("paywall" if ob.priced(inside, self.device) else "dialog opened"), here.sid
        box = next((c for c in self.obs.cands if c.kind == "EditText"), None)
        if box is None:
            return "input gone", here.sid
        live = self.live_composer()
        if not box.enabled or (move.action == "type" and live and not live[1].enabled):
            return "input disabled", here.sid
        band = (int(box.rect.y) - COMPOSER_BAND_PX, int(box.rect.y + box.rect.h) + COMPOSER_BAND_PX)
        moved = ob.counters(before.elements, self.obs.elements, self.device, [band])
        return ("counter", moved[0]) if moved else ("", "")

    # ---------- the replay check ----------

    def verify_replay(self) -> None:
        """Replays each recorded segment from a fresh launch and counts moves that reach the recorded state."""
        deadline = self.clock() + REPLAY_MINUTES * 60
        matched = total = 0
        for segment in self.segments:
            if not segment or self.clock() > deadline:
                continue
            try:
                self.quiet_launch()
                for move, _, to_sid in segment:
                    if self.clock() > deadline:
                        break
                    live = ob.find(self.obs.cands, move.cand) if move.cand else None
                    total += 1
                    if move.cand and live is None:
                        continue
                    if move.action == "tap" and not self.safe_tap(live, "replay"):
                        break
                    if move.action != "tap":
                        self.perform(move, live)
                    seen = self.observe().fp
                    if self.escape_billing():
                        self.observe()
                    if ob.same_state(seen, self.by_id[to_sid].fp):
                        matched += 1
                    else:
                        near = next((s.sid for s in self.states if ob.same_state(s.fp, seen)), "a new screen")
                        self.note("replay.miss", f"{move.action} {move.cand.label[:30] if move.cand else ''} "
                                                 f"expected {to_sid}, reached {near}", outcome="error")
            except DEVICE_ERRORS as e:
                self.note("replay", f"segment stopped: {e}", outcome="error")
        self.replay = (matched, total)
        self.note("replay", f"{matched}/{total} replayed moves reached the recorded state")

    def quiet_launch(self) -> None:
        """A relaunch for the replay check: same launch, dialogs, and filter, nothing recorded."""
        self.phone.terminate()
        self.phone.launch()
        self.wait_for_app(self.launch_root)
        for _ in range(3):
            if not ob.dialog_box(self.obs.cands, self.device):
                break
            close = ob.dismiss_control(self.obs.cands)
            if close is None:
                self.perform(Move("back"), None)
            elif not self.safe_tap(close, "replay"):
                break
            self.observe()
        for n, tap in enumerate(self.filter_taps):
            live = ob.find(self.obs.cands, tap)
            if live and not (n == len(self.filter_taps) - 1 and self.stands_out(tap)) and self.safe_tap(live, "replay"):
                self.observe()

    # ---------- Jev and Sonnet ----------

    def question(self) -> str:
        return f"Which tap most likely helps with this goal: {self.goal}?" if self.goal else RANK_QUESTION

    def jev_options(self) -> dict:
        return {"profile": self.ctx.profile, "budget": self.budget, "no_cache": self.ctx.no_cache,
                "replay": self.ctx.replay, "cache_dir": self.cache_dir}

    def describe(self, s: Seen) -> str:
        texts = [c.tree_label[:60] for c in sorted(s.cands, key=lambda c: (c.rect.y, c.rect.x)) if c.tree_label][:30]
        found = [item for item in self.checklist()[0] if item != "root"]
        return (f"Android app {self.package}, screen {s.sid} ({s.kind}). Text on screen: {'; '.join(texts)}. "
                f"Found so far: {', '.join(found) or 'nothing yet'}.")

    def option_label(self, c: ob.Candidate) -> str:
        return f"{c.label[:60] or 'unlabeled ' + c.kind} ({where(c, self.device)})"

    def ask(self, prompt: str, step: str, text: str, png: bytes, schema, max_tokens: int):
        role = config.roles(self.ctx.profile)["explore_vision"]
        parsed, _ = llm.call(trace_path=self.trace_path, stage="explore", step=step, model=role["model"],
                             effort=role.get("effort"), system=(PROMPTS / f"{prompt}.md").read_text(),
                             messages=[{"role": "user", "content": [{"type": "image", "png": png},
                                                                    {"type": "text", "text": text}]}],
                             max_tokens=max_tokens, budget=self.budget, schema=schema, no_cache=self.ctx.no_cache,
                             replay=self.ctx.replay, cache_dir=self.cache_dir)
        return parsed

    def boxed_png(self, s: Seen, cands: list[ob.Candidate], names: list[str]) -> bytes:
        top, bottom = self.device.content_top_px, self.device.content_bottom_px
        image = Image.open(self.out / "states" / f"{s.sid}.png").convert("RGB").crop((0, top, self.device.w_px, bottom))
        draw, font = ImageDraw.Draw(image), ImageFont.load_default(size=36)
        for c, name in zip(cands, names, strict=True):
            r = c.rect
            draw.rectangle((r.x, r.y - top, r.x + r.w, r.y - top + r.h), outline=(255, 0, 0), width=4)
            draw.rectangle((r.x, r.y - top, r.x + 22 * len(name) + 8, r.y - top + 40), fill=(255, 0, 0))
            draw.text((r.x + 4, r.y - top), name, fill=(255, 255, 255), font=font)
        image = image.resize((int(image.width * ICON_SCALE), int(image.height * ICON_SCALE)))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def name_icons(self, s: Seen) -> None:
        """The Sonnet icon pass: names boxes with no words and adds visible controls the tree doesn't list."""
        unnamed = [n for n, c in enumerate(s.cands, start=1) if not c.label]
        png = self.boxed_png(s, s.cands, [str(n) for n in range(1, len(s.cands) + 1)])
        text = (f"Name these boxes: {', '.join(map(str, unnamed)) or 'none'}.\n"
                f"The image is {int(self.device.w_px * ICON_SCALE)} px wide.")
        try:
            result = self.ask("icons", f"icons.{s.sid}", text, png, IconPass, 2000)
        except llm.LLMFailure as e:
            self.note(f"icons.{s.sid}", f"icon pass failed: {e}", outcome="error")
            return
        for item in result.names:
            if item.box_id in unnamed:
                c = s.cands[item.box_id - 1]
                c.label = item.name
                s.icon_labels.append(IconLabel(mcp_ref=c.ref, name=item.name))
        for point in result.extra_points[:8]:
            self.add_vision(s, point.x / ICON_SCALE, point.y / ICON_SCALE + self.device.content_top_px, point.name)

    def add_vision(self, s: Seen, x: float, y: float, name: str) -> None:
        d = self.device
        if not (0 <= x < d.w_px and d.content_top_px <= y < d.content_bottom_px):
            return
        if any(ob.inside(Rect(x=x, y=y, w=0, h=0), c.rect) for c in s.cands):
            return
        half = VISION_BOX_DP * d.scale / 2
        x0, y0 = max(0.0, x - half), max(float(d.content_top_px), y - half)
        box = Rect(x=round(x0), y=round(y0), w=round(min(float(d.w_px), x + half) - x0),
                   h=round(min(float(d.content_bottom_px), y + half) - y0))
        s.vision.append(VisionElement(name=name, rect_px=box))
        s.cands.append(ob.Candidate(label=name, kind="vision", rect=box, ref=None, tree_label=""))

    def hard_screen(self, s: Seen, opts: list[ob.Candidate], goal: str) -> Move | None:
        """Sonnet picks one move when Jev is unsure, has failed, or taps keep changing nothing."""
        ids = list(decide.option_ids([c.label for c in opts]))
        moves = "; ".join(f"{line.action} {line.from_state}>{line.to_state}" for line in self.recent_lines()) or "none"
        allowed = "\n".join(f"{i}: {self.option_label(c)}" for i, c in zip(ids, opts, strict=True))
        text = f"Goal: {goal}\nAllowed taps:\n{allowed}\nTyping allowed: no\nRecent moves: {moves}"
        try:
            png = self.boxed_png(s, opts, ids)
            answer = self.ask("hard_screen", f"hard.{s.sid}", text, png, HardScreenAction, 1024)
        except llm.LLMFailure as e:
            self.note(f"hard.{s.sid}", f"hard-screen call failed: {e}", outcome="error")
            return None
        if answer.action == "tap" and answer.element_id in ids:
            return Move("tap", opts[ids.index(answer.element_id)], decider="model", why=answer.reason[:80])
        if answer.action in ("back", "swipe", "done"):
            return Move(answer.action, direction=answer.direction or "up", decider="model", why=answer.reason[:80])
        return None

    def recent_lines(self) -> list[ActionLine]:
        path = self.out / "actions.jsonl"
        lines = path.read_text().splitlines()[-5:] if path.exists() else []
        return [ActionLine.model_validate_json(line) for line in lines]

    # ---------- coverage and output ----------

    def checklist(self) -> tuple[list[str], list[str]]:
        items = tomllib.loads((config.CONFIG / "checklist.toml").read_text())["items"]
        answered = {
            "root": self.root is not None,
            "all_tabs": all(self.tab_to.get(t.key) for t in self.tabs),
            "paywall_or_membership": self.priced_paywall() is not None,
            "settings": any("settings" in s.via.lower() for s in self.states),
            "limit": bool(self.core_hit),
        }
        return [i for i in items if answered.get(i)], [i for i in items if not answered.get(i)]

    def write(self, app_version: str | None) -> None:
        for s in self.states:
            state_file = StateFile(
                state_id=s.sid, kind=s.kind, parent_id=s.parent, fingerprint=str(s.fp), foreground_package=s.fg,
                screenshot=f"states/{s.sid}.png", elements_reply=f"states/{s.sid}.elements.json", settled=s.settled,
                settle_seconds=s.settle_s, dynamic_regions=s.dynamic, captured_at=s.captured_at,
                icon_labels=s.icon_labels, vision_elements=s.vision, blocked_reason=s.blocked_reason)
            (self.out / "states" / f"{s.sid}.json").write_text(state_file.model_dump_json(indent=1))
        answered, still_open = self.checklist()
        explore = ExploreFile(
            app_package=self.package, app_version=app_version, budget=self.ctx.budget, relaunches=self.relaunches,
            content_filter=" > ".join(t.label for t in self.filter_taps) or None,
            blocked_state_ids=[s.sid for s in self.states if s.kind == "blocked"],
            coverage=Coverage(states_found=len(self.states), actions_taken=self.actions, stop_reason=self.stop_reason,
                              checklist_answered=answered, checklist_open=still_open),
            device=self.device)
        (self.out / "explore.json").write_text(explore.model_dump_json(indent=1))
        shutil.rmtree(self.scratch, ignore_errors=True)


def mean_color(image: Image.Image, r: Rect) -> np.ndarray:
    crop = image.crop((int(r.x), int(r.y), int(r.x + r.w), int(r.y + r.h)))
    return np.asarray(crop, dtype=float).reshape(-1, 3).mean(axis=0)


def adb_shell(serial: str, args: list[str]) -> str | None:
    """A read-only adb fact (density, app version, AVD name). mobile-mcp has no call for these."""
    tool = adb()
    if not tool:
        return None
    out = subprocess.run([tool, "-s", serial, "shell", *args], capture_output=True, text=True, timeout=30)
    return out.stdout.strip() if out.returncode == 0 else None


def adb_value(serial: str, args: list[str], marker: str) -> str | None:
    lines = (adb_shell(serial, args) or "").splitlines()
    values = [line.split(marker, 1)[1].strip() for line in lines if marker in line]
    return values[-1] if values else None


def exhibit(ex: Explorer, app_version: str | None) -> str:
    answered, still_open = ex.checklist()
    seconds = sorted(ex.phone.list_seconds)
    fast = sum(t < 2.0 for t in seconds)
    kinds = Counter(s.kind for s in ex.states)
    lines = [f"# Explore: {ex.ctx.app['name']} ({ex.package} {app_version or 'version unknown'})", "",
             f"- Device: `{ex.serial}` (mobile-mcp `{ex.phone.device}`)",
             f"- Budget `{ex.ctx.budget}`: {ex.actions} actions, stop: **{ex.stop_reason}**",
             f"- States: {len(ex.states)} ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))})",
             f"- Bottom tabs: {len(ex.tabs)} found, {sum(bool(ex.tab_to.get(t.key)) for t in ex.tabs)} visited",
             f"- Relaunches: {ex.relaunches} (tour cap {MAX_RELAUNCHES}, then {CORE_RELAUNCHES} for the passes after)",
             *[f"  - {n}: {why}" for n, why in enumerate(ex.relaunch_reasons, start=1)],
             f"- Paywall or plans screen captured: {paywall_line(ex)}",
             f"- Content filter: {' > '.join(repr(t.label) for t in ex.filter_taps) or 'none found'}"]
    lines += [f"  - check {n}: {'verified' if ok else 'NOT verified'} by screenshot (`{path}`)"
              for n, ok, path in ex.filter_checks]
    lines += [f"- Redaction: {len(ex.secrets)} strings listed in SIMULA_REDACT; {ex.redacted} element texts "
              "redacted at capture"]
    lines += [f"- Checklist answered: {', '.join(answered) or 'none'}; open: {', '.join(still_open) or 'none'}",
              f"- Denied taps: {sum(1 for _ in denied_lines(ex))} logged, {ex.denied_executed} executed. The deny-list "
             "is English only: a confirm button in another language isn't caught, and the store's billing screen "
             "getting BACK at once is the guard that works in any language (Play purchases only).", ""]
    if seconds:
        lines += ["## Settle signal", "",
                  f"Element-list call time over {len(seconds)} calls: median {seconds[len(seconds) // 2]:.2f} s, "
                  f"p90 {seconds[int(len(seconds) * 0.9)]:.2f} s, max {seconds[-1]:.2f} s; "
                  f"{fast}/{len(seconds)} under the 2 s settle threshold.", ""]
    passes = sum(r.startswith("pass ") for r in ex.core_results)
    lines += ["## Core loop (the free experience)", "",
              f"Core action: {ex.core.name if ex.core else 'none'}; {passes} of {ex.core_reps} passes.", ""]
    lines += [f"- {r}" for r in ex.core_results]
    lines += [f"- Stopped by: {ex.core_hit}" if ex.core_hit else "- Nothing stopped the loop.", ""]
    matched, total = ex.replay
    lines += ["## Replay check", "", f"{matched}/{total} replayed moves reached the recorded state"
              + (f" ({100 * matched / total:.0f}%)." if total else "."), "",
              "## States", "", "| id | kind | over | depth | first words |", "|---|---|---|---|---|"]
    for s in ex.states:
        words = next((c.tree_label for c in s.cands if c.tree_label), "")[:50].replace("|", "/")
        lines.append(f"| {s.sid} | {s.kind} | {s.parent or ''} | {s.depth} | {words} |")
    return "\n".join(lines) + "\n"


def paywall_line(ex: Explorer) -> str:
    if not ex.paywall:
        return "no"
    prices = [t for t in ob.texts(ex.by_id[ex.paywall].elements, ex.device) if ob.PRICE.search(t)]
    return f"yes, {ex.paywall}, prices: {'; '.join(repr(p) for p in prices[:6])}" if prices \
        else f"yes, {ex.paywall}, no price seen"


def denied_lines(ex: Explorer):
    path = ex.out / "actions.jsonl"
    for raw in path.read_text().splitlines() if path.exists() else []:
        line = ActionLine.model_validate_json(raw)
        if line.outcome == "denied":
            yield line


def redact_list() -> list[str]:
    return [s.strip() for s in os.environ.get("SIMULA_REDACT", "").split(",") if s.strip()]


def run(ctx: Ctx) -> None:
    if ctx.replay:
        raise llm.ReplayMiss("explore drives the device; --replay reuses a finished explore/ folder")
    if not redact_list():
        needs_human(ctx.run_dir, "explore", "SIMULA_REDACT is empty",
                    "the explorer saves screenshots and element lists, and nothing would hide the account handle",
                    [".env", ".env.example"], f"simula explore {ctx.app['name']} --run {ctx.run_dir.name}")
        raise ExploreFailed("SIMULA_REDACT is empty: list the emulator account's handle and names in .env, "
                            "comma-separated, then explore again")
    out = ctx.run_dir / "explore"
    shutil.rmtree(out, ignore_errors=True)
    (out / ".scratch").mkdir(parents=True)
    serial = resolve_serial(ctx.device)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("explore stopped by SIGTERM"))
    with emulator_lock(serial, wait_s=LOCK_WAIT_S):
        server = Server(cwd=out)
        try:
            avd = adb_shell(serial, ["getprop", "ro.boot.qemu.avd_name"]) or None
            ex = Explorer(ctx, Phone(server, ctx.app["package"], out / ".scratch", serial, avd), out)
            ex.serial = serial
            explore_app(ex)
        finally:
            server.close()


def explore_app(ex: Explorer) -> None:
    """Every phase's failure is logged; whatever was captured is written, even when one ends the process."""
    app_version = None
    try:
        ex.measure_device()
        app_version = adb_value(ex.serial, ["dumpsys", "package", ex.package], "versionName=")
        run_trace(ex.ctx.run_dir, stage="explore", step="device", decider="code",
                  note=f"serial {ex.serial}, mobile-mcp device {ex.phone.device}")
        run_tour(ex)
        if ex.root and not ex.stop_reason.startswith(("blocked root", "second hang")):
            for phase in (ex.paywall_pass, ex.core_loop, ex.verify_replay):
                try:
                    phase()
                except (Stop, llm.CapReached, *DEVICE_ERRORS) as e:
                    ex.core_results.append(f"{phase.__name__} stopped: {type(e).__name__}: {e}"[:200])
                    ex.note(phase.__name__, str(e)[:200], outcome="error")
                except BaseException as e:
                    ex.core_results.append(f"{phase.__name__} crashed: {type(e).__name__}: {e}"[:200])
                    ex.note(phase.__name__, f"crashed: {type(e).__name__}: {e}"[:200], outcome="error")
                    raise
    finally:
        ex.write(app_version)
        update_manifest(ex.ctx.run_dir, app_version=app_version)
        write_exhibit(ex.ctx.run_dir, 1, "explore", exhibit(ex, app_version))
        run_trace(ex.ctx.run_dir, stage="explore", step="summary", decider="code",
                  note=f"{len(ex.states)} states, {ex.actions} actions, stop: {ex.stop_reason}; "
                       f"{ex.redacted} element texts redacted")
    if not ex.states or ex.stop_reason.startswith(DEVICE_STOPS):
        raise ExploreFailed(ex.stop_reason or "no state was recorded")


def run_tour(ex: Explorer) -> None:
    try:
        ex.tour()
    except Stop as e:
        ex.stop_reason = str(e)
    except llm.CapReached as e:
        ex.stop_reason = f"$ cap: {e}"
    except DEVICE_ERRORS as e:
        ex.stop_reason = f"device error: {type(e).__name__}: {e}"[:200]
        ex.note("tour", ex.stop_reason, outcome="error")
    except BaseException as e:
        ex.stop_reason = f"crashed: {type(e).__name__}: {e}"[:200]
        ex.note("tour", ex.stop_reason, outcome="error")
        raise
    finally:
        ex.tour_actions = ex.actions
