"""Pure material-slot and native-flow configuration rules."""
from __future__ import annotations
import json
import re
from typing import TypedDict
from ..types.json import JsonObject, JsonValue
from .values import record, text, array, strings
from .normalization import _redact, _VECTORS, validate_changes

# Bambu PrintConfig.hpp NozzleVolumeType values; 4 is intentionally unused.
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
    return {k: ('[redacted]' if re.search(r'password|api_?key|access_token|printhost', k, re.I) else v)
            for k, v in record(_redact(config)).items()}


def _mode_name(mode: str) -> str:
    name = _MODE_ALIASES.get(mode, mode)
    if name not in VOLUME_TYPES.values():
        raise ValueError('flow_mode must identify a supported native nozzle volume type')
    return name


def _integers(value: JsonValue, key: str, count: int | None = None) -> list[int]:
    if (not isinstance(value, list) or not value or (count is not None and len(value) != count)
            or any(not isinstance(v, str) or not re.fullmatch(r'\d+', v) for v in value)):
        raise ValueError(f'{key} requires a native 3MF string-array with valid cardinality')
    return [int(text(v)) for v in value]


def _filament_layout(config: JsonObject) -> FilamentLayout:
    """Read Bambu get_filament_config_idx inputs without changing stored arrays."""
    ids = config.get('filament_settings_id', [])
    if not isinstance(ids, list) or not ids:
        raise ValueError('Project must identify its existing material slots')
    count = len(ids)
    raw_variants, raw_indices = config.get('filament_extruder_variant'), config.get('filament_self_index')
    if raw_variants is None and raw_indices is None:
        variants, indices = ['Direct Drive Standard'] * count, list(range(1, count + 1))
    else:
        if not isinstance(raw_variants, list) or not raw_variants or any(not isinstance(v, str) for v in raw_variants):
            raise ValueError('filament_extruder_variant requires native variant declarations')
        variants = strings(raw_variants)
        indices = _integers(raw_indices, 'filament_self_index', len(variants))
    if (any(i < 1 or i > count for i in indices) or set(indices) != set(range(1, count + 1))
            or len(set(zip(indices, variants))) != len(variants)):
        raise ValueError('Filament variants have ambiguous material indices')
    for variant in variants:
        if not re.fullmatch(r'(Direct Drive|Bowden) (Standard|High Flow|TPU High Flow|E3D High Flow)', variant):
            raise ValueError('Unknown filament extruder variant')
    maps = _integers(config.get('filament_map', ['1'] * count), 'filament_map', count)
    extruders = config.get('extruder_type', ['Direct Drive'])
    if not isinstance(extruders, list) or not extruders or any(v not in ('Direct Drive', 'Bowden') for v in extruders):
        raise ValueError('extruder_type requires native extruder declarations')
    if any(i < 1 or i > len(extruders) for i in maps):
        raise ValueError('filament_map must reference an existing extruder')
    volumes = _integers(config.get('filament_volume_map', ['0'] * count), 'filament_volume_map', count)
    if any(v not in VOLUME_TYPES for v in volumes):
        raise ValueError('filament_volume_map contains unsupported nozzle volume types')
    return {'count': count, 'total': len(variants), 'indices': indices, 'variants': variants,
            'extruder_types': [text(extruders[i - 1]) for i in maps],
            'volume_types': [VOLUME_TYPES[v] for v in volumes]}


def _flow_index(layout: FilamentLayout, slot: int, mode: str) -> int:
    variant = layout['extruder_types'][slot] + ' ' + _mode_name(mode)
    for index, (identity, declaration) in enumerate(zip(layout['indices'], layout['variants'])):
        if identity == slot + 1 and declaration == variant:
            return index
    # Native falls back to global index zero. An editor must not overwrite an
    # unrelated material as a consequence of that runtime fallback.
    raise ValueError('Project has no matching filament extruder variant for the requested slot')


def _profile_index(config: JsonObject, extruder_type: str, mode: str) -> int:
    variants = strings(config.get('filament_extruder_variant', ['Direct Drive Standard']))
    wanted = extruder_type + ' ' + _mode_name(mode)
    return variants.index(wanted) if wanted in variants else 0


def _project_vector(config: JsonObject, key: str, length: int) -> list[JsonValue]:
    value = config.get(key)
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f'Project vector {key} must preserve its native cardinality {length}')
    return list(value)


def _native_default_vector(key: str, length: int, rec: JsonObject, schema_status: JsonObject, schema: JsonObject) -> list[JsonValue]:
    """Expand only an unambiguous literal from this bound native source."""
    if rec.get('nullable'):
        raise ValueError(f'{key}: missing nullable vector has no safe native default')
    commit = schema_status.get('source_commit')
    if not commit or commit != schema_status.get('engine_commit'):
        raise ValueError(f'{key}: native default requires matching schema and engine commits')
    if rec.get('type') not in _VECTORS:
        raise ValueError(f'{key}: native default requires a vector setting')
    try:
        value = json.loads(text(rec.get('default')))
    except (TypeError, ValueError) as error:
        literal = text(rec.get('default')) if isinstance(rec.get('default'), str) else ''
        if not re.fullmatch(r'[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?', literal):
            raise ValueError(f'{key}: native default is not a numeric or JSON literal') from error
        value = float(literal)
    if type(value) not in (str, int, float, bool):
        raise ValueError(f'{key}: native default must be a non-null JSON primitive')
    # This also rejects non-finite JSON extensions, wrong types and native ranges.
    return array(validate_changes({key: [value]}, {}, schema)[key]) * length


def _validate_flow_vectors(config: JsonObject, schema: dict[str, JsonObject], layout: FilamentLayout) -> None:
    # These are the source-defined variant keys, not a heuristic on list length.
    for key, rec in schema.items():
        if rec.get('variant_domain') == 'filament' and key in config:
            _project_vector(config, key, layout['total'])
