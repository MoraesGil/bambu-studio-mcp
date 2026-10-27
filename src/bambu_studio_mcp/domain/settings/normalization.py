# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Bambu source-schema normalization, independent of filesystem and runtime."""
from __future__ import annotations

import copy
import math
import re
from decimal import Decimal, InvalidOperation

from ..types.json import JsonObject, JsonValue, json_object, is_json_value
from .values import array, record, strings, text

_TYPES = {'print': 'print', 'process': 'print', 'printer': 'printer', 'machine': 'printer', 'filament': 'filament'}
_NATIVE_TYPES = {'print': 'process', 'printer': 'machine', 'filament': 'filament'}
_IDS = {'print': 'print_settings_id', 'printer': 'printer_settings_id', 'filament': 'filament_settings_id'}
_METADATA = frozenset({'name', 'type', 'from', 'version', 'inherits', 'instantiation', 'description',
                       'setting_id', 'filament_id', 'renamed_from', 'url', 'is_custom_defined'})
_TRANSPORT = frozenset({'printer_technology', 'bbl_use_printhost', 'host_type', 'printer_agent', 'print_host',
                        'print_host_webui', 'flashforge_serial_number', 'printer_model', 'filename_format'})
_CREDENTIALS = frozenset({'printhost_apikey', 'printhost_user', 'printhost_password'})
_IDENTITY = frozenset({'printer_model', 'printer_technology'})
_URL_KEYS = frozenset({'print_host', 'print_host_webui'})
_REDACTED = '<redacted>'
_SCALAR_TYPES = {'coFloats': 'coFloat', 'coInts': 'coInt', 'coBools': 'coBool', 'coStrings': 'coString',
                 'coPercents': 'coPercent', 'coFloatsOrPercents': 'coFloatOrPercent', 'coEnums': 'coEnum',
                 'coPoints': 'coPoint', 'coPoints3': 'coPoint3', 'coPointsGroups': 'coPointsGroup'}
_VECTORS = frozenset(_SCALAR_TYPES)
_VARIANT_PATTERN = re.compile(r'(Direct Drive|Bowden) (Standard|High Flow|TPU High Flow|E3D High Flow)')


def _type(value: str) -> str:
    if isinstance(value, str) and value in _TYPES:
        return _TYPES[value]
    raise ValueError('Preset type must be print/process, filament, or printer/machine')


def _transport(key: str) -> bool:
    return key.startswith('printhost_') or key in _TRANSPORT


def _gcode(key: str) -> bool:
    return key != 'emit_machine_limits_to_gcode' and key.endswith('_gcode')


def _placeholder(value: JsonValue) -> bool:
    todo = [value]
    while todo:
        node = todo.pop()
        if isinstance(node, str) and _REDACTED in node:
            return True
        if isinstance(node, list):
            todo.extend(node)
        elif isinstance(node, dict):
            todo.extend(node.values())
    return False


def _redact(value: JsonValue, key: str = '') -> JsonValue:
    if key in _CREDENTIALS and value not in ('', None, []):
        return _REDACTED
    match value:
        case dict():
            return {field: _redact(child, field) for field, child in value.items()}
        case list():
            return [_redact(child, key) for child in value]
        case str() if key in _URL_KEYS and '@' in value:
            prefix, _, host = value.rpartition('@')
            protocol = re.match(r'[A-Za-z][A-Za-z0-9+.-]*://', prefix)
            return (protocol[0] if protocol else '') + _REDACTED + '@' + host
    return value


def _number(value: JsonValue, key: str) -> Decimal:
    if type(value) not in (str, int, float):
        raise ValueError(f'{key}: expected a finite number')
    try:
        parsed = Decimal(str(value).strip())
        finite = parsed.is_finite() and math.isfinite(float(parsed))
    except (InvalidOperation, OverflowError, ValueError) as error:
        raise ValueError(f'{key}: expected a finite number') from error
    if not finite:
        raise ValueError(f'{key}: expected a finite number')
    return parsed


def _numeric_string(value: JsonValue, definition: JsonObject, key: str, kind: str) -> str:
    suffix = isinstance(value, str) and value.strip().endswith('%')
    if suffix and kind not in ('coPercent', 'coFloatOrPercent'):
        raise ValueError(f'{key}: percent is invalid for this type')
    number = _number(text(value).strip()[:-1] if suffix else value, key)
    if kind == 'coInt':
        if number < -(2 ** 31) or number >= 2 ** 31:
            raise ValueError(f'{key}: outside native integer range')
        if number % 1:
            raise ValueError(f'{key}: expected an integer')
    for bound, lower in (('min', True), ('max', False)):
        limit = definition.get(bound)
        if type(limit) not in (int, float):
            continue
        limit_number = _number(limit, key)
        if (lower and number < limit_number) or (not lower and number > limit_number):
            raise ValueError(f'{key}: outside native {bound} {limit}')
    rendered = format(number, 'f')
    integer_part, separator, fractional = rendered.partition('.')
    output = integer_part + ('.' + fractional.rstrip('0') if separator and fractional.rstrip('0') else '')
    return output + ('%' if suffix or kind == 'coPercent' else '')


def _scalar(value: JsonValue, rec: JsonObject, key: str) -> str:
    declared = text(rec['type'])
    kind = _SCALAR_TYPES.get(declared, declared)
    if rec.get('nullable') and value == 'nil':
        return 'nil'
    if kind in ('coFloat', 'coInt', 'coPercent', 'coFloatOrPercent'):
        return _numeric_string(value, rec, key, kind)
    if kind == 'coBool':
        normalized = str(value).lower() if isinstance(value, (str, int)) else ''
        if normalized in ('true', '1'):
            return '1'
        if normalized in ('false', '0'):
            return '0'
        raise ValueError(f'{key}: expected boolean or 0/1')
    if kind in ('coString', 'coEnum'):
        string = text(value, key)
        permitted = rec.get('enum_values')
        if kind == 'coEnum' and permitted and string not in array(permitted):
            raise ValueError(f'{key}: not one of the native enum values')
        return string
    if kind in ('coPoint', 'coPoint3'):
        size = 2 if kind == 'coPoint' else 3
        coordinates = re.split('[x,]', value) if isinstance(value, str) else value
        if not isinstance(coordinates, list) or len(coordinates) != size:
            raise ValueError(f'{key}: expected {size} coordinates')
        return ('x' if size == 2 else ',').join(format(_number(item, key), 'f') for item in coordinates)
    if kind == 'coPointsGroup':
        points = value.split(',') if isinstance(value, str) else value
        if not isinstance(points, list) or len(points) == 0:
            raise ValueError(f'{key}: expected a nonempty point group')
        return ','.join(_scalar(point, {'type': 'coPoint'}, key) for point in points)
    raise ValueError(f'{key}: unsupported native setting type {kind}')


def _input_changes(value: object, schema: JsonObject) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError('Changes must be a JSON object')
    converted: dict[str, object] = {}
    for key, supplied in value.items():
        if not isinstance(key, str):
            raise ValueError(f'Unknown native setting: {key}')
        definition = schema.get(key)
        kind = definition.get('type') if isinstance(definition, dict) else None
        if kind in ('coPoint', 'coPoint3') and isinstance(supplied, tuple):
            converted[key] = list(supplied)
        elif kind in ('coPoints', 'coPoints3') and isinstance(supplied, list):
            converted[key] = [list(point) if isinstance(point, tuple) else point for point in supplied]
        else:
            converted[key] = supplied
    if not is_json_value(converted):
        raise ValueError('Changes must contain finite JSON values')
    return json_object(converted, 'Changes')


def validate_changes(changes: object, current: object, schema: JsonObject,
                     allow_custom_gcode: bool = False) -> JsonObject:
    if not isinstance(changes, dict) or not isinstance(current, dict):
        raise ValueError('Changes and current configuration must be objects')
    incoming = _input_changes(changes, schema)
    existing = json_object(current, 'Current configuration')
    output: JsonObject = {}
    for key in incoming:
        if key.startswith('_') or key not in schema:
            raise ValueError(f'Unknown native setting: {key}')
        if key == 'post_process' or _transport(key):
            raise ValueError(f'{key}: writing scripts, transport, secrets, or printer identity is prohibited')
        if _gcode(key) and not allow_custom_gcode:
            raise ValueError(f'{key}: custom G-code requires allow_custom_gcode=True')
        supplied = incoming[key]
        if _placeholder(supplied):
            raise ValueError(f'{key}: a redacted placeholder cannot be written')
        definition = record(schema[key])
        if definition['type'] not in _VECTORS:
            output[key] = _scalar(supplied, definition, key)
            continue
        if not isinstance(supplied, list):
            raise ValueError(f'{key}: a vector requires an explicit array; slots are never broadcast')
        previous = existing.get(key)
        if isinstance(previous, list) and previous and len(supplied) != len(previous):
            raise ValueError(f'{key}: array length/cardinality must preserve existing slots')
        output[key] = [_scalar(item, definition, key) for item in supplied]
    return output


def normalize_filament_profile(config: JsonObject, schema: JsonObject) -> JsonObject:
    variants = config.get('filament_extruder_variant', ['Direct Drive Standard'])
    if (not isinstance(variants, list) or not variants
            or any(not isinstance(item, str) or not _VARIANT_PATTERN.fullmatch(item) for item in variants)
            or len(set(strings(variants))) != len(variants)):
        raise ValueError('Filament preset extruder variants are ambiguous')
    normalized = copy.deepcopy(config)
    metadata = record(schema.get('_meta', {}))
    groups = record(metadata.get('variant_keys', {}))
    for key in strings(groups.get('filament', [])):
        if key not in config:
            continue
        supplied = config[key]
        values = supplied if isinstance(supplied, list) else supplied.split(',') if isinstance(supplied, str) else [supplied]
        if len(values) not in range(1, len(variants) + 1):
            raise ValueError(f'Filament preset vector {key} has ambiguous variant cardinality')
        normalized[key] = [copy.deepcopy(values[index] if index < len(values) else values[0]) for index in range(len(variants))]
    return normalized
