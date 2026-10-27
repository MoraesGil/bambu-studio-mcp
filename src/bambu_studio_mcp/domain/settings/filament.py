"""Bambu material routing through native extruder and nozzle-volume axes."""
from __future__ import annotations

import re
from typing import TypedDict

from ..types.json import JsonObject, JsonValue, decode_json
from .normalization import _redact, _VARIANT_PATTERN, _VECTORS, validate_changes
from .values import array, record, strings, text

VOLUME_TYPES = {0: 'Standard', 1: 'High Flow', 3: 'TPU High Flow', 5: 'E3D High Flow'}
_MODE_ALIASES = {'standard': 'Standard', 'high_flow': 'High Flow'}


class FilamentLayout(TypedDict):
    count: int
    total: int
    indices: list[int]
    variants: list[str]
    extruder_types: list[str]
    volume_types: list[str]


def _public(config: JsonObject) -> JsonObject:
    sanitized = record(_redact(config))
    sensitive = re.compile(r'password|api_?key|access_token|printhost', re.IGNORECASE)
    for key in sanitized:
        if sensitive.search(key):
            sanitized[key] = '[redacted]'
    return sanitized


def _mode_name(mode: str) -> str:
    candidate = _MODE_ALIASES.get(mode, mode)
    if candidate in set(VOLUME_TYPES.values()):
        return candidate
    raise ValueError('flow_mode must identify a supported native nozzle volume type')


def _integers(value: JsonValue, key: str, count: int | None = None) -> list[int]:
    if not isinstance(value, list) or len(value) == 0 or (count is not None and count != len(value)):
        raise ValueError(f'{key} requires a native 3MF string-array with valid cardinality')
    numbers: list[int] = []
    for item in value:
        if not isinstance(item, str) or re.fullmatch('[0-9]+', item) is None:
            raise ValueError(f'{key} requires a native 3MF string-array with valid cardinality')
        numbers.append(int(item))
    return numbers


def _filament_layout(config: JsonObject) -> FilamentLayout:
    materials = config.get('filament_settings_id')
    if not isinstance(materials, list) or not materials:
        raise ValueError('Project must identify its existing material slots')
    count = len(materials)
    if config.get('filament_extruder_variant') is None and config.get('filament_self_index') is None:
        indices = list(range(1, count + 1))
        variants = ['Direct Drive Standard' for _ in materials]
    else:
        declarations = config.get('filament_extruder_variant')
        if not isinstance(declarations, list) or not declarations or any(not isinstance(v, str) for v in declarations):
            raise ValueError('filament_extruder_variant requires native variant declarations')
        variants = strings(declarations)
        indices = _integers(config.get('filament_self_index'), 'filament_self_index', len(variants))
    associations: set[tuple[int, str]] = set()
    for identity, variant in zip(indices, variants):
        if identity not in range(1, count + 1) or (identity, variant) in associations:
            raise ValueError('Filament variants have ambiguous material indices')
        associations.add((identity, variant))
        if _VARIANT_PATTERN.fullmatch(variant) is None:
            raise ValueError('Unknown filament extruder variant')
    if {item[0] for item in associations} != set(range(1, count + 1)):
        raise ValueError('Filament variants have ambiguous material indices')
    extruders = config.get('extruder_type', ['Direct Drive'])
    if not isinstance(extruders, list) or not extruders or any(item not in ('Direct Drive', 'Bowden') for item in extruders):
        raise ValueError('extruder_type requires native extruder declarations')
    mapping = _integers(config.get('filament_map', ['1'] * count), 'filament_map', count)
    if any(index not in range(1, len(extruders) + 1) for index in mapping):
        raise ValueError('filament_map must reference an existing extruder')
    volumes = _integers(config.get('filament_volume_map', ['0'] * count), 'filament_volume_map', count)
    if set(volumes) - VOLUME_TYPES.keys():
        raise ValueError('filament_volume_map contains unsupported nozzle volume types')
    return FilamentLayout(count=count, total=len(variants), indices=indices, variants=variants,
                          extruder_types=[text(extruders[index - 1]) for index in mapping],
                          volume_types=[VOLUME_TYPES[index] for index in volumes])


def _flow_index(layout: FilamentLayout, slot: int, mode: str) -> int:
    wanted = f"{layout['extruder_types'][slot]} {_mode_name(mode)}"
    matches = [index for index, identity in enumerate(layout['indices'])
               if identity == slot + 1 and layout['variants'][index] == wanted]
    if matches:
        return matches[0]
    raise ValueError('Project has no matching filament extruder variant for the requested slot')


def _profile_index(config: JsonObject, extruder_type: str, mode: str) -> int:
    wanted = f'{extruder_type} {_mode_name(mode)}'
    for index, variant in enumerate(strings(config.get('filament_extruder_variant', ['Direct Drive Standard']))):
        if wanted == variant:
            return index
    return 0  # Native get_at/get_filament_config_idx fallback within one profile.


def _project_vector(config: JsonObject, key: str, length: int) -> list[JsonValue]:
    candidate = config.get(key)
    if isinstance(candidate, list) and len(candidate) == length:
        return candidate.copy()
    raise ValueError(f'Project vector {key} must preserve its native cardinality {length}')


def _native_default_vector(key: str, length: int, rec: JsonObject,
                           schema_status: JsonObject, schema: JsonObject) -> list[JsonValue]:
    if rec.get('nullable'):
        raise ValueError(f'{key}: missing nullable vector has no safe native default')
    commit = schema_status.get('source_commit')
    if not commit or schema_status.get('engine_commit') != commit:
        raise ValueError(f'{key}: native default requires matching schema and engine commits')
    if rec.get('type') not in _VECTORS:
        raise ValueError(f'{key}: native default requires a vector setting')
    literal = rec.get('default')
    if not isinstance(literal, str):
        raise ValueError(f'{key}: native default is not a numeric or JSON literal')
    try:
        primitive = decode_json(literal)
    except ValueError as error:
        if literal.strip() in ('NaN', 'Infinity', '-Infinity'):
            raise ValueError(f'{key}: expected a finite number') from error
        if re.fullmatch(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', literal) is None:
            raise ValueError(f'{key}: native default is not a numeric or JSON literal') from error
        primitive = float(literal)
    if primitive is None or isinstance(primitive, (list, dict)):
        raise ValueError(f'{key}: native default must be a non-null JSON primitive')
    normalized = array(validate_changes({key: [primitive]}, {}, schema)[key])
    return [normalized[0] for _ in range(length)]


def _validate_flow_vectors(config: JsonObject, schema: dict[str, JsonObject], layout: FilamentLayout) -> None:
    keys = set(config).intersection(schema)
    for key in keys:
        if schema[key].get('variant_domain') == 'filament':
            _project_vector(config, key, layout['total'])
