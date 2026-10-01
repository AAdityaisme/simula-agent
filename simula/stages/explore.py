"""Goal 1: drive the app through mobile-mcp and record every distinct state, the tap graph with transitions,
and full-res screenshots. Then use the app: repeat its core action and measure the free experience.

The only agent in the pipeline: code decides what is possible, Jev what is likely, Sonnet what is hard, and
code checks every answer."""

import contextlib
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
from simula.contracts import (ActionLine, Arrival, Coverage, Device, ExploreFile, HardScreenAction, IconLabel, IconPass,
                              Point, Progress, Rect, ReplacedCapture, StageOutcome, StateFile, VisionElement, WalkPick)
from simula.device import observe as ob
from simula.device.mcp import McpReplyError, McpTimeout, Phone, Server
from simula.device.devices import adb, emulator_lock, online, resolve_serial
from simula.runlog import needs_human, now, run_trace, update_manifest, write_exhibit
from simula.stages import Ctx, NotStarted, rerun_command

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
SETTLE_GAP_S = 3.0
SETTLE_ASK_S = 30.0
SETTLE_CAP_S = 180.0
GOTO_ROUNDS = 4
CORE_OPTIONS = decide.CHUNK - 1  # with "none of these", one Choice question stays within decide.CHUNK
REPLAY_MINUTES = 8
SPLASH_WAIT_S = 90
LAUNCH_WAIT_S = 30
LAUNCH_QUIET_S = 3
WALK_STEPS = 3
WALK_SCROLLS = 30
WALK_ITEMS = 3
WALK_SURE = 0.5
COMPOSER_BAND_PX = 150
LIMIT_STOPS = ("counter", "input disabled", "paywall", "limit")

RANK_QUESTION = "Which tap most likely reveals a limit, a paywall, a currency, an ad, or a new core screen of the app?"
FILTER_QUESTION = ("A content or safety filter decides how much adult or unsafe content the app shows (for "
                   "example 'SFW only', 'Safe mode', 'Hide NSFW', 'Family mode'). Which control sets or opens such a "
                   "filter? If the filter's options are visible, pick the most restrictive one. Pick the last option "
                   "if no control here is a content or safety filter.")
FILTER_MENU_QUESTION = "Which option is the most restrictive content setting (shows the least adult or unsafe content)?"
FILTER_STATE_QUESTION = ("Which state of this switch is the most restrictive content setting (shows the least adult "
                         "or unsafe content)?")
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
DEVICE_LOST = (McpReplyError, McpTimeout)
DEVICE_STOPS = ("device error", "second hang")
AWAY = ("external", "rotated")  # recorded, never explored, left by leave(): another app, or the screen turned sideways


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
    done: str = ""  # the rule that ended the screen's exploration; empty while it has work
    dynamic: list = field(default_factory=list)
    icon_labels: list = field(default_factory=list)
    vision: list = field(default_factory=list)
    blocked_reason: str | None = None
    replaced: list = field(default_factory=list)  # ReplacedCapture: earlier captures a relaunch replaced


@dataclass
class CoreAction:
    kind: str
    state: Seen
    controls: list[ob.Candidate]
    name: str


@dataclass
class Landing:
    """What invariant 1 made of a screen: arrived or not, the one action toward the target the model named, if any,
    and the two signals."""
    arrived: bool
    move: Move | None
    model: str
    structure: float


def where(c: ob.Candidate, device: Device) -> str:
    x, y = c.point
    row = ("top", "middle", "bottom")[min(2, int(3 * (y - device.content_top_px)
                                              / (device.content_bottom_px - device.content_top_px)))]
    return f"{row} {('left', 'center', 'right')[min(2, int(3 * x / device.w_px))]}"


def hop_key(s: Seen, move: Move) -> tuple:
    return s.sid, move.action, move.cand.key if move.cand else move.direction


def label_of(c: ob.Candidate, device: Device) -> str:
    state = "" if c.checked is None else "on, " if c.checked else "off, "
    return f"{c.label[:60] or 'unlabeled ' + c.kind} ({state}{where(c, device)})"


def filter_label(taps: list[ob.Candidate], on: bool | None) -> str:
    """The filter as applied: its controls in order, and a switch's kept state."""
    state = "" if on is None else " (on)" if on else " (off)"
    return " > ".join(t.label for t in taps) + state if taps else ""


def texts_by_y(elements: list[dict], device: Device) -> list[tuple[int, str]]:
    return sorted((e["coordinates"]["y"], e["text"].strip()) for e in elements
                  if ob.in_content(e, device) and (e.get("text") or "").strip())


def title_of(elements: list[dict], device: Device) -> str:
    top = [t for y, t in texts_by_y(elements, device) if y < ob.TOP_CHROME_BOTTOM_PX]
    return top[0][:60] if top else ""


def png_half(image: Image.Image) -> bytes:
    """A (content) crop at half size, as a model call sees it."""
    image = image.resize((int(image.width * ICON_SCALE), int(image.height * ICON_SCALE)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def content(image: Image.Image, device: Device) -> Image.Image:
    return image.convert("RGB").crop((0, device.content_top_px, device.w_px, device.content_bottom_px))


def listed(cands: list[ob.Candidate], device: Device) -> tuple[str, list[str]]:
    """The controls of a screen as a model reads them, one id each, and the ids."""
    ids = list(decide.option_ids([c.label for c in cands]))
    return "\n".join(f"{i}: {label_of(c, device)}" for i, c in zip(ids, cands, strict=True)) or "none", ids


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
        self.filter_on: bool | None = None  # the state a switch filter is kept in; None when the filter is a tap
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
        self.core_completed = 0  # passes that did the core action and saw its result (a reply, a load, a limit)
        self.paywall: str | None = None
        self.relaunch_reasons: list[str] = []
        self.returns: list[str] = []  # launches that found the app as it was left: no relaunch, so not counted as one
        self.left: Seen | None = None  # the app's screen the last move away from the app started from
        self.left_back = False  # that move was BACK, which can end the app's task (a launch then starts it afresh)
        self.exits: set[str] = set()  # states BACK left the app from: BACK is never taken from them again
        self.last_summary, self.last_seen = "", False  # the last watch's timing line; whether its result showed
        self.stop_kind = self.stop_evidence = ""
        self.tour_actions = 0
        self.replay = (0, 0)
        self.started = clock()
        self.secrets = redact_list()
        self.redacted = 0
        self.denied_executed = 0
        self.counts: Counter = Counter()
        self.settles: list[tuple[float, str]] = []
        self.landing: Landing | None = None
        self.replay_fingerprint = (0, 0)
        self.home: Seen | None = None  # while a relaunch is set up: the screen it lands on, maybe reloaded
        (out / "states").mkdir(parents=True, exist_ok=True)
        self.scratch = out / ".scratch"
        self.scratch.mkdir(exist_ok=True)

    # ---------- observing ----------

    def observe(self) -> Obs:
        self.obs = None
        settled = ob.settle(self.phone.elements, self.phone.small_hash, self.device, self.clock, self.sleep)
        if ob.anr(settled.elements):
            return self.answer_anr(settled.elements)
        self.anr_waited = False
        shot = self.phone.screenshot(self.scratch / "now.png", (self.device.w_px, self.device.h_px))
        image = Image.open(shot).convert("RGB")
        reply, elements, hits = ob.redact(settled.reply, image, self.secrets)
        if hits or not settled.ok:
            ob.redact(self.phone.elements()[0], image, self.secrets)  # the screen may have moved since the list
        self.redacted += hits
        image.save(shot)
        fg = self.phone.foreground()
        if not settled.ok:
            self.note("settle", f"unsettled after {settled.seconds:.1f}s", outcome="timeout")
        self.obs = Obs(reply, elements, image, fg, ob.fingerprint(fg, elements, image, self.device),
                       ob.controls(elements, self.device), settled.ok, round(settled.seconds, 2))
        return self.obs

    def answer_anr(self, elements: list[dict]) -> Obs:
        wait = next((c for c in ob.controls(elements, self.device) if c.ident == "aerr_wait"), None)
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
            self.current = self.record(self.observe(), None, None, None)
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

    def record(self, obs: Obs, came_from: Seen | None, move: Move | None, before: Obs | None) -> Seen:
        for known in self.states:
            if ob.same_state(known.fp, obs.fp):
                if known is not came_from:
                    self.revisit(known, obs)
                return known
        kind, box = self.kind_of(obs, before)
        home = self.home if kind == "screen" and self.home and self.homelike(self.home, obs) else None
        sid = home.sid if home else f"s{len(self.states) + 1:02d}"
        if home:
            self.keep_capture(home)
        shutil.copyfile(self.scratch / "now.png", self.out / "states" / f"{sid}.png")
        (self.out / "states" / f"{sid}.elements.json").write_text(json.dumps(obs.reply, indent=1, ensure_ascii=False))
        # an overlay in the parent's window leaves the parent's controls listed behind it, maybe moved: only the new
        # ones are its own (a control without words is told by its place too); a control something lies over is none
        keys = {c.key for c in before.cands} if box and before else set()
        named = {(c.tree_label, c.kind) for c in before.cands if c.tree_label and ob.overlaps(c.rect, box)} \
            if box and before else set()
        cands = [c for c in obs.cands if (box is None or (ob.inside(c.rect, box) and c.key not in keys
                                                          and (c.tree_label, c.kind) not in named))
                 and not ob.covered(c, obs.elements, self.device)]
        if home:
            return self.refresh(home, obs, cands)
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

    def keep_capture(self, s: Seen) -> None:
        """The capture a relaunch is about to replace stays, numbered, for the moves logged on it so far: the model
        resolves their taps against it."""
        n = len(s.replaced) + 1
        png, tree = f"states/{s.sid}.r{n}.png", f"states/{s.sid}.r{n}.elements.json"
        shutil.copyfile(self.out / "states" / f"{s.sid}.png", self.out / png)
        shutil.copyfile(self.out / "states" / f"{s.sid}.elements.json", self.out / tree)
        s.replaced.append(ReplacedCapture(until_step=self.step, screenshot=png, elements_reply=tree,
                                          icon_labels=list(s.icon_labels), vision_elements=list(s.vision)))

    def homelike(self, home: Seen, obs: Obs) -> bool:
        """What a relaunch's landing must show to be home: nothing the first launch's checks would block, no wall (an
        account or money) that home didn't already show in the same place, like a guest home's own "Log in", and the
        tab bar the first launch recorded, if it recorded one. Any other landing, such as a restored deeper screen, is
        recorded as its own state and back_to_root goes back from it."""
        new = [c for c in obs.cands if not any(h.label == c.label and h.kind == c.kind and ob.overlaps(h.rect, c.rect)
                                               for h in home.cands)]
        if self.blocked(obs) or ob.walled(new):
            return False
        return all(ob.find(obs.cands, t) for t in self.tabs)

    def refresh(self, home: Seen, obs: Obs, cands: list[ob.Candidate]) -> Seen:
        """A relaunch lands on its home screen by definition: one no recorded state matches is home with its list
        reloaded, re-recorded from this capture (the saved capture, its controls and their crops). A reload is no
        evidence of a region that moves on its own."""
        known = {c.key for c in home.cands}
        home.fp, home.fg, home.cands, home.elements = obs.fp, obs.fg, cands, obs.elements
        home.settled, home.settle_s, home.captured_at = obs.settled, obs.settle_s, now()
        home.upsell, home.priced = ob.is_upsell(obs.elements, self.device), ob.priced(obs.elements, self.device)
        home.visits, home.dynamic = home.visits + 1, []
        self.note("state", f"{home.sid} re-recorded: the relaunch landed on it with other content")
        self.name_icons(home)
        self.log_denied(home, [c for c in cands if c.key not in known])
        return home

    def away(self, obs: Obs) -> str | None:
        """Another app in front, or the screen turned sideways: no screen of the app to judge or act on."""
        if obs.fg != self.package:
            return "external"
        return "rotated" if obs.image.width > obs.image.height else None

    def kind_of(self, obs: Obs, before: Obs | None) -> tuple[str, Rect | None]:
        away = self.away(obs)
        if away:
            return away, None
        box = ob.dialog_box(obs.cands, self.device) or (
            ob.overlay_box(before.cands, obs.cands, self.device, frozenset(self.tab_keys()),
                           lambda box: ob.scrim(before.image, obs.image, box, self.device)) if before else None)
        return (ob.box_kind(box, self.device), box) if box else ("screen", None)

    def revisit(self, s: Seen, obs: Obs) -> None:
        s.visits += 1
        self.note_dynamic(s, obs.image)

    def note_dynamic(self, s: Seen, image: Image.Image) -> None:
        """What changed since the state's saved capture, with nothing tapped, is a region that moves on its own."""
        boxes = ob.changed_boxes(Image.open(self.out / "states" / f"{s.sid}.png"), image, self.device)
        for b in boxes or []:
            if not any(ob.inside(b, d) for d in s.dynamic):
                s.dynamic.append(b)

    def log_denied(self, s: Seen, cands: list[ob.Candidate] | None = None) -> None:
        for c in s.cands if cands is None else cands:
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

    def act(self, move: Move, purpose: str = "tour", watch=None, loop: int | None = None,
            expect: Seen | None = None) -> Seen:
        """Runs one move from the current state, observes, records where it landed, and logs the line. On a
        core-loop pass, loop is the pass number and the line records what stopped the loop, if anything. A hop of a
        route names the state it expects (expect): a landing the fingerprint doesn't match is judged by what it shows
        (invariant 1), and a recorded control the screen doesn't show is never tapped (invariant 3)."""
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
        if move.action == "back" and s.sid in self.exits:
            self.note("exit", f"BACK from {s.sid} left the app before: not taken again ({move.why})", outcome="blocked")
            return s
        if move.cand and move.action == "tap":
            reason = ob.denied(move.cand, upsell=s.upsell, core=purpose == "core", toggle_ok=purpose == "filter")
            if reason:
                self.log(s, None, move, move.cand, "unknown", f"denied: {reason}", "denied")
                s.tried.add(move.cand.key)
                return s
        live = ob.find(before.cands, move.cand) if move.cand else None
        shown = live is not None and self.shows(move.cand, live, before)
        if move.cand and not shown:
            self.counts["covered controls"] += live is not None
            self.log(s, None, move, move.cand, "unknown",
                     "control covered on the live screen" if live else "control not on the live screen", "error")
            if expect:
                self.counts["hops tried"] += 1
                self.landing = self.judge(expect)
                return s
            s.tried.add(move.cand.key)
            if move.cand.key in self.tab_keys():
                self.tab_to.setdefault(move.cand.key, "")
            return s
        outcome = "ok"
        try:
            self.perform(move, live, s.upsell, core=purpose == "core", toggle_ok=purpose == "filter")
        except McpTimeout as e:
            outcome = "timeout"
            self.hang(s, e)
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
        chatting = purpose == "core" and self.core.kind == "chat" and self.live_box() and not self.covering(before)
        to = s if chatting else self.land(obs, s, move, before, expect)
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
        if to.kind in AWAY and s.kind not in AWAY:
            self.left, self.left_back = s, move.action == "back"
            if self.left_back and to.fg != self.package:
                self.exits.add(s.sid)
        if move.cand and move.cand.key in self.tab_keys():
            self.tab_to.setdefault(move.cand.key, to.sid)
        self.current = to
        if to.fg in BILLING:
            back = Move("back", why="the store's billing screen")
            self.current = self.record(self.observe(), to, back, None)
            self.log(to, self.current, back, None, "back", "", "ok")
        return self.current

    def land(self, obs: Obs, s: Seen, move: Move, before: Obs, expect: Seen | None) -> Seen:
        """Where a move landed. For a hop, the expected state when the fingerprint matches or invariant 1 judges the
        screen the same place (the new capture is then not a new state); otherwise the recorded state it matches or a
        new one, with the model's one action toward the expected state kept for goto."""
        self.landing = None
        if expect is None:
            return self.record(obs, s, move, before)
        self.counts["hops tried"] += 1
        if ob.same_state(expect.fp, obs.fp) or self.arrived(expect):
            self.counts["hops arrived"] += 1
            self.counts["hops arrived by the model"] += self.landing is not None
            return expect
        return self.record(obs, s, move, before)

    def surface(self) -> list[ob.Candidate]:
        """The live controls of what is in front: on a sheet or modal only its own, re-found, never one behind it."""
        s = self.current
        if s.box is None:
            return self.obs.cands
        return [live for c in s.cands for live in [ob.find(self.obs.cands, c)] if live]

    def shows(self, cand: ob.Candidate, live: ob.Candidate, now: Obs) -> bool:
        """Invariant 3: a recorded control is tapped only when the screen shows it as it was recorded, compared with
        its crop in the capture it was recorded from. A control read off the live screen is what the screen shows,
        unless its tap point lies under the tab bar."""
        if self.under_tab_bar(cand, live, now):
            return False
        if any(c is cand for c in now.cands):
            return True
        owner = next((st for st in self.states if any(c is cand for c in st.cands)), None)
        if owner is None:
            return True
        then = Image.open(self.out / "states" / f"{owner.sid}.png")
        return ob.looks_same(then, cand.rect, now.image, live.rect, self.device)

    def under_tab_bar(self, cand: ob.Candidate, live: ob.Candidate, now: Obs) -> bool:
        """Content scrolls under the tab bar and stays in the element list: a tap point at or below the top of tabs the
        screen shows lands on a tab. A sheet over the tab bar hides the tabs, so its own controls there are shown."""
        if cand.key in self.tab_keys():
            return False
        shown = [lt.rect.y for tab in self.tabs for lt in [ob.find(now.cands, tab)] if lt and self.shows(tab, lt, now)]
        return bool(shown) and live.point[1] >= min(shown)

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

    def hang(self, s: Seen, e: Exception) -> None:
        """A device call failed on s: a timeout is a hang, any other failure a lost device. The second on one screen
        ends the tour as a device stop, so no done.json is written."""
        hung = isinstance(e, McpTimeout)
        self.note("hang" if hung else "device", f"{type(e).__name__}: {e}"[:200],
                  outcome="timeout" if hung else "error")
        self.hangs[s.sid] += 1
        if self.hangs[s.sid] >= 2:
            stop = f"second hang on {s.sid}" if hung else f"device error on {s.sid}: {type(e).__name__}: {e}"[:200]
            self.human("the device failed twice on one screen", stop)
            raise Stop(stop)

    def lost(self, phase: str, e: Exception) -> None:
        """The device failed after the tour: whatever the tour found, the run can't be reused as a finished explore."""
        self.core_results.append(f"{phase} stopped: device error: {type(e).__name__}: {e}"[:200])
        self.stop_reason = f"device error in {phase} (tour: {self.stop_reason}): {type(e).__name__}: {e}"[:200]
        self.note(phase, self.stop_reason, outcome="error")
        self.human("the device failed after the tour", self.stop_reason)

    def human(self, what: str, why: str) -> None:
        needs_human(self.ctx.run_dir, "explore", what, why, ["trace.jsonl", "explore/actions.jsonl"], rerun(self.ctx))

    # ---------- launching ----------

    def relaunch(self, first: bool = False, why: str = "") -> None:
        """Terminate, launch, settle, record and dismiss launch dialogs, then re-apply the content filter. After the
        first launch, where it lands is home: the launch screen, or the filtered root once the filter is re-applied.
        A screen the tour recorded (the fingerprint matches) is a deeper screen the app restored, and back_to_root
        goes back from there; any other screen is home with its list reloaded, re-recorded (refresh). An away screen
        is left first (leave), and a relaunch leave() needs replaces the rest of this one. The filter is re-applied
        from the launch screen only: a relaunch that stops short of it walks a recorded route there, or relaunches
        once more, counted."""
        if not first:
            self.count_relaunch(why)
        self.phone.terminate()
        self.phone.launch()
        self.home = None if first else self.launch_root
        try:
            self.wait_for_app(self.home)
            self.current = None
            self.normalize()
            home = self.record(self.obs, None, None, None)
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
                if self.current.kind in AWAY:
                    count = self.relaunches
                    self.leave()
                    if self.relaunches > count:
                        return
                if self.filter_taps and not self.walk_home():
                    self.relaunch(why=f"the content filter is re-applied on the launch screen, and no recorded way "
                                      f"led there from {self.current.sid}")
                    return
                self.home = self.root
            self.apply_filter(first)
        finally:
            self.home = None
        self.segments.append([])

    def count_relaunch(self, why: str) -> None:
        cap = MAX_RELAUNCHES + (0 if self.touring else CORE_RELAUNCHES)
        if self.relaunches >= cap:
            self.human(f"the explorer needed relaunch {cap + 1}", f"{cap} relaunches used; the next was for: {why}")
            raise Stop("relaunch cap")
        self.relaunch_reasons.append(why)
        self.relaunches += 1
        if self.current:
            self.log(self.current, self.root, Move("relaunch", why=why), None, "unknown", "", "ok")

    def reopen(self, dialog: Seen) -> bool:
        """A relaunch that stops at a launch dialog instead of dismissing it, to follow its call to action."""
        self.count_relaunch(f"reopen the launch dialog {dialog.sid}")
        self.phone.terminate()
        self.phone.launch()
        self.wait_for_app(dialog)
        self.current = self.record(self.obs, None, None, None)
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
            dialog = self.record(self.obs, None, None, None)
            dialog.done, dialog.depth, dialog.launch = "a launch dialog, dismissed", 0, True
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
        shows the bottom tabs is a top screen already, and BACK there would leave the app; so would BACK from a
        screen it already left the app from."""
        for _ in range(4):
            here = self.current
            if here is self.launch_root or here.kind in AWAY or self.shows_tabs(here) or here.sid in self.exits:
                return
            self.act(Move("back", why="relaunch landed off the launch screen"), purpose="setup")

    def walk_home(self) -> bool:
        """A recorded route to the launch screen from where back_to_root stopped (BACK there would leave the app)."""
        for move, expected in self.route(self.current, self.launch_root) or []:
            self.act(move, purpose="setup")
            if self.current is not expected:
                break
        return self.current is self.launch_root

    # ---------- the content filter ----------

    def apply_filter(self, first: bool) -> None:
        if first:
            self.filter_taps = self.find_filter()
            if self.filter_taps and self.current is not self.root:
                self.root.done = "the unfiltered launch screen"
                self.root, self.current.depth, self.current.back_to = self.current, 0, None
        else:
            for n, tap in enumerate(self.filter_taps):
                if self.filter_set(n):
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
        taps, at = [pick], home
        if pick.checked is None and not self.stands_out(pick):
            opened = self.act(Move("tap", pick, decider="jev", why="content filter"), purpose="filter")
            if opened is not home and opened.kind in ("modal", "sheet"):
                option = self.pick(opened, [c for c in opened.cands if not ob.denied(c, toggle_ok=True)],
                                   FILTER_MENU_QUESTION, "filter.menu")
                if option:
                    if option.checked is None:
                        self.act(Move("tap", option, decider="jev", why="content filter option"), purpose="filter")
                    taps.append(option)
                    at = opened
        if taps[-1].checked is not None:
            self.filter_on = self.reach(at, taps[-1])
        self.note("filter", f"content filter: {filter_label(taps, self.filter_on)}", decider="jev")
        return taps

    def reach(self, s: Seen, switch: ob.Candidate) -> bool:
        """A switch is a state to reach, not a tap (a tap flips it, whatever the app shipped): Jev names the
        restrictive state, and the switch is tapped only when it shows the other one. With no answer it stays as
        the app ships it."""
        name = switch.label[:60]
        try:
            result = decide.choose(self.trace_path, "explore", "filter.state", self.describe(s), FILTER_STATE_QUESTION,
                                   [f"{name} on", f"{name} off"], **self.jev_options())
        except decide.JevFailed:
            self.note("filter.state", f"no answer on {name!r}'s restrictive state; left as the app ships it")
            return switch.checked
        want = decide.index_of(result.option_id) == 0
        if want != switch.checked:
            self.act(Move("tap", switch, decider="jev", why=f"content filter: turn {name!r} {'on' if want else 'off'}"),
                     purpose="filter")
        return want

    def filter_set(self, n: int) -> bool:
        """The filter's n-th control needs no tap now: it is the last one and already shows what the filter wants
        (a switch in its restrictive state, a chip that stands out as selected), or it is a switch the screen doesn't
        show, whose state can't be read, so a tap would be blind."""
        if n != len(self.filter_taps) - 1:
            return False
        tap = self.filter_taps[-1]
        if self.filter_on is None:
            return self.stands_out(tap)
        live = ob.find(self.obs.cands, tap)
        return live is None or live.checked == self.filter_on

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
        live = ob.find(self.obs.cands, last)
        ok = (live.checked == self.filter_on if self.filter_on is not None else self.stands_out(last)) if live \
            else last.tree_label in ob.texts(self.obs.elements, self.device)
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
        """An untried tab the screen shows (invariant 3): a sheet or drawer over the tab bar hides them all."""
        return next((t for t in self.tabs if t.key not in self.tab_to
                     for live in [ob.find(self.obs.cands, t)] if live and self.shows(t, live, self.obs)), None)

    def options(self, s: Seen) -> list[ob.Candidate]:
        filter_row = self.filter_row(s.cands)
        return [c for c in s.cands if c.key not in s.tried and c.key not in self.tab_keys() and c.key not in filter_row
                and not ob.denied(c, upsell=s.upsell) and (c.key not in s.waiting or s.visits > s.waiting[c.key])]

    def filter_row(self, cands: list[ob.Candidate]) -> set[str]:
        """The content filter's controls among a screen's controls: the opener, and every option in the chosen one's
        row. Neither the tour nor a model-named tap touches them, so nothing undoes the filter."""
        keys = {t.key for t in self.filter_taps}
        chosen = ob.find(cands, self.filter_taps[-1]) if self.filter_taps else None
        if chosen:
            cy = ob.center(chosen.rect)[1]
            keys |= {c.key for c in cands
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

    def model_done(self, s: Seen, rule: str) -> None:
        """The model may end a screen only with the code's own evidence that it is spent: no untried option, its tap
        cap used, or the run of taps that changed nothing which made the code ask. Otherwise its done or back is one
        move, and the ranked options go on."""
        if not self.options(s) or s.taps >= TAPS_PER_STATE or s.noop_run >= NOOPS_BEFORE_SONNET:
            s.done = rule
        else:
            self.counts["model done refused while options were left"] += 1

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
        """Walks recorded moves to the target. A hop that lands elsewhere, or can't tap a control the screen doesn't
        show, is judged by what the screen shows (invariant 1): one action away takes that action and looks once
        more; another app or a sideways screen is left first (leave); anywhere else re-plans a route from where it
        is, without the hops that already failed. Only a place with no route on relaunches, once."""
        relaunched, failed = False, set()
        for _ in range(GOTO_ROUNDS):
            if self.current is target:
                return True
            if self.current.kind in AWAY:
                count = self.relaunches
                self.leave()
                relaunched |= self.relaunches > count
                continue
            hops = self.route(self.current, target, failed)
            if hops is None:
                if relaunched:
                    return False
                relaunched = True
                self.relaunch(why=f"no recorded way from {self.current.sid} to {target.sid}")
                continue
            for move, expected in hops:
                here = self.current
                self.act(move, purpose="nav", expect=expected)
                if self.current is not expected and not self.take_landing(expected):
                    failed.add(hop_key(here, move))
                    break
        return self.current is target

    def take_landing(self, expected: Seen) -> bool:
        """After a hop missed: the judged screen may be the expected place after all, or one action from it."""
        landing = self.landing
        if landing and landing.arrived:
            self.current = expected
            self.counts["hops arrived"] += 1
            self.counts["hops arrived by the model"] += 1
        elif landing and landing.move:
            self.act(landing.move, purpose="nav", expect=expected)
        return self.current is expected

    def shows_tabs(self, s: Seen) -> bool:
        return any(ob.find(s.cands, t) for t in self.tabs)

    def route(self, src: Seen, dst: Seen, failed: set = frozenset()) -> list[tuple[Move, Seen]] | None:
        """Recorded moves from src to dst, never into or out of an away screen (BACK there walks another app) and
        never a BACK from a screen BACK already left the app from."""
        came = {src.sid: None}
        queue = deque([src] if src.kind not in AWAY else [])
        while queue:
            x = queue.popleft()
            if x is dst:
                break
            for move, y in self.links(x):
                if y.sid not in came and y.kind not in (*AWAY, "blocked") and hop_key(x, move) not in failed \
                        and not (move.action == "back" and x.sid in self.exits):
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

    # ---------- arrival (invariant 1) ----------

    def goal_of(self, target: Seen) -> str:
        """The target in words, from the run's own data: its title and the control that first led there, or the
        core action Jev chose when the target is where it happens."""
        if self.core and target is self.core.state:
            return f"the screen where the app's core action happens: {self.core.name}"
        via = f", first reached by tapping {target.via[:60]!r}" if target.via else ""
        return f"screen {target.sid}, a {target.kind} titled {title_of(target.elements, self.device)!r}{via}"

    def arrived(self, target: Seen, step: str = "arrival") -> bool:
        """The screen now is the target: the fingerprint says so for free, or else invariant 1's judgment does."""
        if self.obs is not None and ob.same_state(self.obs.fp, target.fp):
            self.landing = None
            return True
        self.landing = self.judge(target, step)
        return self.landing.arrived

    def judge(self, target: Seen, step: str = "arrival") -> Landing:
        """What the screen shows, judged against the recorded target by two signals of different kinds: the model
        (both screenshots and the goal in words) and the element lists' overlap. Arrival needs both. When they
        disagree it is not arrival; the model's one action or a re-plan follows, and the trace says so. An away screen
        is never the target and gets no judgment, so no action of the model's runs in another app: leave() comes
        first."""
        obs = self.obs
        if self.away(obs):
            return Landing(False, None, "away", 0.0)
        structure = ob.structure(target.elements, obs.elements, self.device, target.dynamic)
        row = self.filter_row(obs.cands)
        cands = [c for c in obs.cands if c.key not in row]
        controls, ids = listed(cands, self.device)
        title = title_of(target.elements, self.device)
        text = (f"Goal: {self.goal_of(target)}\nThe target's identifying text: "
                f"{title!r}\nControls on the current screen (image 2):\n{controls}")
        self.counts["arrival shots"] += 1
        shot = self.out / "arrival" / f"{self.counts['arrival shots']:03d}.png"
        shot.parent.mkdir(exist_ok=True)
        obs.image.save(shot)
        pngs = [png_half(content(Image.open(self.out / "states" / f"{target.sid}.png"), self.device)),
                png_half(content(obs.image, self.device))]
        try:
            answer = self.ask("arrival", f"{step}.{target.sid}", text, pngs, Arrival)
        except llm.LLMFailure as e:
            self.note(f"{step}.{target.sid}", f"arrival call failed: {e}", outcome="error")
            return Landing(False, None, "failed", structure)
        # a text the element list lacks was read off the wrong screen
        read = ob.read_on_screen(obs.elements, answer.identifying_text, title, self.device)
        model_same, structure_same = answer.verdict == "same" and read, structure >= ob.STRUCTURE_SAME
        arrived = model_same and structure_same
        move = None if arrived else self.step_toward(answer, cands, ids)
        if step == "arrival":
            self.counts[f"model {answer.verdict}"] += 1
            self.counts["side effects"] += answer.side_effect
            if answer.verdict == "same" and not read:
                self.counts["model same without its text on screen"] += 1
            if model_same and not structure_same:
                self.counts["structure vetoed a model same"] += 1
            if structure_same and not model_same:
                self.counts["structure same, model not"] += 1
        disagree = " (the signals disagree)" if model_same != structure_same else ""
        if answer.verdict == "same" and not read:
            disagree += f" (it read {answer.identifying_text[:40]!r}, which the element list doesn't show)"
        self.note(f"{step}.{target.sid}", f"{'arrived at' if arrived else 'not at'} {target.sid}: model {answer.verdict} "
                                          f"{answer.confidence:.2f}, structure {structure:.2f}{disagree}; "
                                          f"target explore/states/{target.sid}.png, now explore/arrival/{shot.name}; "
                                          f"{answer.reason}", decider="model")
        return Landing(arrived, move, answer.verdict, structure)

    def step_toward(self, answer: Arrival, cands: list[ob.Candidate], ids: list[str]) -> Move | None:
        why = f"arrival: {answer.reason}"[:80]
        if answer.verdict != "one_action":
            return None
        if answer.action == "tap" and answer.element_id in ids:
            return Move("tap", cands[ids.index(answer.element_id)], decider="model", why=why)
        if answer.action in ("back", "swipe"):
            return Move(answer.action, direction=answer.direction or "up", decider="model", why=why)
        return None

    def leave(self) -> None:
        """Back to the app from an away screen. Another app in front gets a launch, never BACK, which would walk that
        app's own history: a live task comes back as it was left (measured on the emulator), which is a return, not a
        relaunch. The app anywhere but where it was left is a relaunch, counted, and so is the launch screen after a
        BACK out of it: that BACK can end the task, and a fresh start lands there too. A launch can't displace a window
        in the app's own task (a system dialog over it), so the same foreign screen still in front gets BACK, as a
        screen turned sideways does; any other foreign screen is a relaunch. A relaunch too if BACK doesn't come
        back."""
        if self.obs is None:
            self.resync()
            if self.current.kind not in AWAY:
                return
        away = self.current
        if self.obs.fg != self.package:
            self.phone.launch()
            obs = self.observe()
            if obs.fg == self.package:
                restarted = self.left_back and self.left is self.launch_root
                if self.left and not restarted and ob.same_state(obs.fp, self.left.fp):
                    self.resume(away)
                else:
                    self.relaunch(why=f"a launch from {away.fg} did not find the app where it was left")
                return
            if not ob.same_state(obs.fp, away.fp):
                self.relaunch(why=f"a launch from {away.fg} left {obs.fg} in front")
                return
        self.act(Move("back", why=f"return from the {away.kind} screen"), purpose="nav")
        if self.current.kind in AWAY:
            self.relaunch(why=f"BACK did not return from the {away.kind} screen ({self.obs.fg})")

    def resume(self, away: Seen) -> None:
        """The app is back where it was left: the path goes on from there, without the moves made since it left."""
        self.returns.append(f"from {away.fg} back to {self.left.sid}")
        self.log(away, self.left, Move("launch", why="bring the app back"), None, "unknown", "", "ok")
        self.revisit(self.left, self.obs)
        self.current = self.left
        segment = self.segments[-1] if self.segments else []
        while segment and segment[-1][2] != self.left.sid:
            segment.pop()

    def read_upsell(self) -> None:
        """Scrolls an upsell to its end so every benefit and price is captured verbatim; never taps inside it."""
        for _ in range(UPSELL_SWIPES):
            s = self.current
            if not s.upsell or s.swipes >= 1 or s.kind in AWAY:
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
                if self.current.kind in AWAY:
                    self.leave()
                    continue
                target = self.next_target()
                if target is None:
                    raise Stop("nothing left to try")
                if not self.goto(target):
                    target.done = "no way back to it"
                    continue
                move = self.next_move(target)
                if move is None:
                    target.done = "every option tried or its cap spent"
                    continue
                if move.action == "done":
                    self.model_done(target, "the model said done")
                    continue
                seen = len(self.states)
                self.act(move)
                if move.decider == "model" and move.action == "back":
                    self.model_done(target, "the model went back")
                if len(self.states) > seen and self.current.upsell:
                    self.read_upsell()
            except NeedRelaunch as e:
                self.note("relaunch", str(e))
                self.relaunch(why=str(e))
            except DEVICE_LOST as e:
                self.hang(self.current, e)

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
        best = priced or max((s for s in self.states if s.upsell and not s.launch and s.kind not in AWAY),
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
        if self.current.kind in AWAY:
            self.leave()
        elif self.current.upsell and not self.current.launch:
            self.read_upsell()
            self.act(Move("back", why="out of the upsell"), purpose="nav")

    def priced_paywall(self) -> Seen | None:
        return next((s for s in self.states if s.priced and not s.launch and s.kind in ("screen", "modal", "sheet")),
                    None)

    def core_options(self) -> list[CoreAction]:
        """What a user might come to do, for Jev to choose from. A conversation inside a feed item comes first (it
        is reached the way a user starts one, by a path that replays), but a chat the tour saw stays a choice: which
        text box is the conversation with the app's AI is Jev's call."""
        feed = self.feed_option()
        try:
            inside = self.walk_to_input(feed) if feed else None
        except Stop as e:
            self.note("core", f"the walk into an item stopped: {e}", outcome="error")
            inside = None
        options = [inside] if inside else []
        seen = next(((s, found) for s in self.states if s.kind == "screen"
                     for found in [ob.composer(s.cands, self.device)] if found), None)
        if seen:
            s, found = seen
            options.append(CoreAction("chat", s, list(found), f"send messages in a conversation and read the replies "
                                                              f"({self.chat_title(s)!r}; text box + send on {s.sid})"))
        options += [feed] if feed else []
        return (options + self.button_options())[:CORE_OPTIONS]

    def button_options(self) -> list[CoreAction]:
        """The short-label buttons on the main screens (the launch screen and each tab), the biggest first, so the
        app's own word for what it does can be the core action; they fill every option slot the other options leave,
        and Jev judges which, if any."""
        main = {self.root.sid, *self.tab_to.values()}
        found = {}
        for s in self.states:
            if s.sid in main and s.kind == "screen":
                exclude = self.tab_keys() | self.filter_row(s.cands)
                for c in ob.short_buttons(s.cands, self.device, exclude, title_of(s.elements, self.device)):
                    found.setdefault(c.label, (s, c))
        best = sorted(found.values(), key=lambda sc: -ob.area(sc[1].rect))
        return [CoreAction("action", s, [c], f"tap {c.label[:40]!r} again and again on {s.sid}") for s, c in best]

    def feed_option(self) -> CoreAction | None:
        main = {self.root.sid, *self.tab_to.values()}
        feeds = [(s, ob.feed_items(s.cands, self.device, self.tab_keys())) for s in self.states if s.sid in main]
        feeds = [(s, items) for s, items in feeds if items]
        if not feeds:
            return None
        s, items = max(feeds, key=lambda f: len(f[1]))
        return CoreAction("feed", s, items, f"open and read items from the list on {s.sid} "
                                            f"(e.g. {items[0].label[:40]!r})")

    def input_action(self, cands: list[ob.Candidate], upsell: bool) -> ob.Candidate | None:
        """A button that makes new content on each tap (Play, Generate, Draw, Spin ...): at most three words, so
        a sentence that starts with "Create" is not one."""
        return next((c for c in cands if c.tree_label and len(c.label.split()) <= 3 and ob.CREATE.match(c.label)
                     and c.key not in self.tab_keys() and not ob.denied(c, upsell=upsell)), None)

    def choose_core(self) -> list[CoreAction]:
        """Jev names the core action among what the tour saw, or none of them. Its other choices of the same kind
        follow in its own order, for when the one it named can't be reached again: a feed answer never becomes a
        chat."""
        options = self.core_options()
        if not options:
            return []
        evidence = {o.state.sid: self.evidence(o) for o in options}
        rest = [self.describe(s) for s in self.states if s.kind == "screen" and s.sid not in evidence]
        seen = "; ".join([*evidence.values(), *rest])[:3000]
        try:
            result = decide.choose(self.trace_path, "explore", "core", seen, CORE_QUESTION,
                                   [o.name for o in options] + [NO_CORE], **self.jev_options())
        except decide.JevFailed:
            return [o for o in options if o.kind == "chat"] or options[:1]
        first, *rest = decide.ordered(result, list(range(len(options) + 1)))
        if first == len(options):
            return []
        pick = options[first]
        return [pick, *(options[i] for i in rest if i < len(options) and options[i].kind == pick.kind)]

    def evidence(self, o: CoreAction) -> str:
        """What Jev sees of an option's own screen. A conversation is told by its title and its last messages, so a
        chat with the app's AI reads differently from a comment thread or a message to another person."""
        if o.kind != "chat":
            return self.describe(o.state)
        box = o.controls[0]
        said = [t for y, t in texts_by_y(o.state.elements, self.device) if ob.TOP_CHROME_BOTTOM_PX <= y < box.rect.y][-3:]
        return (f"screen {o.state.sid} is a conversation titled {self.chat_title(o.state)!r}; its last messages: "
                + (" | ".join(t[:100] for t in said) or "none yet"))

    def chat_title(self, s: Seen) -> str:
        return title_of(s.elements, self.device)

    def walk_to_input(self, feed: CoreAction) -> CoreAction | None:
        """Opening an item is a step, not the core action: goes into the first item and takes its main action
        (Chat, Start ...) up to WALK_STEPS times, until a text box with send or a play/generate button shows. What it
        finds is one more option for Jev, never a replacement for Jev's answer: a text box inside an item may be a
        comment box or a message to another person. One item's page can lack the action the others have, so up to
        WALK_ITEMS items are tried."""
        for n in range(WALK_ITEMS):
            if n and self.current.kind == "screen" and self.current is not feed.state:
                self.act(Move("back", why="core loop: back to the list for the next item"), purpose="nav")
            found = self.walk_into(feed, n)
            if found:
                return found
        return None

    def rows(self, feed: CoreAction) -> list[ob.Candidate]:
        """The list's rows as its state holds them now: the core action's rows it still holds, or, once a relaunch
        re-recorded home without them, the rows it holds instead. Only the state's own candidates, so every tap is
        crop-checked against its capture (invariant 3); none left ends the walk or the feed pass."""
        held = {c.key: c for c in feed.state.cands}
        return [held[c.key] for c in feed.controls if c.key in held] or ob.feed_items(feed.state.cands, self.device,
                                                                                     self.tab_keys())

    def walk_into(self, feed: CoreAction, n: int) -> CoreAction | None:
        """Invariant 4: on the n-th item's page, a conversation (text box + send) or a play/generate button ends the
        walk at once. Otherwise the model says whether the control that starts the core action is on screen; the walk
        taps it (at most WALK_STEPS times) or scrolls on, until the page stops moving or WALK_SCROLLS. An item that
        opens a sheet or modal is walked the same way, on that surface's own controls."""
        if not self.goto(feed.state):
            return None
        items = self.rows(feed)
        if n >= len(items):
            if not items:
                self.note("walk", f"no recorded rows left on {feed.state.sid}: the walk ends")
            return None
        item = items[n]
        self.act(Move("tap", item, why="core loop: look inside an item"), purpose="nav")
        if self.current is feed.state:
            return None
        tapped, scrolls, still = set(), 0, 0
        while self.current.kind in ("screen", "modal", "sheet") and self.obs is not None:
            here = self.current
            # the chat loop finds its text box on the whole screen, so a box on an overlay could be one behind it
            chat = self.live_composer() if here.kind == "screen" else None
            if chat:
                return CoreAction("chat", here, list(chat), f"open an item and send messages in its conversation "
                                                            f"({self.chat_title(here)!r}; text box + send inside "
                                                            f"the item, {here.sid})")
            action = self.input_action(self.surface(), here.upsell)
            if action:
                return CoreAction("action", here, [action], f"open an item and tap {action.label[:40]!r} inside it "
                                                            f"again and again ({here.sid})")
            start = self.start_control(item, tapped) if len(tapped) < WALK_STEPS else None
            if start:
                tapped.add(start.key)
                self.act(Move("tap", start, decider="model", why="core loop: the item's main action"), purpose="nav")
                continue
            if scrolls >= WALK_SCROLLS or still >= 2:
                break
            scrolls, shown, laid = scrolls + 1, ob.texts(self.obs.elements, self.device), self.obs.elements
            self.act(Move("swipe", direction="up", why="core loop: the item's main action may be further down"),
                     purpose="nav")
            # a scroll moves the elements; an ad, a GIF or a video playing in place is not movement, however large
            moved = self.obs is None or ob.texts(self.obs.elements, self.device) != shown \
                or ob.shifted(laid, self.obs.elements, self.device)
            still = 0 if moved else still + 1
        self.note("core", f"no input control inside {item.label[:40]!r}")
        return None

    def start_control(self, item: ob.Candidate, tapped: set[str]) -> ob.Candidate | None:
        """The model names the control on this screen that starts the core action for the item, if one is visible.
        It counts only when it is a listed control, named with confidence, and still shown on a fresh look
        (invariant 3); anything less keeps the walk going."""
        before, here = self.obs, self.current
        row = self.filter_row(before.cands)
        cands = [c for c in self.surface() if c.key not in tapped and c.key not in self.tab_keys() and c.key not in row
                 and not ob.denied(c, upsell=here.upsell)]
        if not cands:
            return None
        controls, ids = listed(cands, self.device)
        text = (f"Goal: {CORE_QUESTION} Find the control on this page that starts it for the item "
                f"{item.label[:60]!r}.\nControls on this page:\n{controls}")
        try:
            pick = self.ask("walk", f"walk.{here.sid}", text, self.boxed_png(here, cands, ids, before.image),
                            WalkPick)
        except llm.LLMFailure as e:
            self.note(f"walk.{here.sid}", f"walk call failed: {e}", outcome="error")
            return None
        named = cands[ids.index(pick.element_id)] if pick.on_screen and pick.element_id in ids else None
        self.note(f"walk.{here.sid}", f"{'on screen: ' + repr(named.label[:40]) if named else 'not on screen'} "
                                      f"({pick.confidence:.2f}): {pick.reason}", decider="model")
        if named is None or pick.confidence < WALK_SURE:
            return None
        fresh = self.observe()
        live = ob.find(fresh.cands, named)
        if live is None or not ob.looks_same(before.image, named.rect, fresh.image, live.rect, self.device):
            self.counts["walk picks the screen no longer shows"] += 1
            return None
        return live

    def core_loop(self) -> None:
        self.touring = False
        if not self.may_send:
            self.core_results.append("off (--no-send)")
            return
        ranked = self.choose_core()
        if not ranked:
            self.core_results.append("no core action found on the screens seen")
            return
        for choice in ranked:
            self.core = choice
            self.note("core", f"core action: {self.core.name}", decider="jev")
            if self.at_core(1):
                break
            self.core_results.append(f"could not get to {choice.state.sid}; Jev's next choice of the same kind "
                                     f"follows, if any")
        else:
            return
        deadline = self.clock() + self.core_reps * CORE_SECONDS_PER_REP
        for n in range(1, self.core_reps + 1):
            if self.clock() > deadline:
                self.core_results.append("stopped: out of time")
                return
            try:
                if n > 1 and not self.at_core(n):
                    self.core_results.append(f"pass {n}: could not get back to {self.core.state.sid}")
                    return
                if self.core.kind == "feed" and not self.rows(self.core):
                    self.note("core", f"no recorded rows left on {self.core.state.sid}: the feed pass ends")
                    self.core_results.append(f"pass {n}: no recorded rows left on {self.core.state.sid}")
                    return
                result, seen, hit = self.core_once(n)
            except DEVICE_ERRORS as e:
                self.core_results.append(f"pass {n}: {type(e).__name__}: {e}"[:200])
                if isinstance(e, DEVICE_LOST):
                    raise
                return
            self.core_results.append(f"pass {n}: {result or 'no measurement'}")
            self.core_completed += bool(seen or hit)
            if hit:
                self.core_hit = f"{hit} on pass {n}"
                if self.current.upsell:
                    self.read_upsell()
                return

    def at_core(self, n: int) -> bool:
        """Invariant 1 at the core screen: the screen as it is now is judged against the core state (a conversation
        grows, so after a pass it rarely matches its first capture). One action away takes it; anywhere else walks
        there from the state the screen really is."""
        if self.obs is None:
            self.resync()
        if self.arrived(self.core.state):
            self.current = self.core.state
            return True
        if self.current is self.core.state:
            self.current = self.record(self.obs, None, None, None)
        return self.take_landing(self.core.state) or self.goto(self.core.state)

    def core_once(self, n: int) -> tuple[str, bool, str]:
        """One pass of the core action. Returns its measurement, whether its result showed, and what stopped the
        loop, if anything."""
        core, before = self.core, ob.texts(self.obs.elements, self.device)
        self.last_summary, self.last_seen = "", False
        if core.kind == "chat":
            message = CORE_MESSAGES[(n - 1) % len(CORE_MESSAGES)]
            box, idle = self.live_box() or core.controls[0], self.live_composer()
            idle = (self.obs.image, idle[1]) if idle else None
            self.act(Move("tap", box, why="core loop: focus the text box"), purpose="core", loop=n)
            if not self.stop_kind:
                self.act(Move("type", text=message, why="core loop: type"), purpose="core", loop=n)
            if not self.stop_kind:
                _, send = self.live_composer()
                self.act(Move("tap", send, why="core loop: send"), purpose="core", loop=n,
                         watch=lambda: self.watch(before | {message}, "reply", idle))
            return self.last_summary, self.last_seen, self.stop_text()
        controls = self.rows(core) if core.kind == "feed" else core.controls
        control = controls[(n - 1) % len(controls)]
        verb = "load" if core.kind == "feed" else "result"
        self.note_dynamic(core.state, self.obs.image)  # an ad that moved since the state was saved isn't a result
        self.act(Move("tap", control, why=f"core loop: {core.kind}"), purpose="core", loop=n,
                 watch=lambda: self.watch(before, verb, dynamic=core.state.dynamic))
        if not self.stop_kind:
            if self.current.kind in AWAY:
                self.leave()
            elif self.current is not core.state:
                self.act(Move("back", why="core loop: back"), purpose="core", loop=n)
        return self.last_summary, self.last_seen, self.stop_text()

    def stop_text(self) -> str:
        return f"{self.stop_kind} ({self.stop_evidence})" if self.stop_kind else ""

    def live_composer(self) -> tuple[ob.Candidate, ob.Candidate] | None:
        """The text box and its send control on the screen as it is now."""
        return ob.composer(self.obs.cands, self.device) if self.live_box() else None

    def live_box(self) -> ob.Candidate | None:
        """The chat's text box on the screen as it is now, whatever shows in the send slot (a reply still being
        written puts a stop control there)."""
        if self.obs is None or self.obs.fg != self.package or ob.dialog_box(self.obs.cands, self.device):
            return None
        return self.lower_box(self.obs.cands)

    def lower_box(self, cands: list[ob.Candidate]) -> ob.Candidate | None:
        middle = (self.device.content_top_px + self.device.content_bottom_px) / 2
        return next((c for c in cands if c.kind == "EditText" and ob.center(c.rect)[1] > middle), None)

    def covering(self, before: Obs) -> list[ob.Candidate]:
        """A sheet in the chat's own window leaves the composer in the tree: it shows as a second text box in the
        lower half, or as new controls lying over the text box (bubbles and hints never do, nor a send control
        relabeled in place while a reply is written)."""
        box, middle = self.live_box(), (self.device.content_top_px + self.device.content_bottom_px) / 2
        if box is None:
            return []
        old, spots = {(c.label, c.kind) for c in before.cands}, [c.rect for c in before.cands]
        return [c for c in self.obs.cands if c is not box and (
            (c.kind == "EditText" and ob.center(c.rect)[1] > middle)
            or ((c.label, c.kind) not in old and c.rect not in spots and ob.overlaps(c.rect, box.rect)
                and not ob.inside(c.rect, box.rect)))]

    def within(self, s: Seen) -> list[dict]:
        return [e for e in s.elements if s.box is None or ob.inside(ob.rect(e), s.box)]

    def sheet_words(self, before: Obs) -> str:
        """What a sheet over the composer says: the new words reaching down where the text box was. The
        conversation above it, the new reply included, is content and never names a stop."""
        box, old = self.lower_box(before.cands), ob.texts(before.elements, self.device)
        floor = box.rect.y if box else (self.device.content_top_px + self.device.content_bottom_px) / 2
        return self.named([e for e in self.obs.elements if e["coordinates"]["y"] + e["coordinates"]["height"] > floor
                           and ob.words(e) not in old])

    def named(self, elements: list[dict]) -> str:
        """A dialog's or sheet's own words, never the conversation's: a price makes it a paywall, limit words a
        limit."""
        texts = ob.texts(elements, self.device)
        return ("paywall" if any(ob.PRICE.search(t) for t in texts) else
                "limit" if any(ob.LIMIT.search(t) for t in texts) else "")

    def watch(self, before: set[str], verb: str, idle: tuple[Image.Image, ob.Candidate] | None = None,
              dynamic: list[Rect] = ()) -> str:
        """Invariant 2: after the core action the explorer stays until the screen settles: two screenshots
        SETTLE_GAP_S apart that match, once the result has begun to show (new text, or for an action that isn't a
        conversation, pixels that changed since the first look) and, in a conversation, once the send control
        looks again as it did before the message (idle: that screenshot and control; a stop control in its place
        means the reply is still coming). While the screen keeps changing, the model is asked every SETTLE_ASK_S
        whether the work is still progressing, finished (only decorative motion is left), or stalled, up to the
        SETTLE_CAP_S outer cap; its finished counts only once the send control is idle again. The new text's timing is
        read from the element list on the way."""
        start, samples, first, last, asks, how = self.clock(), [], None, None, 0, "cap"
        while True:
            reply, elements = self.phone.elements()
            at = self.clock() - start
            samples.append((at, frozenset(ob.texts(elements, self.device) - before)))
            frame = self.frame(reply)
            first = first or frame
            # a reply shows as text; another result (an image, a board) may show only as pixels
            begun = ob.reply_timing(samples)[0] is not None \
                or (idle is None and not ob.still(first, frame, self.device))
            if last is not None and begun and ob.still(last, frame, self.device) \
                    and self.idle_again(idle, elements, frame):
                how = "settled"
                break
            if last is not None and at >= min(SETTLE_CAP_S, (asks + 1) * SETTLE_ASK_S):
                asks += 1
                how = self.progress(last, frame, at)
                if how == "finished" and not self.idle_again(idle, elements, frame):
                    self.counts["model finished while send still busy"] += 1
                    how = "progressing"
                if how != "progressing" or at >= SETTLE_CAP_S:
                    break
            last = frame
            self.sleep(max(0.0, SETTLE_GAP_S - (self.clock() - start - at)))
        seconds = self.clock() - start
        self.settles.append((round(seconds, 1), how))
        self.note("settle", f"{verb}: {how} after {seconds:.0f} s, {asks} model checks")
        self.last_summary = ob.timing_line(verb, samples, seconds)
        # a conversation's result is the reply's text; any other result must show outside what moves on its own
        self.last_seen = ob.reply_timing(samples)[0] is not None if idle else \
            how != "stalled" and self.result_showed(before, elements, (first, last, frame), dynamic)
        return self.last_summary

    def result_showed(self, before: set[str], elements: list[dict], looks: tuple[Image.Image, Image.Image, Image.Image],
                      dynamic: list[Rect]) -> bool:
        """Whether an action that isn't a conversation showed a result: new text, or pixels changed since the first
        look, outside what moves on its own (the core state's recorded dynamic regions, and whatever still moved
        between the last two looks: an ad, a GIF, a spinner). Most of the screen changing and then holding still is
        a new page."""
        first, last, frame = looks
        moving = ob.changed_boxes(last, frame, self.device)
        if moving is None:  # most of the screen still moving at the end: nothing held still to count
            return False
        changed = ob.changed_boxes(first, frame, self.device)
        own = [*dynamic, *moving]
        fresh = [ob.rect(e) for e in elements if ob.in_content(e, self.device) and ob.words(e)
                 and ob.words(e) not in before]
        # ponytail: pixels are compared from the first look after the tap, so a pixel-only result drawn before it
        # (with no new text) isn't seen; pass the pre-tap capture in if that shows up
        return changed is None or any(not any(ob.overlaps(b, m) for m in own) for b in [*changed, *fresh])

    def idle_again(self, idle: tuple[Image.Image, ob.Candidate] | None, elements: list[dict],
                   frame: Image.Image) -> bool:
        if idle is None:
            return True
        live = ob.find(ob.controls(elements, self.device), idle[1])
        return live is not None and ob.looks_same(idle[0], idle[1].rect, frame, live.rect, self.device)

    def frame(self, reply: dict) -> Image.Image:
        """A full screenshot for the settle check, redacted with the element list read just before it."""
        shot = self.phone.screenshot(self.scratch / "frame.png", (self.device.w_px, self.device.h_px))
        image = Image.open(shot).convert("RGB")
        ob.redact(reply, image, self.secrets)
        return image

    def progress(self, before: Image.Image, now: Image.Image, waited: float) -> str:
        text = (f"The action: one pass of the app's core action ({self.core.name}). It is {waited:.0f} s since the "
                f"action, and the screen still changes between screenshots {SETTLE_GAP_S:.0f} s apart.")
        try:
            answer = self.ask("settle", "settle", text, [png_half(content(before, self.device)),
                                                         png_half(content(now, self.device))], Progress)
        except llm.LLMFailure as e:
            self.note("settle", f"progress call failed: {e}", outcome="error")
            return "progressing"
        self.note("settle", f"{answer.verdict} after {waited:.0f} s: {answer.reason}", decider="model")
        return answer.verdict

    def hit(self, s: Seen, here: Seen, before: Obs, move: Move) -> tuple[str, str]:
        """What a core-loop move brought up that ends the loop, read from what changed on screen and never from
        words in content. A few words for loop_stop, and the evidence."""
        if here.kind == "external":
            if self.obs.fg in BILLING:
                return "billing", self.obs.fg
            return ("left the app", self.obs.fg) if self.core.kind == "chat" else ("", "")
        if here.kind == "rotated":
            return "screen rotated", here.sid
        if self.core.kind == "chat":
            return self.chat_stop(here, before, move)
        if here is not s and here.kind in ("modal", "sheet"):
            wall = ob.walled(ob.controls(self.within(here), self.device))
            named = self.named(self.within(here)) or {"account": "sign-in wall",
                                                      "money": "paywall" if here.priced else "upsell"}.get(wall, "")
            # a sheet the action opened shows its result (an item's page) unless it names a price, a limit, an account
            # or money
            if named or here.kind == "modal":
                return named or "modal opened", here.sid
        if here is not s and here.upsell:
            return ("paywall" if here.priced else "upsell screen"), here.sid
        moved = ob.counters(before.elements, self.obs.elements, self.device, [(0, ob.TOP_CHROME_BOTTOM_PX)])
        return ("counter", moved[0]) if moved else ("", "")

    def chat_stop(self, here: Seen, before: Obs, move: Move) -> tuple[str, str]:
        """In a chat only the window can stop the loop: a dialog over it, the text box disabled or gone, send still
        disabled once a message is typed, or a counter moving beside the composer. The conversation's own text,
        prices and timestamps included, never does."""
        if here.kind in ("modal", "sheet"):
            window = ob.dialog_box(self.obs.cands, self.device)
            return self.named(self.within(here) if window else self.sheet_words(before)) or "dialog opened", here.sid
        if self.covering(before):
            return self.sheet_words(before) or "sheet opened", here.sid
        box = next((c for c in self.obs.cands if c.kind == "EditText"), None)
        if box is None:
            return self.sheet_words(before) or "input gone", here.sid
        live = self.live_composer()
        if not box.enabled or (move.action == "type" and (live is None or not live[1].enabled)):
            return "input disabled", here.sid
        band = (int(box.rect.y) - COMPOSER_BAND_PX, int(box.rect.y + box.rect.h) + COMPOSER_BAND_PX)
        moved = ob.counters(before.elements, self.obs.elements, self.device, [band])
        return ("counter", moved[0]) if moved else ("", "")

    # ---------- the replay check ----------

    def verify_replay(self) -> None:
        """Replays each recorded segment from a fresh launch and counts moves that reach the recorded screen. Same
        screen is decided as everywhere else (invariant 1: fingerprint, else the model and the structure agree); that
        is the merge gate's number, and the fingerprint-exact count stands beside it. A control the screen doesn't
        show is never tapped (invariant 3)."""
        deadline = self.clock() + REPLAY_MINUTES * 60
        matched = meant = total = 0
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
                    if move.cand and (live is None or not self.shows(move.cand, live, self.obs)):
                        self.note("replay.miss", f"{move.action} {move.cand.label[:30]!r} expected on the way to "
                                                 f"{to_sid}: control {'covered' if live else 'not'} on the live "
                                                 f"screen", outcome="error")
                        break
                    if move.action == "tap" and not self.safe_tap(live, "replay"):
                        break
                    if move.action != "tap":
                        self.perform(move, live)
                    seen = self.observe().fp
                    if self.escape_billing():
                        self.observe()
                    if ob.same_state(seen, self.by_id[to_sid].fp):
                        matched += 1
                        meant += 1
                    elif self.judge(self.by_id[to_sid], "replay").arrived:
                        meant += 1
                    else:
                        near = next((s.sid for s in self.states if ob.same_state(s.fp, seen)), "a new screen")
                        self.note("replay.miss", f"{move.action} {move.cand.label[:30] if move.cand else ''} "
                                                 f"expected {to_sid}, reached {near}", outcome="error")
            except NeedRelaunch as e:
                self.note("replay", f"segment stopped: {e}", outcome="error")
        self.replay, self.replay_fingerprint = (meant, total), (matched, total)
        self.note("replay", f"{meant}/{total} replayed moves reached the recorded screen, {matched}/{total} by "
                            f"fingerprint alone")

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
            if live and not self.filter_set(n) and self.safe_tap(live, "replay"):
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
        return label_of(c, self.device)

    def ask(self, prompt: str, step: str, text: str, pngs: bytes | list[bytes], schema):
        role = config.roles(self.ctx.profile)["explore_vision"]
        images = [{"type": "image", "png": png} for png in ([pngs] if isinstance(pngs, bytes) else pngs)]
        try:
            parsed, _ = llm.call(trace_path=self.trace_path, stage="explore", step=step, model=role["model"],
                                 effort=role.get("effort"), system=(PROMPTS / f"{prompt}.md").read_text(),
                                 messages=[{"role": "user", "content": [*images, {"type": "text", "text": text}]}],
                                 max_tokens=config.max_tokens(role), budget=self.budget, schema=schema,
                                 no_cache=self.ctx.no_cache, replay=self.ctx.replay, cache_dir=self.cache_dir)
        except llm.LLMFailure:
            self.counts["model call failures"] += 1
            raise
        return parsed

    def boxed_png(self, s: Seen, cands: list[ob.Candidate], names: list[str], image: Image.Image | None = None) -> bytes:
        """The screen with each control outlined and labeled; s's first capture unless another image is given."""
        top = self.device.content_top_px
        image = content(image or Image.open(self.out / "states" / f"{s.sid}.png"), self.device)
        draw, font = ImageDraw.Draw(image), ImageFont.load_default(size=36)
        for c, name in zip(cands, names, strict=True):
            r = c.rect
            draw.rectangle((r.x, r.y - top, r.x + r.w, r.y - top + r.h), outline=(255, 0, 0), width=4)
            draw.rectangle((r.x, r.y - top, r.x + 22 * len(name) + 8, r.y - top + 40), fill=(255, 0, 0))
            draw.text((r.x + 4, r.y - top), name, fill=(255, 255, 255), font=font)
        return png_half(image)

    def name_icons(self, s: Seen) -> None:
        """The Sonnet icon pass: names boxes with no words and adds visible controls the tree doesn't list."""
        unnamed = [n for n, c in enumerate(s.cands, start=1) if not c.label]
        png = self.boxed_png(s, s.cands, [str(n) for n in range(1, len(s.cands) + 1)])
        text = (f"Name these boxes: {', '.join(map(str, unnamed)) or 'none'}.\n"
                f"The image is {int(self.device.w_px * ICON_SCALE)} px wide.")
        try:
            result = self.ask("icons", f"icons.{s.sid}", text, png, IconPass)
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
            answer = self.ask("hard_screen", f"hard.{s.sid}", text, png, HardScreenAction)
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
            "limit": self.core_hit.startswith(LIMIT_STOPS),
        }
        return [i for i in items if answered.get(i)], [i for i in items if not answered.get(i)]

    def write(self, app_version: str | None) -> None:
        for s in self.states:
            state_file = StateFile(
                state_id=s.sid, kind=s.kind, parent_id=s.parent, fingerprint=str(s.fp), foreground_package=s.fg,
                screenshot=f"states/{s.sid}.png", elements_reply=f"states/{s.sid}.elements.json", settled=s.settled,
                settle_seconds=s.settle_s, dynamic_regions=s.dynamic, captured_at=s.captured_at, box=s.box,
                icon_labels=s.icon_labels, vision_elements=s.vision, blocked_reason=s.blocked_reason,
                replaced=s.replaced)
            (self.out / "states" / f"{s.sid}.json").write_text(state_file.model_dump_json(indent=1))
        answered, still_open = self.checklist()
        explore = ExploreFile(
            app_package=self.package, app_version=app_version, budget=self.ctx.budget, relaunches=self.relaunches,
            content_filter=filter_label(self.filter_taps, self.filter_on) or None,
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
             f"- Returns to the app as it was left, by a launch (not relaunches): {len(ex.returns)}",
             *[f"  - {n}: {where}" for n, where in enumerate(ex.returns, start=1)],
             f"- Paywall or plans screen captured: {paywall_line(ex)}",
             f"- Content filter: {filter_label(ex.filter_taps, ex.filter_on) or 'none found'}"]
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
    attempted = sum(r.startswith("pass ") for r in ex.core_results)
    lines += ["## Core loop (the free experience)", "",
              f"Core action: {ex.core.name if ex.core else 'none'}; {attempted} of {ex.core_reps} passes attempted, "
              f"{ex.core_completed} completed.", ""]
    lines += [f"- {r}" for r in ex.core_results]
    lines += [f"- {loop_end(ex, attempted)}", ""]
    meant, total = ex.replay
    matched = ex.replay_fingerprint[0]
    lines += ["## Replay check", "", f"{meant}/{total} replayed moves reached the recorded screen"
              + (f" ({100 * meant / total:.0f}%; same screen as the explorer decides it everywhere: the fingerprint, "
                 f"else the model and the structure agree). Fingerprint alone: {matched}/{total} "
                 f"({100 * matched / total:.0f}%)." if total else "."), "",
              "## Invariants", "", *[f"- {line}" for line in counters(ex)], "",
              "## States", "", "| id | kind | over | depth | untried | ended by | first words |",
              "|---|---|---|---|---|---|---|"]
    for s in ex.states:
        words = next((c.tree_label for c in s.cands if c.tree_label), "")[:50].replace("|", "/")
        untried = len(ex.options(s)) if s.kind in ("screen", "modal", "sheet") else ""
        lines.append(f"| {s.sid} | {s.kind} | {s.parent or ''} | {s.depth} | {untried} | {ended(ex, s)} | {words} |")
    return "\n".join(lines) + "\n"


def loop_end(ex: Explorer, attempted: int) -> str:
    """Why the core loop ended. Only the app's own stop says anything about a limit: a loop cut short by the device or
    the explorer saw no limit only on the passes it finished."""
    if ex.core_hit:
        return f"Stopped by the app: {ex.core_hit}."
    if ex.core_completed == ex.core_reps:
        return f"All {ex.core_reps} passes ran and met no limit."
    if attempted:
        return (f"Not stopped by the app, but only {ex.core_completed} of {ex.core_reps} passes completed (last: "
                f"{ex.core_results[-1]}): no limit showed on those, which doesn't show the app has none.")
    return "No pass ran, so nothing is known about a limit."


def counters(ex: Explorer) -> list[str]:
    """What the four invariants did in this run, in numbers another run can be compared on."""
    c, waits = ex.counts, ex.settles
    how = Counter(h for _, h in waits)
    return [f"hops: {c['hops arrived']}/{c['hops tried']} arrived ({c['hops arrived by the model']} judged by what the "
            f"screen shows)",
            f"model arrival verdicts: {c['model same']} same, {c['model one_action']} one action, "
            f"{c['model elsewhere']} elsewhere; the structure vetoed a model same {c['structure vetoed a model same']} "
            f"times, and said same where the model did not {c['structure same, model not']} times; a model same "
            f"whose identifying text the element list lacks: {c['model same without its text on screen']}",
            f"covered controls not tapped: {c['covered controls']}",
            f"side-effect actions the model saw: {c['side effects']}",
            f"settle waits: {len(waits)} ({', '.join(f'{n} {h}' for h, n in sorted(how.items())) or 'none'}), "
            f"seconds {', '.join(str(sec) for sec, _ in waits) or '-'}",
            f"model finished verdicts refused while send still looked busy: "
            f"{c['model finished while send still busy']}",
            f"walk picks the screen no longer showed: {c['walk picks the screen no longer shows']}",
            f"model done or back refused while the screen had untried options: "
            f"{c['model done refused while options were left']}",
            f"model calls that failed (each traced where it happened): {c['model call failures']}"]


def ended(ex: Explorer, s: Seen) -> str:
    """The rule that ended a screen's exploration."""
    if s.done or s.kind not in ("screen", "modal", "sheet"):
        return s.done
    return "had work left when the tour stopped" if ex.has_work(s) else "every option tried or its cap spent"


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


def rerun(ctx: Ctx) -> str:
    """What to run after a needs-human: explore has no resume, so the same command explores again from the start."""
    return f"{rerun_command('explore', ctx)}  # re-runs explore; it starts over"


def run(ctx: Ctx) -> StageOutcome:
    if ctx.replay:
        raise llm.ReplayMiss("explore drives the device; --replay reuses a finished explore/ folder")
    if not redact_list():
        needs_human(ctx.run_dir, "explore", "SIMULA_REDACT is empty",
                    "the explorer saves screenshots and element lists, and nothing would hide the account handle",
                    [".env", ".env.example"], rerun(ctx))
        raise ExploreFailed("SIMULA_REDACT is empty: list the emulator account's handle and names in .env, "
                            "comma-separated, then explore again")
    with contextlib.ExitStack() as held:
        # the device and its lock first: a run that can't explore leaves the last explore, and its record, as they were
        try:
            serial = resolve_serial(ctx.device)
            held.enter_context(emulator_lock(serial, wait_s=LOCK_WAIT_S))
            avd = adb_shell(serial, ["getprop", "ro.boot.qemu.avd_name"]) or None
            refuse_twins(serial, avd)
        except (SystemExit, TimeoutError) as e:
            raise NotStarted(str(e)) from None
        signal.signal(signal.SIGTERM, lambda *_: sys.exit("explore stopped by SIGTERM"))
        out = ctx.run_dir / "explore"
        shutil.rmtree(out, ignore_errors=True)
        (out / ".scratch").mkdir(parents=True)
        server = Server(cwd=out)
        try:
            ex = Explorer(ctx, Phone(server, ctx.app["package"], out / ".scratch", serial, avd), out)
            ex.serial = serial
            return explore_app(ex)
        finally:
            server.close()


def refuse_twins(serial: str, avd: str | None) -> None:
    """mobile-mcp names an emulator by its AVD, so another emulator of the same AVD could answer in its place."""
    tool = adb()
    twins = [s for s in (online(tool) if tool and avd else []) if s != serial
             and adb_shell(s, ["getprop", "ro.boot.qemu.avd_name"]) == avd]
    if twins:
        raise SystemExit(f"{serial} and {', '.join(twins)} run the same AVD {avd!r}, which mobile-mcp can't tell "
                         "apart: stop one of them")


def explore_app(ex: Explorer) -> StageOutcome:
    """Every phase's failure is logged; whatever was captured is written, even when one ends the process."""
    app_version = None
    try:
        ex.measure_device()
        app_version = adb_value(ex.serial, ["dumpsys", "package", ex.package], "versionName=")
        run_trace(ex.ctx.run_dir, stage="explore", step="device", decider="code",
                  note=f"serial {ex.serial}, mobile-mcp device {ex.phone.device}")
        run_tour(ex)
        if ex.root and not ex.stop_reason.startswith(("blocked root", *DEVICE_STOPS)):
            for phase in (ex.paywall_pass, ex.core_loop, ex.verify_replay):
                try:
                    phase()
                except DEVICE_LOST as e:
                    ex.lost(phase.__name__, e)
                    break
                except (Stop, llm.CapReached, NeedRelaunch) as e:
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
        run_trace(ex.ctx.run_dir, stage="explore", step="counters", decider="code",
                  note=json.dumps({**ex.counts, "settles": ex.settles, "replay_fingerprint": ex.replay_fingerprint})[:3000])
    if not ex.states or ex.stop_reason.startswith(DEVICE_STOPS):
        raise ExploreFailed(ex.stop_reason or "no state was recorded")
    return outcome(ex)


def outcome(ex: Explorer) -> StageOutcome:
    """Partial when the explore stopped short of what it set out to do: the relaunch cap ended the tour or a phase
    after it, the paywall pass or the replay check stopped early, or the core loop finished fewer passes than planned
    and the app didn't stop it (--no-send turns the loop off on purpose). Complete otherwise."""
    reasons = []
    if ex.stop_reason == "relaunch cap" or any("relaunch cap" in r for r in ex.core_results):
        reasons.append(f"it used all {ex.relaunches} relaunches allowed ({MAX_RELAUNCHES} in the tour, "
                       f"{CORE_RELAUNCHES} after it), so it stopped before seeing everything it planned to")
    for phase, name in (("paywall_pass", "the paywall pass"), ("verify_replay", "the replay check")):
        reasons += [f"{name} stopped early ({r.split(': ', 1)[1]})" for r in ex.core_results
                    if r.startswith(f"{phase} stopped: ") and "relaunch cap" not in r]
    if ex.may_send and not ex.core_hit and ex.core_completed < ex.core_reps:
        last = ex.core_results[-1] if ex.core_results else f"the tour ended: {ex.stop_reason}"
        reasons.append(f"the core loop completed {ex.core_completed} of {ex.core_reps} passes (last: {last})")
    return StageOutcome(status="partial", reasons=reasons, resume=rerun(ex.ctx)) if reasons else StageOutcome()


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
