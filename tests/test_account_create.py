"""--allow-account-create on a fake phone: off, nothing changes; on, a sign-up wall is passed by a way on without an
account first, else by an email sign-up typed from SIMULA_TEST_*, and anything it isn't sure of stops it at the wall
with its reason. The identity never reaches a saved file, a model's input or a screenshot unredacted. The tests named
for a red-team finding failed on the commit it was found on (rt_f*: 8735b45, rt_r2_*: b568449,
rt_r3_*: 66f6ca8, rt_r4_*: 3d48f8d)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from simula import decide, llm, runlog
from simula.contracts import IconPass, Rect
from simula.device import mcp
from simula.device import observe as ob
from simula.stages import explore as stage
from tests.fake_device import PACKAGE, FakePhone, Screen, fake_jev, fake_sonnet
from tests.fake_device import explore as run_explorer
from tests.fake_device import explorer as new_explorer
from tests.test_explore_offline import janitor_like, unlisted

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


PHONES: list = []  # the FormPhones a test made, newest last
KEYS = 1500  # the top of a FormPhone's soft keyboard


class FormPhone(FakePhone):
    """A fake phone whose text boxes take the focus on a tap and keep what is typed into them, and that opens on a
    screen it reached once it remembers one (a signed-in app). Its soft keyboard is up while a box has the focus, over
    the lower part of the screen (KEYS): a tap there presses a key, and BACK closes the keyboard and nothing else."""

    def __post_init__(self):
        super().__post_init__()
        self.values: dict[tuple[str, str], str] = {}
        self.focus: tuple[str, str] | None = None
        self.remember: set[str] = set()
        PHONES.append(self)

    def dumpsys(self, args: list[str]) -> str:
        if args[1] == "input_method":
            return f"  mInputShown={'true' if self.focus else 'false'}\n"
        return f"  Window #3 Window{{a1 u0 InputMethod}}:\n    mFrame=[0,{KEYS}][1080,2400] last=[0,0][0,0]\n"

    def back(self) -> None:
        if self.focus is None:
            return super().back()
        self.tick()
        self.log.append(("back", self.screen))
        self.focus = None

    def current_elements(self) -> list[dict]:
        return [{**e, "text": self.values.get((self.screen, e["ref"]), e["text"]),
                 **({"focused": True} if self.focus == (self.screen, e["ref"]) else {})}
                for e in super().current_elements()]

    def tap(self, x: int, y: int) -> None:
        if self.focus and y >= KEYS:
            self.tick()
            self.log.append(("key", self.screen))
            return
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


@pytest.fixture(autouse=True)
def keyboard(monkeypatch):
    """dumpsys answers from the newest FormPhone; without one, as conftest's no_device answers."""
    PHONES.clear()
    fallback = stage.adb_shell
    monkeypatch.setattr(stage, "adb_shell", lambda serial, args: PHONES[-1].dumpsys(args) if PHONES
                        else fallback(serial, args))


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
     "it asks for a consent or an attestation ('Agree and continue')"),
    (sign_up_app(after_way=form_with(el("@c4", "TextView", "Referral code", 1320, h=60),
                                     el("@f4", "EditText", "", 1400))),
     "s02 has a box that is not the email, the password or the name"),
    (sign_up_app(after_way=[{**e, "text": "owner.real@gmail.com"} if e["ref"] == "@f1" else e for e in FORM]),
     "a box on s02 shows a value already"),
    (other_app, "the way in left the app (com.google.android.gms)"),
    (sign_up_app(after_way=[{**e, "coordinates": {**e["coordinates"], "width": 440}} if e["ref"] == "@f1" else e
                            for e in form_with(el("@f1b", "EditText", "", 500, x=550, w=440))]),
     "s02 has a box that is not the email, the password or the name"),
], ids=["another account", "phone way", "phone box", "captcha", "payment rt-f6a", "consent rt-f3", "unknown box",
        "pre-filled rt-f2", "another app rt-f7", "one caption over two boxes greptile-40"])
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
    [*HOME, el("@ca", "Button", "Create account", 1600, w=400)],
], ids=["an error rt-f8", "an onboarding form rt-f11", "the app still offering create account greptile-43"])
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


def test_a_send_that_lands_on_a_screen_seen_before_the_wall_is_no_account_greptile_41(tmp_path, monkeypatch, identity):
    """Greptile on #41 (item 41): the guest app's home has tabs and no sign-in control, so a send that came back to it
    counted as an account made, and every later wall was skipped."""
    def app(clock):
        phone = sign_up_app()(clock)
        phone.start, phone.taps[("home", "Characters")] = "home", "wall"
        phone.taps[("form", "Create account")] = "home"
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, app, allow_account_create=True)
    stage.run_tour(ex)
    assert phone.typed[:3] == [EMAIL, PASSWORD, NAME] and ex.account_state == "sent"
    assert ex.account[0] == "s02: stopped at the wall, the form was sent, but it came back to s01, seen before the wall"


GUEST_HOME = [el("@g0", "TextView", "Discover AI characters", 200, w=700),
              el("@g1", "TextView", "Talk to anyone, any time. Thousands of characters are waiting.", 400, h=200),
              el("@go", "Button", "Start chatting", 1900), el("@li", "Button", "Log in", 200, x=820, w=200, h=90)]


SIGN_UP_LINK = el("@su", "Button", "Sign up", 320, x=820, w=200, h=70)
CTA_HOMES = [[*GUEST_HOME[:2], GUEST_HOME[3], SIGN_UP_LINK, el("@go", kind, "Start chatting", 1900)]
             for kind in ("View", "ImageView", "FrameLayout")]


@pytest.mark.parametrize("home", [GUEST_HOME, [*GUEST_HOME, SIGN_UP_LINK],
                                  [*GUEST_HOME, el("@wl", "Button", "Watch later", 1600, w=400)], *CTA_HOMES],
                         ids=["log in rt-f5a", "a sign-up link rt-f5b", "a content word rt-f5c",
                              "a View CTA rt-r2-3", "an ImageView CTA rt-r2-3", "a FrameLayout CTA rt-r2-3"])
def test_a_usable_guest_home_is_no_wall(tmp_path, monkeypatch, identity, home):
    def app(clock):
        phone = sign_up_app()(clock)
        phone.screens["wall"] = screen(7, *home)
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
    assert ex.secrets == ["offline-test-handle", EMAIL, PASSWORD, NAME]
    assert ex.parts == ["Quinlan", "Testperson", "tester.q7"]
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


def test_an_icon_only_close_reads_the_deny_words_it_read_before_rt_s_l1():
    """rt-s on #46 (LOW 1): item 43's wall words reached denied(), so with the flag off an X whose id names "create
    account" became tappable. A control's deny words are as they were."""
    def close(ident: str) -> ob.Candidate:
        return ob.Candidate(f"Close {ident}", "ImageButton", Rect(x=950, y=200, w=90, h=90), "@x", "", ident=ident)
    assert [ob.denied(close(i)) for i in ("createAccountClose", "register_dismiss", "login_close")] == \
        ["create account", None, None]


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


def test_a_caption_over_boxes_side_by_side_names_only_the_box_it_lies_over_most_greptile_40():
    """Greptile on #41 (item 40): a wide "Email" caption over two boxes side by side named both, so the test email
    could go into the second one."""
    def kinds(caption: dict) -> list[str]:
        row = [caption, el("@a", "EditText", "", 500, w=440), el("@b", "EditText", "", 500, x=550, w=440)]
        return [ob.field_kind(e, row) for e in row[1:]]
    assert kinds(el("@c", "TextView", "Email", 420, h=60)) == ["", ""]
    assert kinds(el("@c", "TextView", "Email", 420, w=600, h=60)) == ["email", ""]


ATTESTED = ("I'm 18+, continue", "Yes, I'm over 18 - Next", "I understand, continue", "Acknowledge and continue",
            "I'm an adult, let's go")
# a form's button that says more than that it sends the form: an attestation inside it, or anything else (RT-R3-1)
MORE = ("OK, continue", "Got it, continue", "Continue, I'm 18 or older", "Create account (I am 18 or older)",
        "Sign up - 18 and older only", "Let's go, I'm 18", "Continue (18 or over)", "Next, I'm of legal age")


@pytest.mark.parametrize("label", (*ATTESTED, *MORE, "Continue"))
def test_an_attestation_is_a_consent_and_the_button_says_only_that_it_sends_rt_r2_1(tmp_path, monkeypatch, identity,
                                                                                     label):
    form = form_with(el("@tos", "TextView", "By continuing you confirm you are 18 or older and accept our Terms", 1550,
                        h=80), el("@go2", "Button", label, 1700), without="@go")

    def app(clock):
        phone = sign_up_app(after_way=form)(clock)
        phone.taps[("form", label)] = "next"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    if label in ATTESTED:
        assert ex.account == [f"s01: stopped at the wall, it asks for a consent or an attestation ({label!r})"]
    elif label in MORE:
        assert ex.account == [f"s01: stopped at the wall, the form's button {label!r} says more than that it sends "
                              "the form"]
    else:  # a plain button under "By continuing you agree" is implicit, Aadi's call (README)
        assert ex.account_state == "made" and taps(phone)[-1] == label
        return
    assert not phone.typed and label not in taps(phone)


@pytest.mark.parametrize("label", ["Continue without registering", "Remind me later", "No thanks"])
def test_a_guest_way_drawn_as_a_link_is_taken_rt_r2_2(tmp_path, monkeypatch, identity, label):
    def app(clock):
        link = el("@g", "TextView", label, 1300, x=340, w=400, h=80)
        phone = sign_up_app(wall=[TITLE, link, EMAIL_WAY, GOOGLE, LOG_IN])(clock)
        phone.taps[("wall", label)] = "home"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert taps(phone) == [label] and not phone.typed
    assert ex.account == [f"s01: went on without an account ({label!r})"]


def test_guest_words_from_the_red_team_round_2():
    ways = ("Continue without registering", "Continue without login", "Remind me later", "No thanks", "Just browsing",
            "Continue as visitor", "Explore first", "Let's continue as guest", "Browse anonymously")
    assert all(ob.GUEST.search(w) for w in ways) and not ob.GUEST.search("Sign up first")


def test_a_picture_the_icon_pass_named_is_no_control_on_the_way_in():
    def picture(label):
        return ob.Candidate(label, "picture", Rect(x=0, y=0, w=500, h=500), "@p", label)
    assert not any(ob.shaped(picture(label)) for label in ("Sign in illustration", "Guest", "Sign up with email"))


def test_a_character_name_after_the_sign_up_is_no_name_box_rt_r2_4(tmp_path, monkeypatch, identity):
    two = [e for e in FORM if e["ref"] not in ("@c3", "@f3")]
    character = [el("@o0", "TextView", "Create your first character", 200),
                 el("@o1", "TextView", "Character name", 420, h=60), el("@o2", "EditText", "", 500),
                 el("@o3", "Button", "Create", 1700)]

    def app(clock):
        phone = sign_up_app(after_way=two, after_form=character)(clock)
        phone.taps[("next", "Create")] = "home"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.typed == [EMAIL, PASSWORD] and ("next", "@o2") not in phone.values and "Create" not in taps(phone)
    assert ex.account[-1].endswith("shows no sign of an account") and ex.account_state == "sent"


def test_a_next_step_form_whose_button_creates_is_the_end_rt_r2_4(tmp_path, monkeypatch, identity):
    two = [e for e in FORM if e["ref"] not in ("@c3", "@f3")]
    profile = [el("@o0", "TextView", "Set up your profile", 200), el("@o1", "TextView", "Your name", 420, h=60),
               el("@o2", "EditText", "", 500), el("@o3", "Button", "Create", 1700)]
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_way=two, after_form=profile))
    assert phone.typed == [EMAIL, PASSWORD] and "Create" not in taps(phone)
    assert ex.account[-1].endswith("shows no sign of an account")


LOG_IN_FORM = [el("@lt", "TextView", "Log in to your account", 200),
               el("@c1", "TextView", "Email", 420, h=60), el("@f1", "EditText", "", 500),
               el("@c2", "TextView", "Password", 720, h=60), el("@f2", "EditText", "", 800),
               el("@li", "Button", "Log in", 1100), el("@su", "Button", "Sign up", 1700)]


def test_a_log_in_form_is_never_filled_and_its_sign_up_link_is_taken_rt_r2_5(tmp_path, monkeypatch, identity):
    def app(clock):
        phone = sign_up_app(wall=LOG_IN_FORM)(clock)
        phone.taps[("wall", "Sign up")] = "form"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.values == {("form", "@f1"): EMAIL, ("form", "@f2"): PASSWORD, ("form", "@f3"): NAME}
    assert taps(phone)[0] == "Sign up" and "Log in" not in taps(phone)
    assert ex.account_state == "made" and ex.account[-1].startswith("s01: signed up with the test identity")


def test_a_log_in_form_whose_button_only_continues_is_never_filled_greptile_43(tmp_path, monkeypatch, identity):
    """Greptile on #37 (item 43): only a log-in button told a log-in form apart, so one whose button says "Continue"
    got the test identity. Offering to reset a forgotten password makes it a log-in form too."""
    log_in = [*LOG_IN_FORM[:5], el("@go", "Button", "Continue", 1100),
              el("@fp", "Button", "Forgot password?", 1300, w=400, h=80), el("@su", "Button", "Sign up", 1700)]

    def app(clock):
        phone = sign_up_app(wall=log_in)(clock)
        phone.taps[("wall", "Sign up")] = "form"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.values == {("form", "@f1"): EMAIL, ("form", "@f2"): PASSWORD, ("form", "@f3"): NAME}
    assert taps(phone)[0] == "Sign up" and "Continue" not in taps(phone) and ex.account_state == "made"


@pytest.mark.parametrize("label", ["Create account", "Register"])
def test_a_wall_that_only_asks_to_create_an_account_is_a_wall_greptile_43(tmp_path, monkeypatch, identity, label):
    """Greptile on #37 (item 43): a wall was recognized by sign-in words only, so one offering nothing but "Create
    account" or "Register" was explored as a screen."""
    def app(clock):
        phone = sign_up_app(wall=[TITLE, el("@ca", "Button", label, 1500)])(clock)
        phone.taps[("wall", label)] = "form"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert taps(phone)[0] == label and phone.typed == [EMAIL, PASSWORD, NAME] and ex.account_state == "made"


def test_name_parts_are_redacted_as_whole_words_only_rt_r2_6():
    parts = stage.identity_parts({"email": "ann.test@example.org", "password": "x", "name": "Ann Lee"})
    shown = [el("@a", "Button", "Announcements", 300), el("@b", "Button", "Fleet", 500),
             el("@c", "TextView", "Hi Ann! Your handle is @ann.test", 700)]
    reply = {"content": [{"type": "text", "text": ob.ELEMENTS_PREFIX + json.dumps(shown)}]}
    _, elements, hits = ob.redact(reply, Image.new("RGB", (1080, 2400)), [], parts)
    assert [e["text"] for e in elements] == ["Announcements", "Fleet", "Hi [redacted]! Your handle is @[redacted]"]
    assert hits == 1


def test_the_keyboard_is_painted_over_once_a_value_is_typed_rt_r2_7(tmp_path, monkeypatch, identity):
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_form=VERIFY))

    def covered(sid: str) -> bool:
        lower = Image.open(ex.out / "states" / f"{sid}.png").convert("RGB").crop((0, 1500, 1080, 2400))
        return np.asarray(lower).max() == 0
    assert [s.sid for s in ex.states] == ["s01", "s02", "s03", "s04"] and phone.typed == [EMAIL, PASSWORD, NAME]
    assert [covered(s.sid) for s in ex.states] == [False, False, True, False]  # closed before the send


@pytest.mark.parametrize("input_method, window, box", [
    ("mInputShown=false", "mFrame=[0,1500][1080,2400]", None),
    ("mInputShown=true", "frame=[0,1700][1080,2400]", (0, 1700, 1080, 2400)),
    (None, None, (0, 1200, 1080, 2400)),
], ids=["hidden", "a frame", "unreadable: the lower half"])
def test_where_the_keyboard_is_painted(tmp_path, monkeypatch, input_method, window, box):
    ex, _ = new_explorer(tmp_path, monkeypatch, sign_up_app())
    answers = {"input_method": input_method, "window": window}
    monkeypatch.setattr(stage, "adb_shell", lambda serial, args: answers[args[1]])
    image = Image.new("RGB", (1080, 2400), (200, 200, 200))
    ex.cover_keyboard(image)
    pixels = np.asarray(image)
    painted = pixels.max(axis=2) == 0
    if box is None:
        assert not painted.any()
    else:
        x0, y0, x1, y1 = box
        assert painted[y0:y1, x0:x1].all() and not painted[:y0].any()


ERROR = [el("@e0", "TextView", "Something went wrong", 200), el("@ok", "Button", "OK", 1700)]


def test_the_keyboard_is_painted_over_whenever_it_is_up_after_a_value_is_typed_rt_r4_5(tmp_path, monkeypatch, identity):
    """Greptile 4156663419. The sign-up stops with the keyboard still up, its suggestion strip (red here) showing a
    typed word: every later capture paints it while dumpsys says it is up."""
    def app(clock):
        phone = sign_up_app(after_form=ERROR)(clock)
        ImageDraw.Draw(phone.screens["next"].image).rectangle((0, 1500, 1080, 1620), fill=(255, 0, 0))
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert ex.account[-1].endswith("shows no sign of an account") and ex.typing
    phone.focus = ("next", "@e0")
    up = ex.observe()
    assert up.painted == Rect(x=0, y=1500, w=1080, h=900) and up.image.getpixel((540, 1560)) == (0, 0, 0)
    assert Image.open(ex.scratch / "now.png").getpixel((540, 1560)) == (0, 0, 0)
    ok = next(c for c in up.cands if c.label == "OK")
    assert not ex.shows(ok, ok, up)  # a tap there would land on a key
    phone.focus = None
    assert ex.observe().painted is None


def test_a_sign_up_tap_never_lands_on_the_keyboard_rt_r4_4(tmp_path, monkeypatch, identity):
    """Greptile 4156663379. The form's button (y 1700) lies under the keyboard once the name is typed: BACK closes the
    keyboard first."""
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app())
    assert ex.account_state == "made" and not any(kind == "key" for kind, *_ in phone.log)
    assert phone.log.index(("back", "form")) < phone.log.index(("tap", "form", "Create account"))


HIGH_FORM = [FORM[0], el("@c1", "TextView", "Email", 320, h=60), el("@f1", "EditText", "", 400),
             el("@c2", "TextView", "Password", 620, h=60), el("@f2", "EditText", "", 700),
             el("@c3", "TextView", "Name", 920, h=60), el("@f3", "EditText", "", 1000),
             el("@go", "Button", "Create account", 1700)]


@pytest.mark.parametrize("form, typed, over", [(FORM, [EMAIL, PASSWORD], "the name box"),
                                               (HIGH_FORM, [EMAIL, PASSWORD, NAME],
                                                "the form's button 'Create account'")], ids=["a box", "the button"])
def test_when_dumpsys_cannot_say_a_control_under_the_lower_half_stops_the_sign_up_rt_r4_4(tmp_path, monkeypatch,
                                                                                          identity, form, typed, over):
    monkeypatch.setattr(FormPhone, "dumpsys", lambda self, args: "")
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_way=form))
    assert ex.account[-1] == f"s01: stopped at the wall, the keyboard may lie over {over}"
    assert phone.typed == typed and ("back", "form") not in phone.log and "Create account" not in taps(phone)


def test_a_log_in_link_on_a_form_that_asks_for_a_name_is_not_its_button_greptile_p1_3(tmp_path, monkeypatch,
                                                                                         identity):
    form = form_with(el("@li", "Button", "Log in", 1400, w=300, h=80), without="@have")
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_way=form))
    assert phone.values == {("form", "@f1"): EMAIL, ("form", "@f2"): PASSWORD, ("form", "@f3"): NAME}
    assert ex.account_state == "made" and "Log in" not in taps(phone)


def test_a_crop_the_keyboard_was_painted_over_shows_nothing_rt_r3_3(tmp_path, monkeypatch):
    """dumpsys unreadable: the lower half of every capture taken while typing is painted. Painted for a whole run of
    the JanitorAI-like app, a drawer over the tab bar is as black as the tab bar was, so the tab is not tapped
    through the drawer."""
    monkeypatch.setattr(stage, "adb_shell", lambda serial, args: None)
    init = stage.Explorer.__init__

    def typing(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.typing = True
    monkeypatch.setattr(stage.Explorer, "__init__", typing)
    ex, phone = run_explorer(tmp_path, monkeypatch, janitor_like)
    assert all(Image.open(ex.out / "states" / f"{s.sid}.png").getpixel((540, 2000)) == (0, 0, 0) for s in ex.states)
    assert ("shot", "drawer") in phone.log and ("tap", "drawer", "118,2253") not in phone.log


def test_a_hint_is_no_consent_but_a_button_or_toggle_still_attests_rt_r3_1():
    def control(label, kind):
        return ob.Candidate(label, kind, Rect(x=0, y=0, w=500, h=100), "@c", label)
    hints = ("At least 8 characters", "8+ characters", "I'll never share it")
    assert not any(ob.consents(control(h, "TextView"), tapped=False) for h in hints)
    assert all(ob.consents(control(h, "TextView")) for h in hints)  # a control to tap is held to every rule
    assert ob.consents(control("Yes, send me news", "CheckBox"), tapped=False)  # a toggle may be ticked already
    assert ob.consents(control("I'm 18+", "Button"), tapped=False)
    assert ob.consents(control("I accept the Terms", "TextView"), tapped=False)
    assert not any(ob.PLAIN_SUBMIT.search(label) for label in MORE)


@pytest.mark.parametrize("hint", ["At least 8 characters", "8+ characters"])
def test_a_password_hint_stops_nothing_rt_r3_1(tmp_path, monkeypatch, identity, hint):
    form = form_with(el("@hint", "TextView", hint, 930, w=500, h=50))
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(after_way=form))
    assert ex.account_state == "made" and phone.typed == [EMAIL, PASSWORD, NAME], ex.account


def test_a_sign_up_in_two_steps_goes_through(tmp_path, monkeypatch, identity):
    """The email first ("Continue"), then the password and the name ("Create account")."""
    first = [el("@ft", "TextView", "What's your email?", 200, w=600), el("@c1", "TextView", "Email", 420, h=60),
             el("@f1", "EditText", "", 500), el("@go1", "Button", "Continue", 1700)]
    second = [el("@ft2", "TextView", "Almost there", 200, w=600), el("@c2", "TextView", "Password", 420, h=60),
              el("@f2", "EditText", "", 500), el("@c3", "TextView", "Name", 720, h=60), el("@f3", "EditText", "", 800),
              el("@go", "Button", "Create account", 1700)]

    def app(clock):
        phone = sign_up_app(after_way=first)(clock)
        phone.screens["second"] = screen(21, *second)
        phone.taps.update({("form", "Continue"): "second", ("second", "Create account"): "next"})
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.values == {("form", "@f1"): EMAIL, ("second", "@f2"): PASSWORD, ("second", "@f3"): NAME}
    assert ex.account_state == "made", ex.account


def test_a_name_box_is_one_that_says_only_name_rt_r3_2(tmp_path, monkeypatch, identity):
    names = ("Name", "Your name", "Full name", "Enter your name", "Your full name", "Display name",
             "What should we call you?")
    others = ("Name your companion", "Name your AI", "Name of your business", "Username", "Character name")
    assert all(ob.PERSON_NAME.search(n) for n in names) and not any(ob.PERSON_NAME.search(o) for o in others)
    two = [e for e in FORM if e["ref"] not in ("@c3", "@f3")]
    companion = [el("@o0", "TextView", "Meet your AI friend", 200, w=600),
                 el("@o1", "TextView", "Name your companion", 420, h=60), el("@o2", "EditText", "", 500),
                 el("@o3", "Button", "Continue", 1700)]

    def app(clock):
        phone = sign_up_app(after_way=two, after_form=companion)(clock)
        phone.taps[("next", "Continue")] = "home"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.typed == [EMAIL, PASSWORD] and "Continue" not in taps(phone)
    assert ex.account[-1].endswith("shows no sign of an account") and ex.account_state == "sent"


# an attestation inside the way to the sign-up (RT-R4-1), or a label that names email without a way to it
WAYS = ("Continue with email, I'm 18 or older", "Sign up with email (18 or over)", "Sign up - I am of legal age")


@pytest.mark.parametrize("label", [*WAYS, "Email"], ids=["rt_r4_1 a", "rt_r4_1 b", "rt_r4_1 c", "no verb"])
def test_a_way_to_the_sign_up_is_taken_by_its_whole_label_only(tmp_path, monkeypatch, identity, label):
    def app(clock):
        phone = sign_up_app(wall=[TITLE, el("@email", "Button", label, 1500), GOOGLE, LOG_IN])(clock)
        phone.taps[("wall", label)] = "form"
        return phone
    phones = []

    def factory(clock):
        phones.append(app(clock))
        return phones[-1]
    assert stop_reason(tmp_path, monkeypatch, factory) == ("s01: stopped at the wall, no way to an email sign-up "
                                                           f"recognized ({label!r} is not one, whole)")
    assert not phones[0].typed and label not in taps(phones[0])


@pytest.mark.parametrize("label", ["Get started with email", "Continue with your email", "Use email", "Register"])
def test_a_whole_way_to_the_sign_up_is_taken(tmp_path, monkeypatch, identity, label):
    def app(clock):
        phone = sign_up_app(wall=[TITLE, el("@email", "Button", label, 1500), GOOGLE, LOG_IN])(clock)
        phone.taps[("wall", label)] = "form"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert taps(phone)[0] == label and ex.account_state == "made", ex.account


def test_a_name_step_after_the_sign_up_is_the_end_rt_r4_2(tmp_path, monkeypatch, identity):
    """By its box, "Create your character / Name" is a name step; after a send, a form that asks for neither the email
    nor the password ends the sign-up."""
    two = [e for e in FORM if e["ref"] not in ("@c3", "@f3")]
    character = [el("@o0", "TextView", "Create your character", 200, w=600), el("@o1", "TextView", "Name", 420, h=60),
                 el("@o2", "EditText", "", 500), el("@o3", "Button", "Continue", 1700)]

    def app(clock):
        phone = sign_up_app(after_way=two, after_form=character)(clock)
        phone.taps[("next", "Continue")] = "home"
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert phone.typed == [EMAIL, PASSWORD] and "Continue" not in taps(phone)
    assert ex.account[-1].endswith("shows no sign of an account") and ex.account_state == "sent"


def test_a_send_that_leaves_the_screen_as_it_was_sent_nothing_rt_r4_4(tmp_path, monkeypatch, identity):
    def app(clock):
        phone = sign_up_app()(clock)
        del phone.taps[("form", "Create account")]
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert ex.account[-1] == "s01: stopped at the wall, the form's button 'Create account' changed nothing"
    assert ex.account_state == "" and taps(phone)[-1] == "Create account"


class InlineVerify(FormPhone):
    """A form whose send keeps the same screen, typed values and all, and adds a line under it."""

    def tap(self, x: int, y: int) -> None:
        super().tap(x, y)
        if self.log[-1] == ("tap", "form", "Create account"):
            form = self.screens["form"]
            line = el("@v", "TextView", "Check your email to verify your account", 1880, h=60)
            self.screens["form"] = Screen([*form.elements, line], form.image, form.package)


def test_an_inline_check_your_email_on_the_same_screen_is_a_send_greptile_4157597418(tmp_path, monkeypatch, identity):
    """The screen after the send matches the form's recorded state; its new words still say the form was sent."""
    def app(clock):
        phone = sign_up_app(phone=InlineVerify)(clock)
        del phone.taps[("form", "Create account")]
        return phone
    ex, phone = launched(tmp_path, monkeypatch, app)
    assert ex.account_state == "verify" and (ex.run_dir / "needs-human.md").exists()
    assert ex.account[-1].startswith("s01: stopped at the wall, an email verification step on s")


def test_a_sign_up_that_stops_mid_form_keeps_the_keyboard_painted_rt_r4_5(tmp_path, monkeypatch, identity):
    """The red team's case: the password box takes no focus, so the sign-up stops right after the email, with the
    keyboard still up."""
    class NoFocusOnPassword(FormPhone):
        def tap(self, x, y):
            super().tap(x, y)
            if self.focus == ("form", "@f2"):
                self.focus = ("form", "@f1")
    ex, phone = launched(tmp_path, monkeypatch, sign_up_app(phone=NoFocusOnPassword))
    assert phone.typed == [EMAIL] and "the box in focus is not the empty password box" in ex.account[-1]
    assert ex.typing and ex.observe().painted == Rect(x=0, y=1500, w=1080, h=900)
    assert Image.open(ex.scratch / "now.png").getpixel((540, 2000)) == (0, 0, 0)


def test_pictures_side_by_side_at_its_foot_never_pass_a_wall_off_as_a_tab_bar(tmp_path, monkeypatch, identity):
    """A picture the icon pass found is no control: two at the foot of a sign-up wall are no tab bar, so the wall
    stays a wall."""
    ex, _ = new_explorer(tmp_path, monkeypatch, sign_up_app(), allow_account_create=True)
    tiles = [unlisted(ex, 40, 2150, 500, 2330, "picture", "left tile"),
             unlisted(ex, 580, 2150, 1040, 2330, "picture", "right tile")]
    monkeypatch.setattr(ex, "ask", lambda *a: IconPass(names=[], unlisted=tiles))
    wall = ex.current = ex.record(ex.observe(), None, None, None)
    assert [c.label for c in wall.cands if c.kind == "picture"] == ["left tile", "right tile"]
    assert ex.account_wall(wall)


def test_the_tabs_set_past_a_launch_wall_are_never_pictures(tmp_path, monkeypatch, identity):
    """Past a wall, home's tabs are set again (rehome): a picture between two tabs is never one of them."""
    guest = el("@guest", "Button", "Continue as guest", 1300)

    def app(clock):
        phone = sign_up_app(wall=[TITLE, guest, EMAIL_WAY, GOOGLE, LOG_IN])(clock)
        phone.screens["home"] = screen(4, el("@h0", "TextView", "Welcome back", 200),
                                       el("@h1", "Button", "Characters", 700),
                                       el("@home", "Button", "Home", 2200, x=0, w=360),
                                       el("@me", "Button", "Me", 2200, x=720, w=360))
        return phone
    ex, phone = new_explorer(tmp_path, monkeypatch, app, allow_account_create=True)
    tile = unlisted(ex, 380, 2210, 700, 2310, "picture", "promo tile")
    real = ex.ask
    monkeypatch.setattr(ex, "ask", lambda prompt, step, *a: IconPass(names=[], unlisted=[tile])
                        if prompt == "icons" and phone.screen == "home" else real(prompt, step, *a))
    ex.relaunch(first=True)
    assert ex.root.sid != "s01" and [c.label for c in ex.root.cands if c.kind == "picture"] == ["promo tile"]
    assert [t.label for t in ex.tabs] == ["Home", "Me"]
