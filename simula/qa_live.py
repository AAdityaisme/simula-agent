"""`simula qa-live`: an opt-in audit, outside QA's rounds and never part of approval. It walks each core flow of a run
on the live app and on the approved mock side by side, one recorded action at a time, and saves fresh paired evidence
at every checkpoint, so a mock that breaks is told apart from an app that changed since explore. No model calls."""

import json
import shutil
import signal
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from simula import config, qa_metrics, render, runfolder
from simula.contracts import SCHEMA_VERSION, Device, Edge, ProductModel, Rect, StateFile
from simula.device import observe as ob
from simula.device.devices import emulator_lock, resolve_serial
from simula.device.mcp import McpReplyError, McpTimeout, Phone, Server, parse_elements
from simula.runlog import now
from simula.stages import Ctx, explore, mock, qa

MAX_ACTIONS = 30
MAX_MINUTES = 10
ROUTE_HOPS = 4
TAKEN = ("tap", "back", "swipe")
AWAY = ("external", "rotated", "blocked")
DEVICE_ERRORS = (McpTimeout, McpReplyError)
STATUSES = ("matched", "mock_failed", "real_diverged", "blocked", "unsupported", "unverified")
COMPLETED = ("matched", "mock_failed", "real_diverged")
ARRIVED = ("same", "structure", "home")
DIVERGED = ("other", "away")
INPUTS = ("model/product_model.json", "mock/contract_report.json", "qa/approved/index.html")
STATE_JS = "() => window.simula.state()"
NOTE = ("Masked SSIM is descriptive: it has no pass threshold. Explore records no swipe direction: a swipe back to "
        "where an earlier recorded swipe started is its scroll back, taken down; any other is taken up, flagged as "
        "assumed, and a landing elsewhere after it is unverified. Setup (launches and routes to a flow's start) is "
        "never evidence, and a flow not walked here is not certified by it.")


@dataclass
class Live:
    """One settled look at the device, redacted before anything reads it."""
    reply: dict
    elements: list[dict]
    image: Image.Image
    fg: str
    fp: ob.Fingerprint
    cands: list[ob.Candidate]
    settled: bool


@dataclass
class Recorded:
    """A state as explore captured it, with the box of its own overlay when it is a modal or sheet."""
    elements: list[dict]
    image: Image.Image
    cands: list[ob.Candidate]
    box: Rect | None


class FlowEnd(Exception):
    """The flow stops here, with this status."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status


class Stopped(Exception):
    """The audit stops: a cap, or the device failed while setting up."""


def run(app: str, run_id: str | None, out: Path, serial: str | None, clock=time.monotonic,
        sleep=time.sleep) -> dict:
    """Walks every core flow of a run on the live app and its approved mock, and writes OUT/report.json, report.md and
    each checkpoint's evidence. Reads the run, never writes to it."""
    src = runfolder.resolve_run(app, run_id)
    secrets = explore.redact_list()
    if not secrets:
        raise SystemExit("SIMULA_REDACT is empty: list the emulator account's handle and names in .env first")
    if not (src / "qa" / "approved" / "index.html").exists():
        raise SystemExit(f"{src} has no approved mock: run `simula qa {app}` first")
    if not approved_current(src):
        raise SystemExit(f"the approved mock predates the current model/mock: rerun `simula qa {app}`")
    model = ProductModel.model_validate_json((src / "model" / "product_model.json").read_text())
    if model.device != Device():
        raise SystemExit(f"the run was explored on {model.device}, but the mock renders and compares on {Device()}")
    out = new_out(out, src)
    ctx = Ctx(app=config.app_config(app), run_dir=src, profile="real", no_cache=False, replay=False, usd_cap=None,
              allow_fixtures=False)
    scratch = out / ".scratch"
    scratch.mkdir()
    previous = signal.signal(signal.SIGTERM, lambda *_: sys.exit("qa-live stopped by SIGTERM"))
    try:
        with held_device(serial, ctx.app["package"], scratch) as (phone, resolved):
            found = live_device(phone, resolved)
            if found != model.device:
                raise SystemExit(f"the device is {found}, but the run was explored on {model.device}")
            audit = Audit(ctx, model, phone, out, secrets, clock, sleep)
            with render.open_mock(src / "qa" / "approved") as (page, _):
                flows, stop = audit.walk(page)
        report = {"schema_version": SCHEMA_VERSION, "app": app, "run": src.name, "created_at": now(),
                  "inputs": {path: runfolder.sha256(src / path) for path in INPUTS},
                  "device": model.device.model_dump(), "caps": {"actions": MAX_ACTIONS, "minutes": MAX_MINUTES},
                  "actions": {"total": audit.actions, "setup": len(audit.setup)},
                  "minutes": round((clock() - audit.started) / 60, 2), "stop": stop, "summary": summary(flows),
                  "flows": flows, "setup": audit.setup, "note": NOTE}
        write_report(out, report)
        return report
    finally:
        signal.signal(signal.SIGTERM, previous)  # a stop runs this cleanup and releases the device first
        shutil.rmtree(scratch, ignore_errors=True)
        if not any(out.iterdir()):
            out.rmdir()  # a walk that stopped before any evidence leaves no folder, so the same --out can run again


def approved_current(src: Path) -> bool:
    """QA approved this page from the model and mock on disk now: its marker is complete, and the hashes it recorded
    for them (inputs) and for the page (an output) are the files' own. A model or mock rerun alone leaves qa/ as it
    was."""
    marker = runfolder.read_done(src / "qa")
    if marker is None or marker.outcome.status != "complete":
        return False
    recorded = {h.path: h.sha256 for h in [*marker.input_hashes, *marker.output_hashes]}
    return all((src / path).exists() and recorded.get(path) == runfolder.sha256(src / path) for path in INPUTS)


def new_out(out: Path, src: Path) -> Path:
    """A new folder outside the source run, judged on resolved paths so a symlink can't lead back into it."""
    out = out.resolve()
    if out.is_relative_to(src):
        raise SystemExit(f"--out {out} is inside the run {src}: the audit never writes to the run it reads")
    if out.exists():
        raise SystemExit(f"--out {out} already exists: give a new folder")
    out.mkdir(parents=True)
    return out


@contextmanager
def held_device(flag: str | None, package: str, scratch: Path):
    """The device under its lock, held as explore holds it: one explore or walk per emulator, released on every exit."""
    serial = resolve_serial(flag)
    with emulator_lock(serial, wait_s=explore.LOCK_WAIT_S):
        avd = explore.adb_shell(serial, ["getprop", "ro.boot.qemu.avd_name"]) or None
        explore.refuse_twins(serial, avd)
        server = Server(cwd=scratch)
        try:
            yield Phone(server, package, scratch, serial, avd), serial
        finally:
            server.close()


def live_device(phone, serial: str) -> Device:
    """The device's size, density and insets, read the way explore reads them, before anything acts."""
    w, h = phone.screen_size()
    _, elements = phone.elements()
    density = explore.adb_value(serial, ["wm", "density"], "density:")
    return ob.device_from(elements, w, h, int(density) if density and density.isdigit() else Device().density)


def fingerprint(text: str) -> ob.Fingerprint:
    package, top, content, skeleton = text.rsplit("|", 3)
    return ob.Fingerprint(package, int(top, 16), int(content, 16), skeleton)


def matches(cands: list[ob.Candidate], want: ob.Candidate) -> list[ob.Candidate]:
    """Every live control explore's re-find rule (observe.find) would take for want, nearest first."""
    found, rest = [], cands
    while (c := ob.find(rest, want)) is not None:
        found.append(c)
        rest = [o for o in rest if o is not c]
    return found


def launchable(live: Live, device: Device) -> bool:
    """explore's launch screen: the app in front with two labeled controls or a tab bar; a logo alone is a splash."""
    return sum(bool(c.tree_label) for c in live.cands) >= 2 or bool(ob.tab_bar(live.cands, device))


class Audit:
    """One walk over a run's flows. `current` is the recorded state the live app is known to show, or None."""

    def __init__(self, ctx: Ctx, model: ProductModel, phone, out: Path, secrets: list[str], clock, sleep):
        self.ctx, self.model, self.phone, self.out, self.secrets = ctx, model, phone, out, secrets
        self.clock, self.sleep, self.started = clock, sleep, clock()
        self.device, self.package, self.scratch = model.device, ctx.app["package"], out / ".scratch"
        self.states = {s.id: s for s in model.states}
        self.edges = {e.id: e for e in model.edges}
        self.fps = {s.id: fingerprint(s.fingerprint) for s in model.states}
        self.root = next(s.id for s in model.states if s.kind == "screen")  # the model stage's root
        self.in_scope = {e.id for e in mock.scope_edges(model, qa.mock_screens(ctx, model))}
        self.undrawn = set(qa.undrawn_screens(ctx))
        # explore records no swipe direction: a swipe back to where an earlier recorded swipe started is its scroll
        # back, which it swipes down; any other is taken up, as an assumption
        self.scroll_backs = {e.id for n, e in enumerate(model.edges) if e.action == "swipe" and any(
            o.action == "swipe" and (o.from_state, o.to_state) == (e.to_state, e.from_state) for o in model.edges[:n])}
        self.recorded_states: dict[str, Recorded] = {}
        self.actions, self.setup, self.flow = 0, [], ""
        self.current: str | None = None
        self.live: Live | None = None
        self.verdict: dict = {}

    # ---------- the flows ----------

    def walk(self, page) -> tuple[list[dict], str | None]:
        """Every flow in the model's order, each with its status and why. Returns them and what stopped the audit."""
        gestures = page.evaluate(qa.GESTURE_MAP)
        results, stop = [], None
        for flow in self.model.flows:
            self.flow = flow.id
            problem = self.problem(flow.edge_ids)
            result = {"flow": flow.id, "name": flow.name, "edges": flow.edge_ids, "supported": problem is None,
                      "status": "unsupported", "reason": problem, "checkpoints": [],
                      "assumed": [f"{i}: swipe direction assumed up" for i in flow.edge_ids
                                  if i in self.edges and self.assumed(self.edges[i])]}
            if problem is None and stop:
                result.update(status="blocked", reason=f"not visited: {stop}")
            elif problem is None:
                try:
                    self.walk_flow([self.edges[i] for i in flow.edge_ids], page, gestures, result)
                except Stopped as e:
                    stop = str(e)
                    result.update(status="blocked", reason=stop)
            results.append(result)
        return results, stop

    def problem(self, edge_ids: list[str]) -> str | None:
        """Why the walker can't take a flow, read off the model and the mock before any action."""
        if not edge_ids:
            return "the flow has no recorded edges"
        for i in edge_ids:
            edge = self.edges.get(i)
            if edge is None:
                return f"{i} is not a recorded edge"
            if edge.action == "type":
                return f"{i}: typing is not supported"
            if i not in self.in_scope:
                return f"{i} leaves the mock's scope"
            if {edge.from_state, edge.to_state} & self.undrawn:
                return f"{i} passes a screen the mock didn't draw"
            if not self.takeable(edge):
                return f"{i}: no recorded control with a live identity to tap (only the vision pass saw it)"
        return None

    def takeable(self, edge: Edge) -> bool:
        if edge.action != "tap":
            return edge.action in TAKEN
        element = next((e for e in self.states[edge.from_state].elements if e.id == edge.element_id), None)
        return element is not None and element.mcp_ref is not None

    def assumed(self, edge: Edge) -> bool:
        return edge.action == "swipe" and edge.id not in self.scroll_backs

    def walk_flow(self, hops: list[Edge], page, gestures: dict, result: dict) -> None:
        """Puts both sides on the flow's verified start, then takes each hop on the live app and on the mock, captures
        both and pairs them, and stops at the first hop where either side goes wrong."""
        start = hops[0].from_state
        try:
            self.reach(start)
        except FlowEnd as e:
            result.update(status="blocked", reason=f"setup: {e}")
            return
        except DEVICE_ERRORS as e:
            self.current = None
            result.update(status="blocked", reason=f"setup: the device failed ({type(e).__name__}); not retried")
            return
        page.evaluate("id => window.simula.go(id)", start)
        self.checkpoint(result, None, start, page, {"landed": start, "problem": None, "gesture": False}, None)
        for edge in hops:
            try:
                target = self.safe_target(edge) if edge.action == "tap" else None
                control = self.control(page, edge, target)
                self.act(edge, target)
                self.look(edge.to_state)
            except FlowEnd as e:
                result.update(status=e.status, reason=f"{edge.id}: {e}")
                return
            except DEVICE_ERRORS as e:
                self.current = None
                result.update(status="blocked", reason=f"{edge.id}: the device failed ({type(e).__name__}); "
                                                       "not retried")
                return
            problem, gesture = qa.take(page, edge, gestures)
            mock_side = {"landed": page.evaluate(STATE_JS), "problem": problem, "gesture": gesture}
            self.checkpoint(result, edge, edge.to_state, page, mock_side, control)
            ended = self.hop_end(edge, problem)
            if ended:
                result.update(status=ended[0], reason=f"{edge.id}: {ended[1]}")
                return
        result.update(status="matched", reason=None)

    def hop_end(self, edge: Edge, problem: str | None) -> tuple[str, str] | None:
        """A hop's verdict from both sides: the live app's landing decides first, since a mock can't be judged against
        a path the app no longer takes. An unknown landing is attributed to neither side, nor is one after a swipe
        whose direction the walker assumed: the app can't be blamed for the walker's guess."""
        v = self.verdict
        if v["verdict"] in ARRIVED:
            return ("mock_failed", f"mock: {problem}") if problem else None
        if v["verdict"] in DIVERGED:
            where = v["landed"] or f"another app ({v['foreground']})"
            went = f"the app went to {where}, not {v['expected']} (structure {v['structure']:.2f})"
            return ("unverified", f"{went}, after a swipe whose direction is assumed up") if self.assumed(edge) \
                else ("real_diverged", went)
        why = "the screen never settled" if v["verdict"] == "unsettled" else "the screen matches no recorded state"
        return "unverified", f"{why} (structure {v['structure']:.2f} against {v['expected']})"

    # ---------- setup: never evidence ----------

    def reach(self, start: str) -> None:
        """Puts the live app on a flow's start over recorded edges: from where it is, or else from a fresh launch."""
        if self.current is None or self.route(self.current, start) is None:
            self.launch(start)
        if self.current is None:
            raise FlowEnd("blocked", f"the launch landed on a screen no recorded state matches "
                                     f"({self.verdict['verdict']})")
        hops = self.route(self.current, start)
        if hops is None:
            raise FlowEnd("blocked", f"no recorded route of at most {ROUTE_HOPS} hops from {self.current} to {start}")
        for edge in hops:
            target = self.safe_target(edge) if edge.action == "tap" else None
            self.act(edge, target)
            self.look(edge.to_state)
            self.setup.append({"flow": self.flow, "action": edge.action, "edge": edge.id, **self.verdict})
            if self.current != edge.to_state:
                raise FlowEnd("blocked", f"the route to {start} went wrong at {edge.id} ({self.verdict['verdict']})")

    def launch(self, want: str) -> None:
        """Terminate, then launch, neither ever retried (the first call may have landed), then name where the app
        landed: a launch does not imply home."""
        self.current = None
        for name, call in (("terminate", self.phone.terminate), ("launch", self.phone.launch)):
            try:
                self.mutate(call, retry=False)
            except DEVICE_ERRORS as e:
                raise Stopped(f"the device failed to {name} the app ({type(e).__name__}); not retried") from None
            self.setup.append({"flow": self.flow, "action": name})
        try:
            self.live = self.wait_for_launch(want)
        except DEVICE_ERRORS as e:
            raise Stopped(f"the device failed after the launch ({type(e).__name__}); not launched again") from None
        self.verdict = self.match_state(self.live, want, launched=True)
        self.current = self.verdict["landed"]
        self.setup[-1].update(self.verdict)

    def wait_for_launch(self, want: str) -> Live:
        """Looks as explore waits for a relaunch (Explorer.wait_for_app): up to SPLASH_WAIT_S for a launch screen, then
        up to LAUNCH_WAIT_S for want's fingerprint or a dialog, since a feed can sit on loading placeholders for many
        seconds. Before the splash deadline, a dump that fails is only "not yet"."""
        splash = self.clock() + explore.SPLASH_WAIT_S
        live = self.look_once(splash)
        while live is None or not (live.fg == self.package and launchable(live, self.device)) \
                and self.clock() < splash:
            self.sleep(1.5)
            live = self.look_once(splash)
        deadline = self.clock() + explore.LAUNCH_WAIT_S
        while (live.fg == self.package and not ob.same_state(self.fps[want], live.fp)
               and not ob.dialog_box(live.cands, self.device) and self.clock() < deadline):
            self.sleep(1.5)
            live = self.look_once(splash) or live
        return live

    def look_once(self, deadline: float) -> Live | None:
        self.in_time()
        try:
            return self.capture()
        except DEVICE_ERRORS:
            if self.clock() >= deadline:
                raise
            return None

    def route(self, src: str, dst: str) -> list[Edge] | None:
        """The shortest path of recorded edges the walker can take, at most ROUTE_HOPS long, never through a state
        outside the app nor a tap whose recorded control is on the deny-list (the live one is checked again)."""
        came: dict[str, Edge | None] = {src: None}
        frontier = {src}
        for _ in range(ROUTE_HOPS):
            reached = set()
            for e in self.model.edges:
                if (e.from_state in frontier and e.to_state not in came and self.takeable(e)
                        and self.states[e.to_state].kind not in AWAY and (e.action != "tap" or self.allowed(e))):
                    came[e.to_state] = e
                    reached.add(e.to_state)
            frontier = reached
        if dst not in came:
            return None
        hops = []
        while came[dst]:
            hops.append(came[dst])
            dst = came[dst].from_state
        return hops[::-1]

    # ---------- the live side ----------

    def mutate(self, call, *args, **kwargs) -> None:
        """Every device action goes through here, setup included: none starts past a cap."""
        if self.actions >= MAX_ACTIONS:
            raise Stopped(f"the action cap ({MAX_ACTIONS} device actions) was reached")
        self.in_time()
        self.actions += 1
        call(*args, **kwargs)

    def in_time(self) -> None:
        """Checked before every action, every look and every checkpoint, so no wait or capture runs on past the cap
        unreported."""
        if self.clock() - self.started >= MAX_MINUTES * 60:
            raise Stopped(f"the time cap ({MAX_MINUTES} minutes) was reached")

    def act(self, edge: Edge, target: ob.Candidate | None) -> None:
        if self.live.fg != self.package:
            raise FlowEnd("blocked", f"{self.live.fg} is in front, not the app")
        if edge.action == "tap":
            self.mutate(self.phone.tap, *target.point)
        elif edge.action == "back":
            self.mutate(self.phone.back)
        else:
            self.mutate(self.phone.swipe, "down" if edge.id in self.scroll_backs else "up")

    def capture(self) -> Live:
        """A settled element list and screenshot, both redacted as explore redacts them before anything reads them."""
        settled = ob.settle(self.phone.elements, self.phone.small_hash, self.device, self.clock, self.sleep)
        path = self.phone.screenshot(self.scratch / "now.png", (self.device.w_px, self.device.h_px))
        image = Image.open(path).convert("RGB")
        reply, elements, hits = ob.redact(settled.reply, image, self.secrets)
        if hits or not settled.ok:
            ob.redact(self.phone.elements()[0], image, self.secrets)  # the screen may have moved since the list
        image.save(path)  # no raw capture stays on disk, even when the walk is stopped
        fg = self.phone.foreground()
        return Live(reply, elements, image, fg, ob.fingerprint(fg, elements, image, self.device),
                    ob.controls(elements, self.device), settled.ok)

    def look(self, sid: str) -> None:
        self.live = self.capture()
        self.verdict = self.match_state(self.live, sid)
        self.current = self.verdict["landed"]

    def safe_target(self, edge: Edge) -> ob.Candidate:
        """The one live control a tap edge's recorded element is now (invariant 3, as explore applies it): found the way
        explore re-finds a control and alone, on no deny-list, enabled, under nothing, and looking as its recorded
        crop does, not behind an overlay the recording didn't have."""
        rec, want = self.recorded(edge.from_state), self.recorded_control(edge)
        if want is None:
            raise FlowEnd("unsupported", f"no recorded control is {edge.element_id}")
        found = matches(self.live.cands, want)
        if len(found) != 1:
            raise FlowEnd("unsupported", f"{len(found)} live controls fit {edge.element_id}" if found
                          else f"{edge.element_id} is not on the live screen")
        live = found[0]
        reason = ob.denied(live, upsell=ob.is_upsell(self.live.elements, self.device))
        if reason:
            raise FlowEnd("blocked", f"denied: {reason}")
        if not live.enabled:
            raise FlowEnd("blocked", f"{edge.element_id} is disabled on the live screen")
        box = self.new_overlay(rec)
        if box and not ob.inside(live.rect, box):
            raise FlowEnd("unsupported", "an overlay the recording didn't have is up")
        if ob.covered(live, self.live.elements, self.device):
            raise FlowEnd("unsupported", f"something lies over {edge.element_id} on the live screen")
        if not ob.looks_same(rec.image, want.rect, self.live.image, live.rect, self.device):
            raise FlowEnd("unsupported", f"{edge.element_id} looks different from its recorded crop")
        return live

    def recorded_control(self, edge: Edge) -> ob.Candidate | None:
        """The control a tap edge's element is in its recorded capture."""
        element = next(e for e in self.states[edge.from_state].elements if e.id == edge.element_id)
        return next((c for c in self.recorded(edge.from_state).cands if c.ref == element.mcp_ref), None)

    def allowed(self, edge: Edge) -> bool:
        want = self.recorded_control(edge)
        upsell = ob.is_upsell(self.recorded(edge.from_state).elements, self.device)
        return want is not None and not ob.denied(want, upsell=upsell)

    def new_overlay(self, rec: Recorded) -> Rect | None:
        """A dialog or same-window overlay on the live screen, found as explore finds one (Explorer.kind_of, the scrim
        check included), when the recorded capture had none: a popup or coach mark that a tap behind it would hit."""
        if rec.box is not None:
            return None
        tabs = frozenset(t.key for t in ob.tab_bar(self.recorded(self.root).cands, self.device))
        return ob.dialog_box(self.live.cands, self.device) or ob.overlay_box(
            rec.cands, self.live.cands, self.device, tabs,
            lambda box: ob.scrim(rec.image, self.live.image, box, self.device))

    # ---------- where the live app is ----------

    def match_state(self, live: Live, sid: str, launched: bool = False) -> dict:
        """Where the live app is, against the recorded state sid, with no model: a fingerprint match first; else the
        same fixed parts (the top chrome and the element lists' structure, so a feed that reloaded is still itself);
        else, right after a launch, B's rule that a landing that looks like home is home. A screen that matches another
        recorded state, or another app, diverged; one that matches nothing is unknown."""
        verdict, landed = self.judge(live, sid, launched)
        return {"expected": sid, "verdict": verdict, "landed": landed,
                "structure": round(self.structure(sid, live), 3), "foreground": live.fg,
                "fingerprint": str(live.fp)}

    def judge(self, live: Live, sid: str, launched: bool) -> tuple[str, str | None]:
        if not live.settled:
            return "unsettled", None
        if ob.same_state(self.fps[sid], live.fp):
            return "same", sid
        other = next((o for o, fp in self.fps.items() if o != sid and ob.same_state(fp, live.fp)), None)
        if other:
            return "other", other
        if self.fixed(sid, live) and self.structure(sid, live) >= ob.STRUCTURE_SAME:
            return "structure", sid
        if launched and self.homelike(live):
            return ("home", sid) if sid == self.root else ("other", self.root)
        near, best = max(((self.structure(o, live), o) for o in self.fps if o != sid and self.fixed(o, live)),
                         default=(0.0, None))
        if near >= ob.STRUCTURE_SAME:
            return "other", best
        return ("away", None) if live.fg != self.package else ("unknown", None)

    def fixed(self, sid: str, live: Live) -> bool:
        fp = self.fps[sid]
        return fp.package == live.fp.package and ob.hamming(fp.top, live.fp.top) <= ob.TOP_BITS

    def structure(self, sid: str, live: Live) -> float:
        rec = self.recorded(sid)
        return ob.structure(rec.elements, live.elements, self.device, self.states[sid].dynamic_regions)

    def homelike(self, live: Live) -> bool:
        """explore's rule for where a relaunch lands (Explorer.homelike): nothing its launch checks would block, no wall
        (an account or money) home didn't show in the same place, and home's tab bar."""
        home = self.recorded(self.root).cands
        new = [c for c in live.cands if not any(h.label == c.label and h.kind == c.kind and ob.overlaps(h.rect, c.rect)
                                                for h in home)]
        blocked = (live.fg != self.package or not launchable(live, self.device)
                   or ob.dialog_box(live.cands, self.device)
                   or any(ob.BLOCKING.search(t) for t in ob.texts(live.elements, self.device)))
        tabs = [t for t in ob.tab_bar(home, self.device) if not ob.denied(t)]
        return not blocked and not ob.walled(new) and all(ob.find(live.cands, t) for t in tabs)

    def recorded(self, sid: str) -> Recorded:
        if sid not in self.recorded_states:
            explore_dir = self.ctx.run_dir / "explore"
            sf = StateFile.model_validate_json((explore_dir / "states" / f"{sid}.json").read_text())
            elements = parse_elements(json.loads((explore_dir / sf.elements_reply).read_text())) \
                if sf.elements_reply else []
            self.recorded_states[sid] = Recorded(elements, Image.open(explore_dir / sf.screenshot).convert("RGB"),
                                                 ob.controls(elements, self.device), sf.box)
        return self.recorded_states[sid]

    # ---------- evidence ----------

    def control(self, page, edge: Edge, target: ob.Candidate | None) -> dict | None:
        """The tapped control's live bounds against its data-el's on the mock, both in content dp."""
        if target is None:
            return None
        boxes, _ = qa_metrics.screen_dom(page, edge.from_state)
        live, drawn = qa_metrics.device_to_dp(target.rect, self.device), boxes.get(edge.element_id)
        return {"live_dp": qa.rect(live), "mock_dp": qa.rect(drawn) if drawn else None,
                "within": qa_metrics.within(drawn, live)}

    def checkpoint(self, result: dict, edge: Edge | None, expected: str, page, mock_side: dict,
                   control: dict | None) -> None:
        """Saves both sides as they are now: the live capture and its element list (both redacted), the mock's render,
        and the heatmap of their masked SSIM."""
        self.in_time()
        stem = f"flows/{result['flow']}/{len(result['checkpoints']):02d}"
        files = {"real": f"{stem}-real.png", "mock": f"{stem}-mock.png", "heatmap": f"{stem}-heatmap.png",
                 "tree": f"{stem}-tree.json"}
        (self.out / stem).parent.mkdir(parents=True, exist_ok=True)
        self.live.image.save(self.out / files["real"])
        render.screenshot(page, path=self.out / files["mock"])
        (self.out / files["tree"]).write_text(json.dumps(self.live.reply, indent=1, ensure_ascii=False))
        pixels = self.compare_pair(expected, page, Image.open(self.out / files["mock"]))
        qa_metrics.heatmap(self.live.image, pixels["map"], pixels["keep"]).save(self.out / files["heatmap"])
        ssim = None if pixels["ssim"] is None else round(pixels["ssim"], 4)
        result["checkpoints"].append({"edge": edge.id if edge else None, "action": edge.action if edge else "start",
                                      "expected": expected, "live": dict(self.verdict), "mock": mock_side,
                                      "control": control, "ssim": ssim, "coverage": round(pixels["coverage"], 3),
                                      "files": files})

    def compare_pair(self, expected: str, page, mock_image: Image.Image) -> dict:
        """QA's masked SSIM (qa.measure_screen's masks): copied art drawn over the spot it was cropped from, and the
        expected state's regions that move on their own."""
        state = self.states[expected]
        _, images = qa_metrics.screen_dom(page, page.evaluate(STATE_JS))
        origins = {f"assets/{e.id}.png": e.rect_dp for e in state.elements} | qa.art_origins(self.ctx, state)
        copies = [qa_metrics.overlap(origins[src], drawn) for src, drawn in images if src in origins]
        masked = [r for r in copies if r] + [qa_metrics.device_to_dp(r, self.device) for r in state.dynamic_regions]
        return qa_metrics.compare(self.live.image, mock_image, masked)


# ---------- the report ----------

def summary(flows: list[dict]) -> dict:
    statuses = Counter(f["status"] for f in flows)
    coverage = [c["coverage"] for f in flows for c in f["checkpoints"]]
    return {"total": len(flows), "supported": sum(f["supported"] for f in flows),
            "completed": sum(statuses[s] for s in COMPLETED), "by_status": {s: statuses[s] for s in STATUSES},
            "checkpoints": len(coverage),
            "mean_coverage": round(sum(coverage) / len(coverage), 3) if coverage else None}


def write_report(out: Path, report: dict) -> None:
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    (out / "report.md").write_text(markdown(report))


def markdown(report: dict) -> str:
    s, actions = report["summary"], report["actions"]
    stop = f" The audit stopped: {report['stop']}." if report["stop"] else ""
    lines = [f"# Live QA walk: {report['app']}, run {report['run']}", "",
             (f"{s['supported']} of {s['total']} flows supported; {s['completed']} completed (walked to the end or to "
              f"a verified divergence), {s['by_status']['matched']} matched. {actions['total']} device actions "
              f"({actions['setup']} of them setup) in {report['minutes']} min.{stop}"), "",
             "| Flow | Status | Checkpoints | Why |", "|---|---|---|---|"]
    for f in report["flows"]:
        why = "; ".join(r for r in [f["reason"], *f["assumed"]] if r)
        lines.append(f"| {f['flow']}: {f['name']} | {f['status']} | {len(f['checkpoints'])} | {why} |")
    return "\n".join([*lines, "", report["note"], ""])
