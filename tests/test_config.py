"""Generality rule: the only per-app input is which app to run."""

import ast
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


def test_doctor_probes_every_role_at_the_max_tokens_its_code_asks_for():
    from simula.doctor import role_probes
    roles, models = config.roles("real"), config.models()
    probes = role_probes()
    called = {name for name, role in roles.items() if name != "jev" and "max_tokens" in role}
    assert called == set(roles) - {"jev", "pairwise"}, "every role but one that no code calls yet has a budget"
    for name in called:
        role = roles[name]
        for m in filter(None, (role["model"], role.get("declared_fallback"))):
            assert (m, role.get("effort"), config.max_tokens(role, m)) in probes, name
    assert any(n > models[m]["stream_above"] for m, _, n in probes), "a streamed call is probed streamed"


def test_a_roles_max_tokens_is_capped_at_its_models_max_out():
    model = "claude-haiku-4-5-20251001"
    ceiling = config.models()[model]["max_out"]
    assert config.max_tokens({"model": model, "max_tokens": ceiling + 1}) == ceiling
    assert config.max_tokens({"model": model, "max_tokens": 1000}) == 1000


class BudgetChoices(ast.NodeVisitor):
    """Each place a module chooses a call's output budget: a max_tokens= keyword that isn't config.max_tokens(...) or
    a pass-through of a parameter named max_tokens, and each read of a ["max_tokens"] or ["max_out"] key, with the
    function it sits in. A comment or a string can't hide one, and a constant or a renamed role can't pass as one."""

    def __init__(self):
        self.where, self.found = ["<module>"], set()

    def visit_FunctionDef(self, node):
        self.where.append(node.name)
        self.generic_visit(node)
        self.where.pop()

    def visit_keyword(self, node):
        value = node.value
        helper = (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                  and value.func.attr == "max_tokens" and isinstance(value.func.value, ast.Name)
                  and value.func.value.id == "config")
        passed_on = isinstance(value, ast.Name) and value.id == "max_tokens"
        if node.arg == "max_tokens" and not (helper or passed_on):
            self.found.add(self.where[-1])
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if isinstance(node.slice, ast.Constant) and node.slice.value in ("max_tokens", "max_out"):
            self.found.add(self.where[-1])
        self.generic_visit(node)


def test_every_call_takes_its_budget_from_the_one_helper():
    """One rule, one place: a call that chose its own number could ask for more than its model allows, or for another
    budget than the one doctor probes. The two allowed: the rule itself, and doctor's check that Haiku refuses effort,
    which asks for 16 tokens on no role's behalf."""
    choices = set()
    for path in sorted((config.ROOT / "simula").rglob("*.py")):
        visitor = BudgetChoices()
        visitor.visit(ast.parse(path.read_text()))
        choices |= {(str(path.relative_to(config.ROOT)), where) for where in visitor.found}
    assert choices == {("simula/config.py", "max_tokens"), ("simula/doctor.py", "haiku_effort_probe")}


def test_every_role_belongs_to_a_stage_so_changing_it_reruns_that_stage():
    staged = {role for roles in ROLES.values() for role in roles}
    for profile in ("real", "dev"):
        assert set(config.roles(profile)) == staged
