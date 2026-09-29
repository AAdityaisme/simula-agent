"""Every schema a model fills must survive both SDKs' schema transforms without losing a field.

The trap (measured on anthropic 0.117.0, still true on 1.8.0): a dict field becomes an object that can only
be {}, and pydantic still accepts it, so a dict-shaped verdict would pass everything.
"""

import pytest
from anthropic.lib._parse._transform import transform_schema
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from simula.contracts import GATES, JUDGMENT, MODEL_FACING, ModelMeaning, Verdict


def objects(schema: dict):
    """Yields every object schema, following $defs."""
    defs = schema.get("$defs", {})
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref:
                name = ref.split("/")[-1]
                if name not in seen:
                    seen.add(name)
                    yield from walk(defs[name])
                return
            if node.get("type") == "object":
                yield node
            for key, value in node.items():
                if key != "$defs":
                    yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)
    yield from walk(schema)


def dict_fields(schema: dict) -> list[str]:
    return [o.get("title", "?") for o in objects(schema) if isinstance(o.get("additionalProperties"), dict)
            or o.get("additionalProperties") is True]


def emptied_objects(schema: dict) -> list[str]:
    return [o.get("title", "?") for o in objects(schema) if o.get("properties") == {}]


def depth(schema: dict) -> int:
    defs = schema.get("$defs", {})

    def walk(node, stack):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref:
                name = ref.split("/")[-1]
                if name in stack:
                    raise AssertionError(f"recursive schema via {name}")
                return walk(defs[name], stack | {name})
            here = 1 if node.get("type") == "object" else 0
            children = [walk(v, stack) for k, v in node.items() if k != "$defs"]
            return here + max(children, default=0)
        if isinstance(node, list):
            return max((walk(item, stack) for item in node), default=0)
        return 0
    return walk(schema, frozenset())


class DictVerdict(BaseModel):
    criteria: dict[str, bool]


def test_checker_catches_the_trap():
    assert dict_fields(DictVerdict.model_json_schema())
    assert emptied_objects(transform_schema(DictVerdict))
    assert DictVerdict.model_validate({"criteria": {}})  # pydantic happily accepts the empty verdict


@pytest.mark.parametrize("schema", MODEL_FACING, ids=lambda s: s.__name__)
def test_no_dicts_no_recursion_shallow(schema):
    raw = schema.model_json_schema()
    assert dict_fields(raw) == []
    assert depth(raw) <= 4


@pytest.mark.parametrize("schema", MODEL_FACING, ids=lambda s: s.__name__)
def test_anthropic_transform_keeps_every_field(schema):
    transformed = transform_schema(schema)
    assert emptied_objects(transformed) == []
    assert all(o.get("additionalProperties") is False for o in objects(transformed))


@pytest.mark.parametrize("schema", MODEL_FACING, ids=lambda s: s.__name__)
def test_openai_strict_schema_is_closed(schema):
    strict = to_strict_json_schema(schema)
    assert all(o.get("additionalProperties") is False for o in objects(strict))
    assert all(set(o.get("required", [])) == set(o.get("properties", {})) for o in objects(strict))


def test_verdict_keeps_all_named_checks():
    for transformed in (transform_schema(Verdict), to_strict_json_schema(Verdict)):
        assert set(GATES) | set(JUDGMENT) <= set(transformed["properties"])


def test_empty_verdict_is_rejected():
    with pytest.raises(ValidationError):
        Verdict.model_validate({})
    with pytest.raises(ValidationError):
        Verdict.model_validate_json('{"candidate_id": "c1"}')


# Measured 2026-09-29 on claude-opus-5-5: ModelMeaning's schema with 45 properties (every object's, $defs included)
# is refused with "The compiled grammar is too large"; any 44 of them compile. Stripping descriptions and titles
# doesn't help, and nesting two fields in a new object doesn't either. A new field needs one out, or the call split.
MEANING_PROPERTY_LIMIT = 44


def test_the_meaning_schema_stays_within_the_measured_grammar_limit():
    schema = transform_schema(ModelMeaning)
    properties = len(schema["properties"]) + sum(len(d.get("properties", {})) for d in schema["$defs"].values())
    assert properties <= MEANING_PROPERTY_LIMIT
