"""Typed accessors for configuration values."""
from __future__ import annotations
from ..types.json import JsonObject, JsonValue, json_object


def text(value: JsonValue, label: str = 'value') -> str:
    match value:
        case str():
            return value
    raise ValueError(f'{label} must be a string')


def array(value: JsonValue, label: str = 'value') -> list[JsonValue]:
    match value:
        case list():
            return value
    raise ValueError(f'{label} must be an array')


def strings(value: JsonValue, label: str = 'value') -> list[str]:
    items = array(value, label)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f'{label} must be a string')
    return [str(item) for item in items]


def integer(value: JsonValue, label: str = 'value') -> int:
    if type(value) is int:
        return value
    raise ValueError(f'{label} must be an integer')


def record(value: JsonValue) -> JsonObject:
    return json_object(value, 'JSON value')
