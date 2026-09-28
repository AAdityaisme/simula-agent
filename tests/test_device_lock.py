"""One lock per device: two explores on two emulators take two locks; a second explore on the same one waits.
--device, else ANDROID_SERIAL, else the only device online picks the serial."""

import threading

import pytest

from simula import doctor


@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "LOCK_DIR", tmp_path)
    return tmp_path


def test_two_serials_take_two_locks(locks):
    with doctor.emulator_lock("emulator-5554", wait_s=0), doctor.emulator_lock("emulator-5556", wait_s=0):
        assert sorted(p.name for p in locks.iterdir()) == ["simula-emu-emulator-5554.lock",
                                                          "simula-emu-emulator-5556.lock"]
    assert not list(locks.iterdir())


def test_the_same_serial_waits_for_the_first_to_finish(locks):
    held = threading.Event()
    release = threading.Event()

    def first():
        with doctor.emulator_lock("emulator-5554", wait_s=0):
            held.set()
            release.wait(5)
    thread = threading.Thread(target=first)
    thread.start()
    held.wait(5)
    with pytest.raises(TimeoutError):
        with doctor.emulator_lock("emulator-5554", wait_s=0):
            pass
    release.set()
    thread.join(5)
    with doctor.emulator_lock("emulator-5554", wait_s=0):
        assert (locks / "simula-emu-emulator-5554.lock").exists()


def test_serial_comes_from_the_flag_then_the_env_then_the_only_device(monkeypatch):
    monkeypatch.setattr(doctor, "adb", lambda: "adb")
    monkeypatch.setattr(doctor, "online", lambda tool: ["emulator-5554"])
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    assert doctor.resolve_serial(None) == "emulator-5554"
    monkeypatch.setenv("ANDROID_SERIAL", "emulator-5556")
    assert doctor.resolve_serial(None) == "emulator-5556"
    assert doctor.resolve_serial("R58M123") == "R58M123"


def test_several_devices_and_no_flag_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(doctor, "adb", lambda: "adb")
    monkeypatch.setattr(doctor, "online", lambda tool: ["emulator-5554", "emulator-5556"])
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    with pytest.raises(SystemExit, match="--device"):
        doctor.resolve_serial(None)
