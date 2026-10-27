"""Recursive JSON contracts and strict conversion at Python/decoder boundaries."""
from __future__ import annotations

import json
import math
from typing import TypeAlias, TypeGuard, cast
from typing_extensions import TypeAliasType

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue = TypeAliasType('JsonValue', 'str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]')
JsonObject: TypeAlias = dict[str, JsonValue]


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Validate iteratively, rejecting cycles without rejecting shared subtrees."""
    active: set[int] = set()
    pending: list[tuple[object, bool]] = [(value, False)]
    while pending:
        item, leaving = pending.pop()
        if leaving:
            active.remove(id(item))
            continue
        if item is None or isinstance(item, (str, bool, int)):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                return False
            continue
        if not isinstance(item, (list, dict)) or id(item) in active:
            return False
        active.add(id(item))
        pending.append((item, True))
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                return False
            pending.extend((child, False) for child in item.values())
        else:
            pending.extend((child, False) for child in item)
    return True


def _invalid_constant(value: str) -> None:
    raise ValueError(f'Non-finite JSON number: {value}')


def decode_json(data: str | bytes) -> JsonValue:
    parsed: object = json.loads(data, parse_constant=_invalid_constant)
    if not is_json_value(parsed):
        raise ValueError('JSON value must contain finite numbers and JSON primitives')
    return parsed


def json_object(value: object, label: str = 'JSON value') -> JsonObject:
    if isinstance(value, dict) and is_json_value(value):
        return cast(JsonObject, value)
    raise ValueError(f'{label} must be a JSON object')


def decode_object(data: str | bytes, label: str = 'JSON value') -> JsonObject:
    return json_object(decode_json(data), label)
