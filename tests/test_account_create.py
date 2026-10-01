"""--allow-account-create on a fake phone: off, nothing changes; on, a sign-up wall is passed by a way on without an
account first, else by an email sign-up typed from SIMULA_TEST_*, and anything it isn't sure of stops it at the wall
with its reason. The identity never reaches a saved file, a model's input or a screenshot unredacted. The tests named
for a red-team finding (rt-pr35-8735b45) each failed on 8735b45."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from simula import decide, llm, runlog
from simula.contracts import Rect
from simula.device import mcp
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


def screen(seed: int, *elements: dict, package: str = PACKAGE) -> Screen:
    """A screen with its own picture, so two screens never share a fingerprint."""
    blocks = np.random.default_rng(seed).integers(0, 255, (60, 27, 3), dtype=np.uint8)
    return Screen(list(elements), Image.fromarray(blocks).resize((1080, 2400), Image.Resampling.NEAREST), package)


TITLE = el("@t", "TextView", "Create an account to start chatting", 200)
EMAIL_WAY = el("@email", "Button", "Sign up with email", 1500)
GOOGLE = el("@google", "Button", "Continue with Google", 1700)
LOG_IN = el("@login", "Button", "Log in", 1900)
FORM = [el("@ft", "TextView", "Create your account", 200),
        el("@c1", "TextView", "Email", 420, h=60), el("@f1", "EditText", "", 500),
        el("@c2", "TextView", "Password", 720, h=60), el("@f2", "EditText", "", 800),
        el("@c3", "TextView", "Name", 1020, h=60), el("@f3", "EditText", "", 1100),
        el("@go", "Button", "Create account", 1700),
        el("@have", "TextView", "Already have an account? Log in", 1950)]
HOME = [el("@h0", "TextView", "Welcome back", 200), el("@h1", "Button", "Characters", 700),
        el("@h2", "Button", "Discover", 1000),
        *(el(f"@tab{n}", "Button", label, 2200, x=360 * n, w=360) for n, label in enumerate(("Home", "Chats", "Me")))]


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
                after_form: list[dict] = HOME, phone=FormPhone):
    """An app that opens on a sign-up wall: its email way opens a form, and the form's button what comes next."""
    def factory(clock):
        app = phone(screens={"wall": screen(1, *wall), "form": screen(2, *after_way), "next": screen(3, *after_form),
                             "home": screen(4, *HOME)},
                    start="wall", clock=clock,
                    taps={("wall", "Sign up with email"): "form", ("form", "Create account"): "next",
                          **{("wall", label): "home" for label in ("Continue as guest", "Not now", "Skip")}})
        app.remember = {"next"} if after_form is HOME else set()
        return app
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


def test_a_dismissal_on_an_overlay_wall_is_a_way_on_rt_f4(tmp_path, monkeypatch, identity):
    """rt F4: the old test's "Not now" wasn't on the phone. Now it is: dismissing the sheet is the way on."""
    later = el("@later", "Button", "Not now", 2100)
    ex, phone = new_explorer(tmp_path, monkeypatch, sign_up_app(wall=[TITLE, EMAIL_WAY, GOOGLE, LOG_IN, later]),
                             allow_account_create=True)
    phone.screen = "wall"
    wall = ex.current = ex.record(ex.observe(), None, None, None)
    wall.kind = "sheet"
    assert ex.account_wall(wall) and ex.get_past(wall) is True
    assert taps(phone) == ["Not now"] and phone.screen == "home" and not phone.typed
    assert ex.account == ["s01: went on without an account ('Not now')"]


def test_a_way_on_that_leaves_the_app_is_no_way_on_and_never_the_root_rt_f5d(tmp_path, monkeypatch, identity):
    skip = el("@skip", "TextView", "Skip", 200, x=900, w=150, h=90)

    def app(clock):
        phone = sign_up_app(wall=[TITLE, skip, EMAIL_WAY, GOOGLE, LOG_IN])(clock)
        phone.taps[("wall", "Skip")] = "launcher"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert taps(phone) == ["Skip"] and not phone.typed
    assert ex.root.sid == "s01" and ex.root is ex.launch_root and ex.root.kind == "screen"
    assert ex.account == ["s01: stopped at the wall, 'Skip' left the app"]


def test_the_email_form_is_filled_from_the_environment_and_nothing_else_is_typed(tmp_path, monkeypatch, identity):
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app())
    assert phone.typed == [EMAIL, PASSWORD, NAME]
    assert phone.values == {("form", "@f1"): EMAIL, ("form", "@f2"): PASSWORD, ("form", "@f3"): NAME}
    assert "Continue with Google" not in taps(phone) and "Log in" not in taps(phone)
    assert ex.account_state == "made" and ex.root is ex.launch_root is ex.current
    assert ob.texts(ex.root.elements, ex.device) == {e["text"] for e in HOME}
    assert ex.account[-1].startswith("s01: signed up with the test identity")


class PanningPhone(FormPhone):
    """The form pans up 300 px once the first value is typed (the soft keyboard opening with adjustPan)."""
    pan = 0

    def current_elements(self) -> list[dict]:
        shift = self.pan if self.screen == "form" else 0
        return [{**e, "coordinates": {**e["coordinates"], "y": e["coordinates"]["y"] - shift}}
                for e in super().current_elements()]

    def image(self) -> Image.Image:
        image = super().image()
        if self.screen == "form" and self.pan:
            moved = Image.new("RGB", image.size, (255, 255, 255))
            moved.paste(image, (0, -self.pan))
            return moved
        return image

    def type_text(self, text: str) -> None:
        super().type_text(text)
        if self.screen == "form":
            self.pan = 300


def plain(*elements: dict) -> Screen:
    """A form whose empty boxes look alike, as real ones do."""
    image = Image.new("RGB", (1080, 2400), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for n, e in enumerate(elements):
        c = e["coordinates"]
        if e["type"].endswith("EditText"):
            draw.rectangle((c["x"], c["y"], c["x"] + c["width"], c["y"] + c["height"]), outline=(120, 120, 120),
                           width=4)
        else:
            for k in range(0, c["width"], 37 + 11 * n):
                draw.rectangle((c["x"] + k, c["y"] + 10, c["x"] + k + 20, c["y"] + c["height"] - 10), fill=(0, 0, 0))
    return Screen(list(elements), image, PACKAGE)


def test_a_form_that_pans_gets_each_value_in_its_own_box_rt_f10(tmp_path, monkeypatch, identity):
    low = [FORM[0], el("@c1", "TextView", "Email", 1120, h=60), el("@f1", "EditText", "", 1200),
           el("@c2", "TextView", "Password", 1420, h=60), el("@f2", "EditText", "", 1500),
           el("@c3", "TextView", "Name", 1720, h=60), el("@f3", "EditText", "", 1800),
           el("@go", "Button", "Create account", 2100)]

    def app(clock):
        phone = sign_up_app(phone=PanningPhone)(clock)
        phone.screens["form"] = plain(*low)
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.values == {("form", "@f1"): EMAIL, ("form", "@f2"): PASSWORD, ("form", "@f3"): NAME}, ex.account


def stop_reason(tmp_path, monkeypatch, factory) -> str:
    ex, phone = launched(tmp_path, monkeypatch, factory)
    assert ex.root.sid == "s01" and ex.account_state in ("", "sent"), ex.account
    assert {"Continue with Google", "Log in"}.isdisjoint(taps(phone))
    line = next(line for line in runlog.read_trace(ex.run_dir / "trace.jsonl") if line.step == "account")
    assert line.outcome == "blocked" and line.note == ex.account[-1] in stage.exhibit(ex, None)
    return ex.account[-1]


@pytest.mark.parametrize("var, value, reason", [
    ("SIMULA_TEST_PASSWORD", None, "SIMULA_TEST_PASSWORD not set"),
    ("SIMULA_TEST_NAME", "Zoë Testperson",
     "SIMULA_TEST_NAME not ASCII (mobile-mcp would paste it through the clipboard)")])
def test_a_missing_or_pasted_identity_value_stops_at_the_wall(tmp_path, monkeypatch, identity, var, value, reason):
    if value is None:
        monkeypatch.delenv(var)
    else:
        monkeypatch.setenv(var, value)
    assert stop_reason(tmp_path, monkeypatch, sign_up_app()) == f"s01: no sign-up, {reason}"


def other_app(clock):
    """rt F7: the email way opens Google's own sign-in, another app."""
    phone = sign_up_app()(clock)
    phone.screens["form"] = screen(9, el("@g0", "TextView", "Sign in", 200),
                                   el("@g1", "TextView", "Email or phone", 420, h=60), el("@g2", "EditText", "", 500),
                                   el("@g3", "Button", "Next", 1700), package="com.google.android.gms")
    return phone


def form_with(*extra: dict, without: str = "") -> list[dict]:
    return [e for e in FORM if e["ref"] != without] + list(extra)


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
    (sign_up_app(after_way=[el("@pt", "TextView", "Start your membership: $9.99 / month", 200),
                            el("@c1", "TextView", "Name on card", 420, h=60), el("@f1", "EditText", "", 500),
                            el("@c3", "TextView", "Email for receipts", 1020, h=60), el("@f3", "EditText", "", 1100),
                            el("@go", "Button", "Next", 1700)]),
     "a payment step on s02"),
    (sign_up_app(after_way=form_with(el("@ok", "Button", "Agree and continue", 1800), without="@go")),
     "it asks to accept terms ('Agree and continue')"),
    (sign_up_app(after_way=form_with(el("@c4", "TextView", "Referral code", 1320, h=60),
                                     el("@f4", "EditText", "", 1400))),
     "s02 has a box that is not the email, the password or the name"),
    (sign_up_app(after_way=[{**e, "text": "owner.real@gmail.com"} if e["ref"] == "@f1" else e for e in FORM]),
     "a box on s02 shows a value already"),
    (other_app, "the way in left the app (com.google.android.gms)"),
], ids=["another account", "phone way", "phone box", "captcha", "payment rt-f6a", "consent rt-f3", "unknown box",
        "pre-filled rt-f2", "another app rt-f7"])
def test_what_it_is_not_sure_of_stops_it_at_the_wall_before_anything_is_typed(tmp_path, monkeypatch, identity, app,
                                                                                reason):
    phones = []

    def factory(clock):
        phones.append(app(clock))
        return phones[-1]
    assert stop_reason(tmp_path, monkeypatch, factory) == f"s01: stopped at the wall, {reason}"
    assert not phones[0].typed and "Create account" not in taps(phones[0])


@pytest.mark.parametrize("after", [
    [el("@e0", "TextView", "Something went wrong", 200), el("@e1", "TextView", "This email is already registered", 600),
     el("@ok", "Button", "OK", 1700)],
    [el("@o0", "TextView", "Create your first character", 200), el("@o1", "TextView", "Character name", 420, h=60),
     el("@o2", "EditText", "", 500), el("@o3", "Button", "Create", 1700)],
], ids=["an error rt-f8", "an onboarding form rt-f11"])
def test_after_the_form_only_the_app_itself_counts_as_signed_up_and_nothing_more_is_typed(tmp_path, monkeypatch,
                                                                                         identity, after):
    phones = []

    def factory(clock):
        phones.append(sign_up_app(after_form=after)(clock))
        phones[-1].taps[("next", "Create")] = "home"
        return phones[-1]
    reason = stop_reason(tmp_path, monkeypatch, factory)
    assert reason.startswith("s01: stopped at the wall, the form was sent, but s") and reason.endswith(
        "shows no sign of an account")
    assert phones[0].typed == [EMAIL, PASSWORD, NAME] and taps(phones[0])[-1] == "Create account"


VERIFY = [el("@v0", "TextView", "Check your email", 200), el("@v1", "TextView", f"We sent a link to {EMAIL}", 500),
          el("@v2", "Button", "Resend email", 1700)]
CODE = [el("@v0", "TextView", "Check your inbox", 200),
        el("@v1", "TextView", "Enter the code we sent to your email", 420, h=60), el("@v2", "EditText", "", 500),
        el("@v3", "Button", "Continue", 1700)]


@pytest.mark.parametrize("after", [VERIFY, CODE], ids=["a link", "a code box rt-f6b"])
def test_an_email_verification_asks_a_person_and_leaves_explore_partial(tmp_path, monkeypatch, identity, after):
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_form=after))
    assert ex.account_state == "verify" and phone.typed == [EMAIL, PASSWORD, NAME]
    assert taps(phone)[-1] == "Create account"
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


GUEST_HOME = [el("@g0", "TextView", "Discover AI characters", 200, w=700),
              el("@g1", "TextView", "Talk to anyone, any time. Thousands of characters are waiting.", 400, h=200),
              el("@go", "Button", "Start chatting", 1900), el("@li", "Button", "Log in", 200, x=820, w=200, h=90)]


@pytest.mark.parametrize("extra", [[], [el("@su", "Button", "Sign up", 320, x=820, w=200, h=70)],
                                   [el("@wl", "Button", "Watch later", 1600, w=400)]],
                         ids=["log in rt-f5a", "a sign-up link rt-f5b", "a content word rt-f5c"])
def test_a_usable_guest_home_is_no_wall(tmp_path, monkeypatch, identity, extra):
    def app(clock):
        phone = sign_up_app()(clock)
        phone.screens["wall"] = screen(7, *GUEST_HOME, *extra)
        phone.taps.update({("wall", "Sign up"): "form", ("wall", "Watch later"): "home"})
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert not ex.account and not phone.typed and not taps(phone) and ex.root.sid == "s01"


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
    assert ex.secrets == ["offline-test-handle", EMAIL, PASSWORD, NAME, "Quinlan", "Testperson", "tester.q7"]
    assert NAME not in json.dumps(obs.reply) and obs.image.getpixel((500, 1150)) == (0, 0, 0)


def test_an_echo_of_part_of_the_identity_is_redacted_too_rt_f9(tmp_path, monkeypatch, identity):
    hello = [el("@hi", "TextView", "Hi Quinlan! Your handle is @tester.q7", 200), *HOME[1:]]
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_form=hello))
    assert ex.account_state == "made"
    files = [p.read_bytes() for p in ex.run_dir.rglob("*") if p.is_file()]
    assert not [part for part in (b"Quinlan", b"tester.q7") if any(part in data for data in files)]


class OfflineAdb:
    """mobile-mcp's reply when `adb shell input text` exits non-zero: Node's execFileSync message is the full argv."""

    def call(self, tool, timeout, **args):
        text = args["text"].replace(" ", "\\ ")
        return {"isError": True, "content": [{"type": "text", "text": "Error: Command failed: /a/adb -s emulator-5554 "
                                                                      f"shell input text {text}\nadb: device offline"}]}


class FlakyTypePhone(FormPhone):
    """Types through the real Phone.type_text, which fails as mobile-mcp does when adb loses the device."""

    def type_text(self, text: str) -> None:
        if text == PASSWORD:
            real = object.__new__(mcp.Phone)
            real.server, real.device = OfflineAdb(), "emulator-5554"
            real.type_text(text)
        super().type_text(text)


def test_a_failed_type_never_writes_the_typed_text_rt_f1(tmp_path, monkeypatch, identity):
    ex, phone = new_explorer(tmp_path, monkeypatch, sign_up_app(phone=FlakyTypePhone), allow_account_create=True)
    with pytest.raises(stage.ExploreFailed) as failed:
        stage.explore_app(ex)
    assert "mobile_type_keys failed" in ex.stop_reason and PASSWORD[:6] not in str(failed.value)
    files = [p for p in ex.run_dir.rglob("*") if p.is_file()]
    assert not [p for p in files if PASSWORD[:6].encode() in p.read_bytes()]


def test_the_sign_up_words_are_lifted_on_the_sign_up_path_only():
    def control(label, kind="Button"):
        return ob.Candidate(label, kind, Rect(x=0, y=0, w=500, h=100), "@c", label)
    for label in ("Sign up", "Create account", "Continue with email", "Sign up with email", "Submit"):
        assert ob.denied(control(label)) and not ob.denied(control(label), account=True), label
    assert ob.denied(control("", "EditText")) == "text input" and not ob.denied(control("", "EditText"), account=True)
    kept = {"Continue with Google": "continue with", "Sign up with Apple": "sign up", "Sign up with phone": "sign up",
            "Log in": "log in", "Subscribe": "subscribe", "Start free trial": "start free trial", "Post": "post",
            "Sign up and subscribe": "subscribe", "Resend code": "send", "I agree to the Terms": "consent",
            "Agree and continue": "consent", "Accept & continue": "consent", "Accept": "consent"}
    assert {label: ob.denied(control(label), account=True) for label in kept} == kept
    assert ob.denied(control("I accept the terms and the privacy policy", "CheckBox"), account=True) == "consent"


def test_guest_words_are_a_whole_label():
    ways = ("Continue as guest", "Continue as a guest", "Continue without an account", "Skip", "Skip for now",
            "Not now", "Maybe later", "Guest mode")
    content = ("Watch later", "Skip intro", "Guest stars of the week", "Not now, but soon: new characters")
    assert all(ob.GUEST.search(w) for w in ways) and not any(ob.GUEST.search(c) for c in content)


AOL_SIGN_IN = Path(__file__).parent.parent / "runs/perplexity/20260929-212810-1f19585/explore/states/s11.elements.json"


def test_a_box_is_named_by_its_own_words_or_its_caption():
    """The first case is a real sign-in form, AOL's (its caption sits further above than the box is tall), opened
    from Perplexity in a committed run."""
    elements = json.loads(json.loads(AOL_SIGN_IN.read_text())["content"][0]["text"].removeprefix(ob.ELEMENTS_PREFIX))
    assert ob.field_kind(next(e for e in elements if e["type"].endswith("EditText")), elements) == "email"

    def kind(caption: str) -> str:
        pair = [el("@c", "TextView", caption, 420, h=60), el("@b", "EditText", "", 500)]
        return ob.field_kind(pair[1], pair)
    captions = ("Password", "Full name", "Username", "Phone number", "Referral code")
    assert [kind(c) for c in captions] == ["password", "name", "", "phone", ""]
    by_id = {**el("@i", "EditText", "", 900), "identifier": "com.app:id/et_password"}
    assert ob.field_kind(by_id, [by_id]) == "password"
    below = [el("@c", "TextView", "Email", 420, h=60), el("@a", "EditText", "", 500), el("@b", "EditText", "", 700)]
    assert ob.field_kind(below[2], below) == ""
