"""A hung mobile-mcp call times out on the client, the server's process tree is killed, and a new session
answers. Reads retry once after the respawn; actions don't (the first one may have landed)."""

import json
import os
import sys
from pathlib import Path

import pytest

from simula.device import mcp

FAKE = Path(__file__).parent / "fakes" / "hanging_mcp.py"


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "LIST_TIMEOUT_S", 2.0)
    monkeypatch.setattr(mcp, "ACTION_TIMEOUT_S", 2.0)
    monkeypatch.setattr(mcp, "STOP_TIMEOUT_S", 10.0)
    server = mcp.Server(cwd=tmp_path, command=[sys.executable, str(FAKE), str(tmp_path / "hung")])
    yield mcp.Phone(server, "com.example.app", tmp_path)
    server.close()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_hung_read_is_killed_respawned_and_retried(phone, tmp_path):
    _, elements = phone.elements()
    hung_pid = int((tmp_path / "hung").read_text())
    assert phone.server.respawns == 1
    assert elements[0]["text"] == f"pid {elements[0]['text'].split()[1]}" and str(hung_pid) not in elements[0]["text"]
    assert not alive(hung_pid)
    assert phone.list_seconds and phone.list_seconds[0] >= 2.0


def test_a_hung_action_times_out_without_a_retry(phone):
    with pytest.raises(mcp.McpTimeout):
        phone.tap(10, 10)
    assert phone.server.respawns == 1
    phone.elements()


class FlakyServer:
    """Answers the element list with uiautomator's transient dump failure a set number of times."""

    def __init__(self, failures: int):
        self.failures = failures

    def call(self, tool, timeout, **args):
        if tool == "mobile_list_available_devices":
            return {"content": [{"type": "text", "text": '{"devices": [{"id": "simula", "platform": "android"}]}'}]}
        if self.failures:
            self.failures -= 1
            return {"content": [{"type": "text", "text": "Error: Command failed: mobilecli dump ui: no XML content "
                                                         "found in uiautomator dump"}], "isError": True}
        return {"content": [{"type": "text", "text": 'Found these elements on screen: [{"ref": "@e1", "type": "x", '
                                                     '"coordinates": {"x": 0, "y": 200, "width": 10, "height": 10}}]'}]}


def test_a_failed_dump_is_read_again(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "LIST_RETRY_PAUSE_S", 0)
    phone = mcp.Phone(FlakyServer(2), "com.example.app", tmp_path)
    assert phone.elements()[1][0]["ref"] == "@e1" and len(phone.list_seconds) == 3
    with pytest.raises(mcp.McpReplyError):
        mcp.Phone(FlakyServer(3), "com.example.app", tmp_path).elements()


LOST = {"content": [{"type": "text", "text": 'Device "simula" not found. Use the mobile_list_available_devices tool '
                                             'to see available devices.. Please fix the issue and try again.'}]}


class LosesTheDevice(FlakyServer):
    """mobilecli's 'Device not found' answer, a set number of times, then a real reply. mobile-mcp sends it as
    text, without isError (its ActionableError)."""

    def __init__(self, losses: int):
        super().__init__(0)
        self.losses = losses

    def call(self, tool, timeout, **args):
        if tool == "mobile_get_foreground_app" and self.losses:
            self.losses -= 1
            return LOST
        if tool == "mobile_get_foreground_app":
            return {"content": [{"type": "text", "text": "Foreground app: Janitor (com.janitor.ai)"}]}
        return super().call(tool, timeout, **args)


def test_a_lost_device_is_asked_again(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "DEVICE_RETRY_PAUSE_S", 0)
    assert mcp.Phone(LosesTheDevice(5), "com.janitor.ai", tmp_path).foreground() == "com.janitor.ai"
    with pytest.raises(mcp.McpReplyError):
        mcp.Phone(LosesTheDevice(6), "com.janitor.ai", tmp_path).foreground()


class MissesTheDeviceAfterARespawn(FlakyServer):
    """The first element list hangs; the fresh server then lists no device for a set number of calls, and anything
    else asked meanwhile gets 'Device not found'."""

    def __init__(self, unlisted: int):
        super().__init__(0)
        self.unlisted, self.hung, self.respawns = unlisted, False, 0

    def respawn(self):
        self.respawns += 1

    def call(self, tool, timeout, **args):
        if tool == "mobile_list_elements_on_screen" and not self.hung:
            self.hung = True
            raise mcp.McpTimeout(f"{tool} took over {timeout:.0f}s")
        if self.respawns and self.unlisted:
            if tool == "mobile_list_available_devices":
                self.unlisted -= 1
                return {"content": [{"type": "text", "text": '{"devices": []}'}]}
            return LOST
        return super().call(tool, timeout, **args)


def test_a_respawn_waits_until_the_device_is_listed_again(tmp_path, monkeypatch):
    """Luzia's run 20261001-052746-71aa5a4: a swipe hung, and 19 s after the respawn the new server still didn't
    list the device, so the reads after it failed and the run stopped. The read waits for the list instead; a
    device still missing at START_TIMEOUT_S is lost."""
    monkeypatch.setattr(mcp, "DEVICE_RETRY_PAUSE_S", 0)
    monkeypatch.setattr(mcp, "LIST_RETRY_PAUSE_S", 0)
    server = MissesTheDeviceAfterARespawn(unlisted=20)
    assert mcp.Phone(server, "com.example.app", tmp_path).elements()[1][0]["ref"] == "@e1"
    assert server.respawns == 1 and server.unlisted == 0
    monkeypatch.setattr(mcp, "START_TIMEOUT_S", 0.05)
    with pytest.raises(mcp.McpReplyError, match="not found"):
        mcp.Phone(MissesTheDeviceAfterARespawn(unlisted=10 ** 6), "com.example.app", tmp_path).elements()

class RefusesActions(FlakyServer):
    """Answers every device action with an error, the way mobile-mcp reports a tap that didn't happen."""

    def call(self, tool, timeout, **args):
        if tool == "mobile_list_available_devices":
            return super().call(tool, timeout, **args)
        return {"content": [{"type": "text", "text": "Error: adb: device offline"}], "isError": True}


@pytest.mark.parametrize("action", [lambda p: p.tap(1, 1), lambda p: p.back(), lambda p: p.swipe("up"),
                                    lambda p: p.type_text("hi"), lambda p: p.launch(), lambda p: p.terminate()],
                         ids=["tap", "back", "swipe", "type_text", "launch", "terminate"])
def test_an_action_that_errors_raises(tmp_path, action):
    with pytest.raises(mcp.McpReplyError, match="failed"):
        action(mcp.Phone(RefusesActions(0), "com.example.app", tmp_path))


class Lists(FlakyServer):
    def __init__(self, devices):
        super().__init__(0)
        self.devices = devices

    def call(self, tool, timeout, **args):
        if tool == "mobile_list_available_devices":
            return {"content": [{"type": "text", "text": json.dumps({"devices": self.devices})}]}
        return super().call(tool, timeout, **args)


def device(id, name):
    return {"id": id, "name": name, "platform": "android"}


def test_the_device_whose_id_is_the_serial_wins(tmp_path):
    server = Lists([device("simula", "simula"), device("emulator-5556", "other")])
    assert mcp.Phone(server, "x", tmp_path, "emulator-5556", "other").device == "emulator-5556"
    assert mcp.Phone(server, "x", tmp_path, "emulator-5554", "simula").device == "simula"


def test_two_devices_of_one_avd_are_an_error(tmp_path):
    server = Lists([device("simula", "simula"), device("simula", "simula")])
    with pytest.raises(SystemExit, match="can't be told apart"):
        mcp.Phone(server, "x", tmp_path, "emulator-5554", "simula")
