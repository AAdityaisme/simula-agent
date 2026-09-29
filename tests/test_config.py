"""Generality rule: the only per-app input is which app to run."""

import tomllib

import pytest

from simula import config
from simula.stages import ROLES

APP_FILES = sorted((config.CONFIG / "apps").glob("*.toml"))


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: p.stem)
def test_app_config_is_package_only(path):
    assert set(tomllib.loads(path.read_text())) == {"package"}


def test_every_role_names_a_known_model():
    known = set(config.models())
    for profile in ("real", "dev"):
        for role in config.roles(profile).values():
            assert role["model"] in known


def test_haiku_never_gets_an_effort_in_any_profile():
    for profile in ("real", "dev"):
        for role in config.roles(profile).values():
            if not config.models()[role["model"]]["supports_effort"]:
                assert "effort" not in role


def test_caps_cover_every_stage():
    assert set(config.profiles()["caps_usd"]) == set(config.STAGES)


def test_budgets_are_run_flags():
    assert config.budget("deep") == {"actions": 80, "wall_minutes": 25}
    assert config.budget("transfer") == {"actions": 40, "wall_minutes": 12}


def test_doctor_probes_every_role_at_its_real_settings():
    from simula.doctor import role_probes
    probes = role_probes()
    assert ("claude-opus-5-5", "xhigh", 128000) in probes
    assert ("gpt-6-sol", "high", 4096) in probes
    assert ("gpt-6-luna", "high", 4096) in probes
    probed_roles = {role.removesuffix(" fallback") for roles in probes.values() for role in roles}
    assert set(config.roles("real")) - {"jev"} <= probed_roles


def test_every_role_belongs_to_a_stage_so_changing_it_reruns_that_stage():
    staged = {role for roles in ROLES.values() for role in roles}
    for profile in ("real", "dev"):
        assert set(config.roles(profile)) == staged
