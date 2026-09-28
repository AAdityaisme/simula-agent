"""A hung mobile-mcp call times out on the client, the server's process tree is killed, and a new session
answers. Reads retry once after the respawn; actions don't (the first one may have landed)."""

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
