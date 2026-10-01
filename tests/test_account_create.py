"""--allow-account-create on a fake phone: off, nothing changes; on, a sign-up wall is passed by a way on without an
account first, else by an email sign-up typed from SIMULA_TEST_*, and every other way in stops at the wall with its
reason. The identity never reaches a saved file, a model's input or a screenshot unredacted."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from simula import decide, llm, runlog
from simula.contracts import Rect
from simula.device import observe as ob
from simula.stages import explore as stage
from tests.fake_device import PACKAGE, FakePhone, Screen, fake_jev, fake_sonnet
from tests.fake_device import explore as run_explorer
from tests.fake_device import explorer as new_explorer
from tests.test_explore_offline import janitor_like

EMAIL, PASSWORD, NAME = "tester.q7@example.org", "Pw-7f3Kq9-unique", "Quinlan Testperson"


def el(ref: str, kind: str, text: str, y: int, x: int = 90, w: int = 900, h: int = 120) -> dict:
    return {"ref": ref, "type": f"android.widget.{kind}", "text": text, "coordinates": {"x": x, "y": y, "width": w,
                                                                                         "height": h}}


def screen(seed: int, *elements: dict) -> Screen:
    """A screen with its own picture, so two screens never share a fingerprint."""
    blocks = np.random.default_rng(seed).integers(0, 255, (60, 27, 3), dtype=np.uint8)
    return Screen(list(elements), Image.fromarray(blocks).resize((1080, 2400), Image.Resampling.NEAREST), PACKAGE)


TITLE = el("@t", "TextView", "Create an account to start chatting", 200)
EMAIL_WAY = el("@email", "Button", "Sign up with email", 1500)
GOOGLE = el("@google", "Button", "Continue with Google", 1700)
LOG_IN = el("@login", "Button", "Log in", 1900)
FORM = [el("@ft", "TextView", "Create your account", 200),
        el("@c1", "TextView", "Email", 420, h=60), el("@f1", "EditText", "", 500),
        el("@c2", "TextView", "Password", 720, h=60), el("@f2", "EditText", "", 800),
        el("@c3", "TextView", "Name", 1020, h=60), el("@f3", "EditText", "", 1100),
        el("@c4", "TextView", "Referral code", 1320, h=60), el("@f4", "EditText", "", 1400),
        el("@go", "Button", "Create account", 1700),
        el("@have", "TextView", "Already have an account? Log in", 1950)]
HOME = [el("@h0", "TextView", "Welcome back", 200), el("@h1", "Button", "Characters", 700),
        el("@h2", "Button", "Discover", 1000)]


class FormPhone(FakePhone):
    """A fake phone whose text boxes take the focus on a tap and keep what is typed into them, and that opens on a
    screen it reached once it remembers one (a signed-in app)."""

    def __post_init__(self):
        super().__post_init__()
        self.values: dict[tuple[str, str], str] = {}
        self.focus: tuple[str, str] | None = None
        self.remember: set[str] = set()

    def current_elements(self) -> list[dict]:
        return [{**e, "text": self.values.get((self.screen, e["ref"]), e["text"]),
                 **({"focused": True} if self.focus == (self.screen, e["ref"]) else {})}
                for e in super().current_elements()]

    def tap(self, x: int, y: int) -> None:
        box = next((e for e in self.current_elements() if e["type"].endswith("EditText")
                    and ob.inside(Rect(x=x, y=y, w=0, h=0), ob.rect(e))), None)
        self.focus = (self.screen, box["ref"]) if box else None
        super().tap(x, y)
        if self.screen in self.remember:
            self.start = self.screen

    def type_text(self, text: str) -> None:
        super().type_text(text)
        if self.focus:
            self.values[self.focus] = self.values.get(self.focus, "") + text


def sign_up_app(wall: list[dict] = (TITLE, EMAIL_WAY, GOOGLE, LOG_IN), after_way: list[dict] = FORM,
                after_form: list[dict] = HOME):
    """An app that opens on a sign-up wall: its email way opens a form, and the form's button what comes next."""
    def factory(clock):
        phone = FormPhone(screens={"wall": screen(1, *wall), "form": screen(2, *after_way),
                                   "next": screen(3, *after_form), "home": screen(4, *HOME)},
                          start="wall", clock=clock,
                          taps={("wall", "Sign up with email"): "form", ("wall", "Continue as guest"): "home",
                                ("form", "Create account"): "next", ("form", "Continue"): "next"})
        phone.remember = {"next"} if after_form is HOME else set()
        return phone
    return factory


@pytest.fixture
def identity(monkeypatch):
    for var, value in zip(stage.IDENTITY.values(), (EMAIL, PASSWORD, NAME), strict=True):
        monkeypatch.setenv(var, value)


def launched(tmp_path, monkeypatch, factory, allow=True):
    """The first launch, where a launch wall is met: the explorer and the phone after it."""
    ex, phone = new_explorer(tmp_path, monkeypatch, factory, allow_account_create=allow)
    ex.relaunch(first=True)
    return ex, phone


def taps(phone) -> list[str]:
    return [key for kind, *rest in phone.log if kind == "tap" for key in rest[1:]]


def test_flag_off_leaves_the_wall_as_it_is(tmp_path, monkeypatch, identity):
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(), allow=False)
    assert ex.root.sid == "s01" and ex.current is ex.root and not phone.typed
    assert not {"Sign up with email", "Continue with Google"} & set(taps(phone))
    assert ex.secrets == ["offline-test-handle"] and not ex.account
    assert not any(line.step == "account" for line in runlog.read_trace(ex.run_dir / "trace.jsonl"))


def test_without_a_wall_the_flag_changes_no_action(tmp_path, monkeypatch, identity):
    off, phone_off = run_explorer(tmp_path / "off", monkeypatch, janitor_like)
    on, phone_on = run_explorer(tmp_path / "on", monkeypatch, janitor_like, allow_account_create=True)
    assert (on.out / "actions.jsonl").read_text() == (off.out / "actions.jsonl").read_text()
    assert phone_on.log == phone_off.log and not on.account


def test_the_way_on_without_an_account_comes_first(tmp_path, monkeypatch, identity):
    guest = el("@guest", "Button", "Continue as guest", 1300)
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(wall=[TITLE, guest, EMAIL_WAY, GOOGLE, LOG_IN]))
    assert taps(phone) == ["Continue as guest"] and not phone.typed
    assert ex.root is ex.launch_root is ex.current and ex.root.sid != "s01"
    assert ex.account == ["s01: went on without an account ('Continue as guest')"]


def test_a_dismissal_of_an_overlay_is_no_way_on(tmp_path, monkeypatch, identity):
    ex, phone = new_explorer(tmp_path, monkeypatch, sign_up_app(), allow_account_create=True)
    phone.screen = "wall"
    wall = ex.current = ex.record(ex.observe(), None, None, None)
    wall.kind = "sheet"
    wall.cands.append(ob.Candidate("Not now", "Button", Rect(x=90, y=2100, w=900, h=120), "@later", "Not now"))
    ex.get_past(wall)
    assert "Not now" not in taps(phone) and phone.typed == [EMAIL, PASSWORD, NAME]


def test_the_email_form_is_filled_from_the_environment_and_nothing_else_is_typed(tmp_path, monkeypatch, identity):
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app())
    assert phone.typed == [EMAIL, PASSWORD, NAME]
    assert phone.values == {("form", "@f1"): EMAIL, ("form", "@f2"): PASSWORD, ("form", "@f3"): NAME}
    assert "Continue with Google" not in taps(phone) and "Log in" not in taps(phone)
    assert ex.account_state == "made" and ex.root is ex.launch_root is ex.current
    assert ob.texts(ex.root.elements, ex.device) == {e["text"] for e in HOME}
    assert ex.account[-1].startswith("s01: signed up with the test identity")


def stop_reason(tmp_path, monkeypatch, factory) -> str:
    ex, phone = launched(tmp_path, monkeypatch, factory)
    assert ex.root.sid == "s01" and not ex.account_state, ex.account
    assert {"Continue with Google", "Log in"}.isdisjoint(taps(phone))
    line = next(line for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.step == "account")
    assert line.outcome == "blocked" and line.note == ex.account[-1] in stage.exhibit(ex, None)
    return ex.account[-1]


def test_missing_identity_stops_at_the_wall(tmp_path, monkeypatch, identity):
    monkeypatch.delenv("SIMULA_TEST_PASSWORD")
    assert stop_reason(tmp_path, monkeypatch, sign_up_app()) == "s01: no sign-up, SIMULA_TEST_PASSWORD not set"


@pytest.mark.parametrize("app, reason", [
    (sign_up_app(wall=[TITLE, GOOGLE, el("@apple", "Button", "Sign up with Apple", 1300), LOG_IN]),
     "the way in takes a sign-in with another account ('Continue with Google')"),
    (sign_up_app(wall=[TITLE, el("@phone", "Button", "Sign up with phone", 1300), GOOGLE, LOG_IN]),
     "the way in takes a phone number ('Sign up with phone')"),
    (sign_up_app(after_way=[FORM[0], el("@c", "TextView", "Phone number", 420, h=60), el("@p", "EditText", "", 500),
                            el("@go", "Button", "Continue", 1700)]),
     "s02 asks for a phone number"),
    (sign_up_app(after_way=[FORM[0], el("@robot", "TextView", "I'm not a robot", 700)]),
     "a check only a person can pass (\"I'm not a robot\")"),
    (sign_up_app(after_way=[el("@plans", "TextView", "Choose your plan", 200),
                            el("@price", "TextView", "$9.99 / month", 700),
                            el("@go", "Button", "Start free trial", 1700)]),
     "a payment step on s02"),
], ids=["another account", "phone way", "phone box", "captcha", "payment"])
def test_each_way_in_it_never_takes_stops_at_the_wall_with_its_reason(tmp_path, monkeypatch, identity, app, reason):
    assert stop_reason(tmp_path, monkeypatch, app) == f"s01: stopped at the wall, {reason}"


VERIFY = [el("@v0", "TextView", "Check your email", 200), el("@v1", "TextView", f"We sent a link to {EMAIL}", 500),
          el("@v2", "Button", "Resend email", 1700)]


def test_an_email_verification_asks_a_person_and_leaves_explore_partial(tmp_path, monkeypatch, identity):
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_form=VERIFY))
    assert ex.account_state == "verify" and phone.typed == [EMAIL, PASSWORD, NAME]
    assert "Check your email" in ob.texts(ex.current.elements, ex.device)
    assert ex.account[-1] == (f"s01: stopped at the wall, an email verification step on {ex.current.sid}, for a person "
                              "(needs-human.md)")
    asked = (ex.run_dir / "needs-human.md").read_text()
    assert "SIMULA_TEST_EMAIL" in asked and "follow its link" in asked and "--allow-account-create" in asked
    result = stage.outcome(ex)
    assert result.status == "partial" and any("verify the test account's email" in r for r in result.reasons)


def test_a_wall_the_tour_opens_is_met_like_a_launch_wall(tmp_path, monkeypatch, identity):
    def app(clock):
        phone = sign_up_app()(clock)
        phone.start, phone.taps[("home", "Characters")] = "home", "wall"
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, app, allow_account_create=True)
    stage.run_tour(ex)
    assert phone.typed[:3] == [EMAIL, PASSWORD, NAME] and ex.account_state == "made"
    assert ex.root.sid == "s01" and ex.account[0].startswith("s02: signed up with the test identity"), ex.account


def test_the_identity_reaches_no_file_no_model_and_no_screenshot(tmp_path, monkeypatch, identity):
    seen = []

    def jev(state, instructions, labels, backend, **kwargs):
        seen.append(json.dumps([state, instructions, labels]))
        return fake_jev(state, instructions, labels, backend, **kwargs)

    def sonnet(model, system, messages, effort, schema, max_tokens, total_timeout=None):
        seen.append(system + json.dumps([p.get("text", "") for p in messages[0]["content"]]))
        return fake_sonnet(model, system, messages, effort, schema, max_tokens, total_timeout)
    ex, phone = new_explorer(tmp_path, monkeypatch, sign_up_app(after_form=VERIFY), allow_account_create=True)
    monkeypatch.setattr(decide, "ask_choice", jev)
    monkeypatch.setitem(llm.PROVIDERS, "anthropic", sonnet)
    stage.explore_app(ex)
    assert phone.typed[:3] == [EMAIL, PASSWORD, NAME] and seen
    secrets = [s.encode() for s in (EMAIL, PASSWORD, NAME)]
    files = [p for p in [*ex.run_dir.rglob("*"), *Path(ex.cache_dir).rglob("*")] if p.is_file()]
    assert files and not [(p, s) for p in files for s in secrets if s in p.read_bytes()]
    assert not [s for text in seen for s in (EMAIL, PASSWORD, NAME) if s in text]
    typed = [line.note for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.note.startswith("type ")]
    assert len(typed) == 3 and all(note.startswith(f"type {ob.REDACTED!r}") for note in typed)

    phone.screen, phone.values[("form", "@f3")] = "form", NAME
    ex.scratch.mkdir()
    obs = ex.observe()
    assert ex.secrets == ["offline-test-handle", EMAIL, PASSWORD, NAME]
    assert NAME not in json.dumps(obs.reply) and obs.image.getpixel((500, 1150)) == (0, 0, 0)


def test_the_sign_up_words_are_lifted_on_the_sign_up_path_only():
    def control(label, kind="Button"):
        return ob.Candidate(label, kind, Rect(x=0, y=0, w=500, h=100), "@c", label)
    for label in ("Sign up", "Create account", "Continue with email", "Sign up with email", "Submit"):
        assert ob.denied(control(label)) and not ob.denied(control(label), account=True), label
    assert ob.denied(control("", "EditText")) == "text input" and not ob.denied(control("", "EditText"), account=True)
    kept = {"Continue with Google": "continue with", "Sign up with Apple": "sign up", "Sign up with phone": "sign up",
            "Log in": "log in", "Subscribe": "subscribe", "Start free trial": "start free trial", "Post": "post",
            "Sign up and subscribe": "subscribe", "Resend code": "send", "I agree to the Terms": "terms"}
    assert {label: ob.denied(control(label), account=True) for label in kept} == kept


AOL_SIGN_IN = Path(__file__).parent.parent / "runs/perplexity/20260929-212810-1f19585/explore/states/s11.elements.json"


def test_a_box_is_named_by_its_own_words_or_its_caption():
    """The first case is a real sign-in form, AOL's (its caption sits further above than the box is tall), opened
    from Perplexity in a committed run."""
    elements = json.loads(json.loads(AOL_SIGN_IN.read_text())["content"][0]["text"].removeprefix(ob.ELEMENTS_PREFIX))
    box = next(c for c in ob.controls(elements, ob.Device()) if c.kind == "EditText")
    assert ob.field_kind(box, elements) == "email"

    def kind(caption: str) -> str:
        pair = [el("@c", "TextView", caption, 420, h=60), el("@b", "EditText", "", 500)]
        return ob.field_kind(ob.controls(pair, ob.Device())[-1], pair)
    captions = ("Password", "Full name", "Username", "Phone number", "Referral code")
    assert [kind(c) for c in captions] == ["password", "name", "", "phone", ""]
    by_id = ob.Candidate("", "EditText", Rect(x=0, y=900, w=900, h=120), "@i", "", ident="et_password")
    assert ob.field_kind(by_id, []) == "password"
