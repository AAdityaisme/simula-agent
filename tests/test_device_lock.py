"""One lock per device: two explores on two emulators take two locks; a second explore on the same one waits.
--device, else ANDROID_SERIAL, else the only device online picks the serial."""

import threading

import pytest

from simula.device import devices


@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, "LOCK_DIR", tmp_path)
    return tmp_path


def test_two_serials_take_two_locks(locks):
    with devices.emulator_lock("emulator-5554", wait_s=0), devices.emulator_lock("emulator-5556", wait_s=0):
        assert sorted(p.name for p in locks.iterdir()) == ["simula-emu-emulator-5554.lock",
                                                          "simula-emu-emulator-5556.lock"]
    assert not list(locks.iterdir())


def test_the_same_serial_waits_for_the_first_to_finish(locks):
    held = threading.Event()
    release = threading.Event()

    def first():
        with devices.emulator_lock("emulator-5554", wait_s=0):
            held.set()
            release.wait(5)
    thread = threading.Thread(target=first)
    thread.start()
    held.wait(5)
    with pytest.raises(TimeoutError):
        with devices.emulator_lock("emulator-5554", wait_s=0):
            pass
    release.set()
    thread.join(5)
    with devices.emulator_lock("emulator-5554", wait_s=0):
        assert (locks / "simula-emu-emulator-5554.lock").exists()


def test_serial_comes_from_the_flag_then_the_env_then_the_only_device(monkeypatch):
    monkeypatch.setattr(devices, "adb", lambda: "adb")
    monkeypatch.setattr(devices, "online", lambda tool: ["emulator-5554"])
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    assert devices.resolve_serial(None) == "emulator-5554"
    monkeypatch.setenv("ANDROID_SERIAL", "emulator-5556")
    assert devices.resolve_serial(None) == "emulator-5556"
    assert devices.resolve_serial("R58M123") == "R58M123"


def test_several_devices_and_no_flag_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(devices, "adb", lambda: "adb")
    monkeypatch.setattr(devices, "online", lambda tool: ["emulator-5554", "emulator-5556"])
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    with pytest.raises(SystemExit, match="--device"):
        devices.resolve_serial(None)


def test_the_lock_holds_the_holders_pid(locks):
    import os
    with devices.emulator_lock("emulator-5554", wait_s=0):
        assert (locks / "simula-emu-emulator-5554.lock" / "pid").read_text() == str(os.getpid())


def test_a_lock_whose_holder_died_is_broken(locks):
    import subprocess
    import sys
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    stale = locks / "simula-emu-emulator-5554.lock"
    stale.mkdir()
    (stale / "pid").write_text(str(dead.pid))
    with devices.emulator_lock("emulator-5554", wait_s=0):
        assert (stale / "pid").read_text() != str(dead.pid)
    assert not stale.exists()


def test_a_lock_whose_holder_lives_is_waited_on(locks):
    import os
    held = locks / "simula-emu-emulator-5554.lock"
    held.mkdir()
    (held / "pid").write_text(str(os.getppid()))
    with pytest.raises(TimeoutError):
        with devices.emulator_lock("emulator-5554", wait_s=0):
            pass
    assert held.exists()


def test_reclaiming_leaves_a_live_holders_lock(locks):
    import os
    held = locks / "simula-emu-emulator-5554.lock"
    held.mkdir()
    (held / "pid").write_text(str(os.getpid()))
    devices.reclaim(held)
    assert (held / "pid").exists()


def test_two_emulators_of_one_avd_are_refused(monkeypatch):
    from simula.stages import explore as stage
    monkeypatch.setattr(stage, "adb", lambda: "adb")
    monkeypatch.setattr(stage, "online", lambda tool: ["emulator-5554", "emulator-5556"])
    monkeypatch.setattr(stage, "adb_shell", lambda serial, args: "simula")
    with pytest.raises(SystemExit, match="same AVD"):
        stage.refuse_twins("emulator-5554", "simula")
    monkeypatch.setattr(stage, "adb_shell", lambda serial, args: "simula" if serial == "emulator-5554" else "other")
    stage.refuse_twins("emulator-5554", "simula")


def test_the_guard_never_follows_a_planted_symlink(locks, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    stale = locks / "simula-emu-emulator-5554.lock"
    stale.mkdir()
    (locks / "simula-emu-emulator-5554.lock.guard").symlink_to(victim)
    with pytest.raises(OSError):
        devices.reclaim(stale)
    assert victim.read_text() == "keep me"
